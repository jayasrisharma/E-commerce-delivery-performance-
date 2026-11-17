"""Generate the portfolio outputs and cross-check Pandas aggregates with SQL."""

import argparse
import json
import re
import sqlite3
from pathlib import Path
from textwrap import fill

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import pandas as pd

from data_processing import RAW, ROOT, prepare_data


MIN_SELLER_ORDERS = 100
MIN_SELLER_YEAR_ORDERS = 25
MIN_CATEGORY_ORDERS = 500
MIN_STATE_ORDERS = 500
DELAY_BINS = [-float("inf"), -14, -7, 0, 3, 7, 14, float("inf")]
DELAY_LABELS = ["≤−14", "−14 to −7", "−7 to 0", "0 to 3", "3 to 7", "7 to 14", ">14"]
INK, MUTED, TEAL, ORANGE = "#17304a", "#49647d", "#007d78", "#bd4e2e"


def calculate(facts, seller_order, category_order):
    eligible = facts.loc[facts["delivery_eligible"].eq(1)].copy()
    eligible["late"] = eligible["delay_days"].gt(0)
    eligible["timing"] = eligible["late"].map({False: "On time", True: "Late"})
    eligible["y2017"] = eligible["purchase_year"].eq(2017)
    eligible["y2018"] = eligible["purchase_year"].eq(2018)
    eligible["late_2017"] = eligible["late"] & eligible["y2017"]
    eligible["late_2018"] = eligible["late"] & eligible["y2018"]
    late = eligible.loc[eligible["late"]]
    reviewed = facts["review_score"].dropna()
    scored = eligible.dropna(subset=["review_score"])
    metrics = {
        "total_orders": len(facts),
        "delivered_orders": int(facts["order_status"].eq("delivered").sum()),
        "canceled_orders": int(facts["order_status"].eq("canceled").sum()),
        "unavailable_orders": int(facts["order_status"].eq("unavailable").sum()),
        "eligible_orders": len(eligible),
        "on_time_orders": len(eligible) - len(late),
        "late_orders": len(late),
        "on_time_pct": 100 * (1 - eligible["late"].mean()),
        "late_pct": 100 * eligible["late"].mean(),
        "mean_delivery_days": eligible["delivery_days"].mean(),
        "mean_delay_days": eligible["delay_days"].mean(),
        "mean_late_delay_days": late["delay_days"].mean(),
        "calendar_late_orders": int(eligible["calendar_late"].sum()),
        "calendar_late_pct": 100 * eligible["calendar_late"].mean(),
        "reviewed_orders": len(reviewed),
        "mean_review_score": reviewed.mean(),
        "low_review_pct": 100 * reviewed.le(2).mean(),
    }
    checks = {"kpi_summary": pd.DataFrame([metrics])}
    outputs = {
        "kpi_summary": pd.DataFrame(metrics.items(), columns=["metric", "value"]),
        "delay_distribution": (
            pd.cut(eligible["delay_days"], DELAY_BINS, labels=DELAY_LABELS)
            .value_counts(sort=False).rename_axis("delay_days_bucket").reset_index(name="orders")
            .assign(order_pct=lambda df: 100 * df["orders"] / len(eligible))
        ),
    }
    # Use the order fact after dropping missing scores so low-rating denominators are explicit.
    score_distribution = reviewed.value_counts().reindex(range(1, 6), fill_value=0).sort_index()
    outputs["review_score_distribution"] = score_distribution.rename_axis("review_score").reset_index(
        name="reviewed_orders"
    ).assign(review_pct=lambda df: 100 * df["reviewed_orders"] / len(reviewed))
    checks["review_score_distribution"] = outputs["review_score_distribution"].query("reviewed_orders > 0")

    def performance(table, group):
        result = table.groupby(group, observed=True).agg(
            orders=("order_id", "size"),
            late_orders=("late", "sum"),
            mean_delivery_days=("delivery_days", "mean"),
            mean_delay_days=("delay_days", "mean"),
            reviewed_orders=("review_score", "count"),
            mean_review_score=("review_score", "mean"),
        ).reset_index()
        return result.assign(late_pct=100 * result["late_orders"] / result["orders"])

    def review_by(table, group):
        result = table.assign(low_review=table["review_score"].le(2)).groupby(
            group, observed=True
        ).agg(
            reviewed_orders=("review_score", "size"),
            mean_review_score=("review_score", "mean"),
            low_review_orders=("low_review", "sum"),
        ).reset_index()
        return result.assign(low_review_pct=100 * result["low_review_orders"] / result["reviewed_orders"])

    outputs["review_by_timing"] = review_by(scored, "timing")
    checks["review_by_timing"] = outputs["review_by_timing"]
    severity = pd.cut(
        scored["delay_days"], [-float("inf"), 0, 3, 7, 14, float("inf")],
        labels=["On time", ">0–3 days late", ">3–7 days late", ">7–14 days late", ">14 days late"],
    )
    outputs["review_by_delay"] = review_by(scored.assign(timing=severity), "timing")

    sellers = seller_order.merge(eligible, on="order_id", validate="many_to_one")
    seller_years = sellers.groupby("seller_id")[["y2017", "late_2017", "y2018", "late_2018"]].sum()
    seller_years = seller_years.rename(columns={"y2017": "orders_2017", "y2018": "orders_2018"})
    seller_years["late_pct_2017"] = 100 * seller_years["late_2017"] / seller_years["orders_2017"]
    seller_years["late_pct_2018"] = 100 * seller_years["late_2018"] / seller_years["orders_2018"]
    seller_table = performance(sellers, "seller_id").merge(
        seller_years, on="seller_id", validate="one_to_one"
    )
    # Compare each seller with the corresponding marketplace period.
    marketplace_2017 = 100 * eligible.loc[eligible["y2017"], "late"].mean()
    marketplace_2018 = 100 * eligible.loc[eligible["y2018"], "late"].mean()
    seller_table["above_benchmark_both_years"] = (
        seller_table["orders_2017"].ge(MIN_SELLER_YEAR_ORDERS)
        & seller_table["orders_2018"].ge(MIN_SELLER_YEAR_ORDERS)
        & seller_table["late_pct_2017"].gt(marketplace_2017)
        & seller_table["late_pct_2018"].gt(marketplace_2018)
    )

    categories = category_order.merge(eligible, on="order_id", validate="many_to_one")
    states = performance(eligible, "customer_state")
    category_table = performance(categories, "category")
    for name, key, table, threshold in [
        ("seller_performance", "seller_id", seller_table, MIN_SELLER_ORDERS),
        ("category_performance", "category", category_table, MIN_CATEGORY_ORDERS),
        ("state_performance", "customer_state", states, MIN_STATE_ORDERS),
    ]:
        ranked = table.loc[table["orders"].ge(threshold)].sort_values(
            ["late_pct", "orders", key], ascending=[False, False, True]
        ).reset_index(drop=True)
        outputs[name] = checks[name] = ranked

    handoff = eligible.dropna(subset=["handoff_days"]).copy()
    handoff["handoff_group"] = handoff["handoff_days"].gt(3).map({
        False: "Within 3 days", True: "Over 3 days",
    })
    outputs["handoff_performance"] = performance(handoff, "handoff_group")
    checks["handoff_performance"] = outputs["handoff_performance"]
    diagnostics = {
        "pearson_delay_vs_review": scored["delay_days"].corr(scored["review_score"]),
        "eligible_orders_with_review": len(scored),
        "eligible_orders_with_handoff": len(handoff),
        "marketplace_late_pct_2017": marketplace_2017,
        "marketplace_late_pct_2018": marketplace_2018,
        "sellers_above_benchmark_both_years": int(outputs["seller_performance"][
            "above_benchmark_both_years"
        ].sum()),
    }
    if outputs["delay_distribution"]["orders"].sum() != len(eligible):
        raise ValueError("Delay buckets do not reconcile to eligible orders.")
    return metrics, outputs, checks, diagnostics


