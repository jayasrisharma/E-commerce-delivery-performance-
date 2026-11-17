"""Validate Olist source tables and prepare order-grain analytical data."""

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
FILES = {
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "translation": "product_category_name_translation.csv",
}
ORDER_DATES = {
    "order_purchase_timestamp": "purchased_at",
    "order_approved_at": "approved_at",
    "order_delivered_carrier_date": "handed_to_carrier_at",
    "order_delivered_customer_date": "delivered_at",
    "order_estimated_delivery_date": "estimated_at",
}


def check_key(table, columns, label):
    if table[columns].isna().any().any() or table.duplicated(columns).any():
        raise ValueError(f"Missing or duplicate key in {label}: {columns}")


def check_reference(child, parent, column, label):
    failures = int((~child[column].isin(parent[column])).sum())
    if failures:
        raise ValueError(f"{failures} unresolved {column} values in {label}")


def parse_date(values, audit, name):
    parsed = pd.to_datetime(values, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    audit[name] = int((values.notna() & parsed.isna()).sum())
    return parsed


def prepare_data(raw_dir=RAW):
    """Return the order fact, two distinct order-to-segment tables, and diagnostics."""
    raw_dir = Path(raw_dir)
    tables = {}
    audit = {"sources": {}, "unparseable_timestamps": {}}
    for name, filename in FILES.items():
        path = raw_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing {path}. See data/README.md for setup.")
        raw = pd.read_csv(path)
        clean = raw.drop_duplicates()
        tables[name] = clean
        audit["sources"][filename] = {
            "rows": len(raw),
            "columns": len(raw.columns),
            "exact_duplicates_removed": len(raw) - len(clean),
            "missing_by_column": {c: int(n) for c, n in raw.isna().sum().items() if n},
        }

    orders = tables["orders"].rename(columns=ORDER_DATES)
    items = tables["items"]
    customers = tables["customers"]
    sellers = tables["sellers"]
    products = tables["products"]
    translation = tables["translation"]
    reviews = tables["reviews"].copy()

    for name, key in {
        "orders": ["order_id"],
        "items": ["order_id", "order_item_id"],
        "customers": ["customer_id"],
        "sellers": ["seller_id"],
        "products": ["product_id"],
        "translation": ["product_category_name"],
    }.items():
        check_key(tables[name], key, name)

    for child, parent, column in [
        ("orders", "customers", "customer_id"),
        ("items", "orders", "order_id"),
        ("items", "products", "product_id"),
        ("items", "sellers", "seller_id"),
        ("reviews", "orders", "order_id"),
    ]:
        check_reference(tables[child], tables[parent], column, child)

    for column in ORDER_DATES.values():
        orders[column] = parse_date(orders[column], audit["unparseable_timestamps"], column)
    for column in ["review_creation_date", "review_answer_timestamp"]:
        reviews[column] = parse_date(reviews[column], audit["unparseable_timestamps"], column)

    review_scores = pd.to_numeric(reviews["review_score"], errors="coerce")
    valid_reviews = review_scores.isin([1, 2, 3, 4, 5])
    reviews["review_score"] = review_scores
    # Retain the latest valid answered review per order, with stable tie-breakers.
    reviews = reviews.loc[valid_reviews].sort_values(
        ["review_answer_timestamp", "review_creation_date", "review_id", "review_score"],
        na_position="first",
    )
    one_review = reviews.drop_duplicates("order_id", keep="last")[["order_id", "review_score"]]

    facts = orders.merge(customers[["customer_id", "customer_state"]],
                         on="customer_id", validate="many_to_one")
    facts = facts.merge(one_review, on="order_id", how="left", validate="one_to_one")
    if len(facts) != len(orders):
        raise ValueError("The order fact must retain every order exactly once.")

    delivered = facts["order_status"].eq("delivered")
    backwards = (
        facts["delivered_at"].lt(facts["purchased_at"])
        | facts["estimated_at"].lt(facts["purchased_at"])
    )
    missing_dates = facts[["purchased_at", "delivered_at", "estimated_at"]].isna().any(axis=1)
    valid = delivered & ~missing_dates & ~backwards
    facts["delivery_eligible"] = valid.astype(int)
    facts["delivery_days"] = (
        (facts["delivered_at"] - facts["purchased_at"]).dt.total_seconds() / 86400
    ).where(valid)
    facts["delay_days"] = (
        (facts["delivered_at"] - facts["estimated_at"]).dt.total_seconds() / 86400
    ).where(valid)
    facts["calendar_late"] = facts["delivered_at"].dt.normalize().gt(
        facts["estimated_at"].dt.normalize()
    ).where(valid)
    facts["purchase_year"] = facts["purchased_at"].dt.year
    valid_handoff = (
        facts["approved_at"].ge(facts["purchased_at"])
        & facts["handed_to_carrier_at"].ge(facts["approved_at"])
        & facts["handed_to_carrier_at"].le(facts["delivered_at"])
    )
    facts["handoff_days"] = (
        (facts["handed_to_carrier_at"] - facts["approved_at"]).dt.total_seconds() / 86400
    ).where(valid & valid_handoff)

    product_category = products[["product_id", "product_category_name"]].merge(
        translation, on="product_category_name", how="left", validate="many_to_one"
    )
    product_category["category"] = (
        product_category["product_category_name_english"]
        .fillna(product_category["product_category_name"])
        .fillna("unknown")
    )
    seller_order = items[["order_id", "seller_id"]].drop_duplicates()
    category_order = (
        items[["order_id", "product_id"]]
        .merge(product_category[["product_id", "category"]], on="product_id", validate="many_to_one")
        [["order_id", "category"]]
        .drop_duplicates()
    )

    audit.update({
        "foreign_keys_validated": True,
        "purchase_start": str(facts["purchased_at"].min().date()),
        "purchase_end": str(facts["purchased_at"].max().date()),
        "status_counts": {k: int(v) for k, v in facts["order_status"].value_counts().items()},
        "delivered_missing_required_dates": int((delivered & missing_dates).sum()),
        "delivered_invalid_date_order": int((delivered & ~missing_dates & backwards).sum()),
        "eligible_missing_or_invalid_handoff": int((valid & ~valid_handoff).sum()),
        "review_rows_with_invalid_score": int((~valid_reviews).sum()),
        "additional_valid_review_rows_removed": len(reviews) - len(one_review),
        "orders_without_valid_review": int(facts["review_score"].isna().sum()),
        "orders_without_items": int((~facts["order_id"].isin(items["order_id"])).sum()),
        "eligible_orders_without_items": int((valid & ~facts["order_id"].isin(items["order_id"])).sum()),
        "multi_seller_orders": int(seller_order["order_id"].duplicated(keep=False)
                                   .groupby(seller_order["order_id"]).any().sum()),
        "multi_category_orders": int(category_order["order_id"].duplicated(keep=False)
                                     .groupby(category_order["order_id"]).any().sum()),
        "named_categories_without_translation": sorted(
            product_category.loc[
                product_category["product_category_name_english"].isna(),
                "product_category_name",
            ].dropna().unique().tolist()
        ),
    })
    return facts, seller_order, category_order, audit
