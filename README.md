# E-commerce Delivery Performance & Customer Experience Analysis

Which fulfilment problems should a marketplace investigate first to improve delivery reliability and customer experience? This independent case study analyzes **99,441 historical Olist orders** placed in Brazil from **4 September 2016 to 17 October 2018**.

Of 96,470 eligible deliveries, **8.11% missed the stored estimate**. Among reviewed eligible deliveries, **54.07% of late orders** scored one or two stars, compared with **9.22% of on-time orders**. The analysis also identifies a candidate carrier-handoff alert and seller, category, and destination-state cohorts for operational review.

| Start here | File |
|---|---|
| Decisions and supporting evidence | [One-page executive summary](report/executive_summary.md) |
| Visual overview | [Delivery performance dashboard](outputs/charts/delivery_dashboard.png) |
| Executed walkthrough | [Analysis notebook](notebooks/delivery_analysis.ipynb) |
| Reusable analytics | [Data preparation](src/data_processing.py), [Python runner](src/analysis.py), [SQL queries](sql/analysis_queries.sql) |
| Supporting data | [KPI summary](outputs/kpi_summary.csv), [results directory](outputs/), [data-quality audit](outputs/data_quality.json) |
| Application materials | [Resume bullet options](report/resume_bullets.md) |

## Reproduce the analysis

1. Download the official Olist ZIP separately. Create `data/raw/` in the repository root and extract the seven listed CSVs directly into it. Git ignores the raw directory. [Data setup and archive checksum](data/README.md).
2. From the repository root, create and activate a Python 3.11 virtual environment, then run these commands.

   ```sh
   python -m pip install -r requirements.txt
   python src/analysis.py
   python -m jupyter execute --inplace --timeout=180 notebooks/delivery_analysis.ipynb
   ```

The Python command alone regenerates the CSVs, data-quality JSON, and dashboard. The third command re-executes and saves the notebook. The runner also accepts `--data` and `--output` directory arguments. SQL runs in Python's built-in SQLite. A separate database service is optional.

## Methodology

| Measure | Definition |
|---|---|
| Order status | One row per `order_id`. Delivered, canceled and unavailable counts use the source status field. Other undelivered statuses remain in total order volume. |
| Timing cohort | Delivered status and valid purchase, actual delivery and estimated delivery timestamps. Delivery and estimate must be on or after purchase. Source timestamps are used as recorded. Eight delivered orders lacked a required timestamp. |
| Delivery duration and signed delay | Actual delivery minus purchase, and actual delivery minus estimate, respectively. Both use elapsed seconds divided by 86,400. Mean signed delay uses every eligible delivery. Mean late delay uses only positive delays. |
| On-time and late rates | Actual delivery at or before the stored estimate is on time. A positive delay is late. Both rates divide by the eligible delivery count. A calendar-date sensitivity treats arrival on the estimated date as on time. |
| Review metrics | Keep a valid 1–5 score per order from the latest answer, then break ties by review creation time, review ID and score, taking the largest in each case. Missing dates sort first. Overall review metrics use all scored orders. Timing comparisons and Pearson correlation use only scored eligible deliveries. Low-rated means 1–2 stars. |
| Segment performance | Count one eligible order per seller or category present, or per customer state. Orders with multiple sellers or categories can appear in several segments. Each segment uses the delivery outcome of the whole order. Rates divide by the segment's eligible orders. |
| Ranking rules | At least 100 eligible orders per seller and 500 per category or state. The repeat-seller flag also requires 25 orders in each observed purchase year (2017 and 2018) and a late rate above that year's marketplace benchmark in both. The 2018 window is partial. |
| Handoff comparison | Elapsed time between order approval and recorded carrier handoff. Include records with purchase ≤ approval ≤ handoff ≤ actual delivery. Compare `>3` and `≤3` elapsed days as an exploratory cutoff. |

The pipeline checks full-row duplicates, primary keys, foreign keys and join cardinalities. It drops duplicate order–segment pairs before joining to eligible orders and retains an `unknown` category when a product lacks a category. Untranslated category codes remain in Portuguese. The audit lists original missing fields, record counts, exclusions, status totals and validation results. [Data-quality audit](outputs/data_quality.json).

### SQL validation

The SQL consumes three prepared relations. `order_fact` carries status, eligibility, duration, signed delay, date-only late flag, review score, purchase year, destination state, and handoff duration. Its key is `order_id`. The other relations are `seller_order(order_id, seller_id)` and `category_order(order_id, category)`, each with a unique pair as its key. The runner executes all seven queries in memory and compares every aggregate row with Pandas before writing outputs. The queries were also executed against the same prepared data in PostgreSQL 18.6 via PGlite, with matching results. [Query file](sql/analysis_queries.sql).

## Scope and limitations

- The records describe Olist in Brazil during 2016–2018. Market conditions, fulfilment partners, and customer expectations may differ at Myntra or in India.
- Ratings are a proxy for customer experience. They cover multiple aspects of the order and are available for a subset of orders. Seller, category, destination, season and promise length can confound comparisons.
- All stored estimates are recorded at midnight. Using calendar dates lowers the measured late rate from 8.11% to 6.77%. Confirm the customer-facing promise convention before setting a production KPI.
- Carrier handoff and final delivery are recorded at order level. Seller or category segments cannot pinpoint the responsible party on multi-seller or multi-category orders. The handoff comparison also excludes 1,388 eligible orders without a usable handoff sequence.
- The analysis supports prioritizing investigations and designing pilots. Operational effects would require a prospective evaluation.

## Dataset attribution

Olist published the *Brazilian E-Commerce Public Dataset by Olist*. This project uses Kaggle version 2 and provides derived summaries from the source CSVs. The dataset is licensed under Creative Commons Attribution–NonCommercial–ShareAlike 4.0 International. [Official dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) · [License](https://creativecommons.org/licenses/by-nc-sa/4.0/).