def verify_sql(facts, seller_order, category_order, expected):
    """Execute the committed SQL and compare every returned row with Pandas."""
    sql = (ROOT / "sql" / "analysis_queries.sql").read_text(encoding="utf-8")
    sections = re.split(r"^-- result: (\w+)\s*$", sql, flags=re.MULTILINE)
    queries = dict(zip(sections[1::2], sections[2::2]))
    if queries.keys() != expected.keys():
        raise ValueError(f"Unexpected SQL result names: {list(queries)}")
    with sqlite3.connect(":memory:") as connection:
        facts[[
            "order_id", "order_status", "delivery_eligible", "delivery_days", "delay_days",
            "calendar_late", "review_score", "customer_state", "purchase_year", "handoff_days",
        ]].to_sql("order_fact", connection, index=False)
        seller_order.to_sql("seller_order", connection, index=False)
        category_order.to_sql("category_order", connection, index=False)
        for name, query in queries.items():
            got = pd.read_sql_query(query, connection)
            wanted = expected[name]
            if set(wanted.columns) != set(got.columns):
                raise ValueError(f"Column mismatch for {name}: {list(got.columns)}")
            # SQL engines may represent integers as floats and flags as integers.
            wanted = wanted.astype({c: float for c in wanted.select_dtypes(include=["number", "bool"])})
            got = got.astype({c: float for c in wanted.select_dtypes(include="number")})
            got = got[wanted.columns]
            pd.testing.assert_frame_equal(got.reset_index(drop=True), wanted.reset_index(drop=True),
                                          check_dtype=False, atol=1e-9, rtol=1e-9, obj=name)
    return list(queries)


