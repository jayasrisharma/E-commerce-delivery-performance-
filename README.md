# Olist source data

This project uses the *Brazilian E-Commerce Public Dataset by Olist*, Kaggle version 2, updated 1 October 2021. The original CSVs are unchanged locally. The project produces derived analytical tables and charts. [Official dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) · [Kaggle metadata](https://www.kaggle.com/api/v1/datasets/view/olistbr/brazilian-ecommerce) · [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).

From the repository root, create `data/raw/`. Download the ZIP from Kaggle separately and extract the following seven files directly into that directory.

- `olist_orders_dataset.csv`
- `olist_order_items_dataset.csv`
- `olist_customers_dataset.csv`
- `olist_sellers_dataset.csv`
- `olist_products_dataset.csv`
- `olist_order_reviews_dataset.csv`
- `product_category_name_translation.csv`

The [official ZIP endpoint](https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce) was used for this run. Its SHA-256 was `967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784`. The ZIP also contains payments and geolocation tables. State analysis uses the customer table directly. Git ignores `data/raw/` so the source CSVs stay local.