def render_dashboard(metrics, outputs, path):
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
        "axes.labelcolor": MUTED, "axes.edgecolor": "#cbd6df", "axes.titlecolor": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
        "axes.spines.right": False, "axes.spines.left": False,
    })
    fig, grid = plt.subplots(2, 3, figsize=(17.5, 10.7), facecolor="#ffffff")
    fig.subplots_adjust(left=.077, right=.963, top=.69, bottom=.13, hspace=.72, wspace=.45)
    fig.text(.049, .947, "Delivery reliability and customer experience", size=28, weight="bold")
    fig.text(.05, .906, "Olist marketplace · Brazil · Orders placed September 2016–October 2018",
             size=12, color=MUTED)
    for index, (title, value, color) in enumerate([
        ("Total orders", f"{metrics['total_orders']:,}", INK),
        ("Delivered orders", f"{metrics['delivered_orders']:,}", INK),
        ("On-time delivery rate", f"{metrics['on_time_pct']:.1f}%", TEAL),
        ("Late delivery rate", f"{metrics['late_pct']:.1f}%", ORANGE),
    ]):
        x = .05 + index * .235
        fig.text(x, .843, title, size=11, color=MUTED)
        fig.text(x, .798, value, size=26, weight="bold", color=color)
    fig.text(.05, .756,
             f"Timing rates use {metrics['eligible_orders']:,} delivered orders with valid timestamps.",
             size=10, color=MUTED)

    ax = grid[0, 0]
    delay = outputs["delay_distribution"]
    ax.bar(delay.index, delay["order_pct"], color=[TEAL] * 3 + [ORANGE] * 4, width=.7, zorder=3)
    ax.set_xticks(delay.index, delay["delay_days_bucket"], rotation=26, ha="right", fontsize=8)
    ax.yaxis.set_major_formatter(PercentFormatter())
    ax.set_xlabel("Actual minus estimated delivery (days)")
    ax.set_title("How far from the promise?", loc="left", weight="bold", pad=13)
    ax.grid(axis="y", alpha=.20, zorder=0)
    for i, value in enumerate(delay["order_pct"]):
        ax.annotate(f"{value:.1f}%", (i, value), xytext=(0, 4), textcoords="offset points",
                    ha="center", size=8)
    ax.set_ylim(0, delay["order_pct"].max() * 1.16)

    ax = grid[0, 1]
    review = outputs["review_by_timing"].set_index("timing").loc[["On time", "Late"]]
    ax.bar([0, 1], review["low_review_pct"], color=[TEAL, ORANGE], width=.49, zorder=3)
    ax.set_xticks([0, 1], [f"{k}\n{n:,} reviewed" for k, n in review["reviewed_orders"].items()])
    ax.set_title("How often do orders score 1–2 stars?", loc="left", weight="bold", pad=13)
    ax.yaxis.set_major_formatter(PercentFormatter())
    ax.grid(axis="y", alpha=.20, zorder=0)
    for i, (rate, score) in enumerate(zip(review["low_review_pct"], review["mean_review_score"])):
        ax.annotate(f"{rate:.1f}%\n{score:.2f}/5 mean", (i, rate), xytext=(0, 5),
                    textcoords="offset points", ha="center", size=9)
    ax.set_ylim(0, max(review["low_review_pct"]) * 1.29)

    def rate_bars(ax, table, labels, title, subtitle="", colors=ORANGE):
        rates = table["late_pct"].tolist()
        bars = ax.barh(list(range(len(table))), rates, color=colors, height=.63, zorder=3)
        ax.set_yticks(list(range(len(table))), labels, fontsize=9)
        ax.invert_yaxis()
        ax.xaxis.set_major_formatter(PercentFormatter())
        ax.grid(axis="x", alpha=.20, zorder=0)
        ax.axvline(metrics["late_pct"], color=MUTED, linestyle="--", linewidth=1)
        ax.set_xlim(0, max(max(rates), metrics["late_pct"]) * 1.44)
        ax.bar_label(bars, labels=[f"{r:.1f}% · {n:,}" for r, n in zip(rates, table["orders"])],
                     padding=5, fontsize=8, color=INK,
                     bbox={"facecolor": "white", "edgecolor": "none", "pad": 1})
        ax.set_title(title, loc="left", weight="bold", pad=22 if subtitle else 12)
        if subtitle:
            ax.text(0, 1.025, subtitle, transform=ax.transAxes, size=8.5, color=MUTED)

    categories = outputs["category_performance"].query("category != 'unknown'").head(5)
    rate_bars(grid[0, 2], categories,
              [fill(c.replace("_", " ").capitalize(), 19) for c in categories["category"]],
              "Which categories miss most often?", f"Top five by rate · ≥{MIN_CATEGORY_ORDERS} orders")

    sellers = outputs["seller_performance"].head(5)
    rate_bars(grid[1, 0], sellers, sellers["seller_id"].str[:8].tolist(),
              "Which sellers warrant review?", f"Top five by rate · ≥{MIN_SELLER_ORDERS} orders")

    states = outputs["state_performance"]
    states = pd.concat([states.head(3), states.tail(3)]).drop_duplicates("customer_state")
    rate_bars(grid[1, 1], states, states["customer_state"].tolist(),
              "How do late rates vary by state?", f"High and low rates · ≥{MIN_STATE_ORDERS} orders",
              colors=[ORANGE if r > metrics["late_pct"] else TEAL for r in states["late_pct"]])

    handoff = outputs["handoff_performance"].set_index("handoff_group").loc[
        ["Over 3 days", "Within 3 days"]
    ]
    rate_bars(grid[1, 2], handoff, handoff.index.tolist(), "Is slower handoff linked to late delivery?",
              "Approval to carrier handoff", [ORANGE, TEAL])
    fig.text(.05, .067,
             "Segment bars show late rate and eligible order count. Dashed line marks the marketplace late rate.",
             size=10, color=MUTED)
    fig.text(.05, .043,
             "Seller and category segments can overlap. Delay buckets include their right endpoint. "
             "Review scores are observed ratings.",
             size=10, color=MUTED)
    fig.savefig(path, dpi=145)
    plt.close(fig)


def run(raw_dir=RAW, output_dir=ROOT / "outputs"):
    facts, seller_order, category_order, audit = prepare_data(raw_dir)
    metrics, outputs, checks, diagnostics = calculate(facts, seller_order, category_order)
    audit.update(diagnostics)
    audit["sql_cross_checks_passed"] = verify_sql(facts, seller_order, category_order, checks)

    output_dir = Path(output_dir)
    (output_dir / "charts").mkdir(parents=True, exist_ok=True)
    for name, table in outputs.items():
        table.to_csv(output_dir / f"{name}.csv", index=False, float_format="%.8f")
    (output_dir / "data_quality.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    render_dashboard(metrics, outputs, output_dir / "charts" / "delivery_dashboard.png")
    return metrics, outputs, audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=RAW, help="Directory with seven Olist CSVs")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs", help="Output directory")
    args = parser.parse_args()
    metrics, _, audit = run(args.data, args.output)
    print(f"{metrics['total_orders']:,} orders | {metrics['eligible_orders']:,} eligible | "
          f"{metrics['late_pct']:.2f}% late")
    print(f"Verified {len(audit['sql_cross_checks_passed'])} SQL result sets. Outputs: {args.output}")
