-- PostgreSQL-compatible SQL. The Python runner executes each statement in SQLite
-- using the validated tables described in the README.
-- delivery_eligible and calendar_late are integer flags. Durations are decimal days.

-- result: kpi_summary
WITH order_totals AS (
    SELECT COUNT(*) AS total_orders,
           COUNT(CASE WHEN order_status = 'delivered' THEN 1 END) AS delivered_orders,
           COUNT(CASE WHEN order_status = 'canceled' THEN 1 END) AS canceled_orders,
           COUNT(CASE WHEN order_status = 'unavailable' THEN 1 END) AS unavailable_orders,
           COUNT(review_score) AS reviewed_orders,
           AVG(1.0 * review_score) AS mean_review_score,
           100.0 * COUNT(CASE WHEN review_score <= 2 THEN 1 END)
                 / NULLIF(COUNT(review_score), 0) AS low_review_pct
    FROM order_fact
),
delivery_totals AS (
    SELECT COUNT(*) AS eligible_orders,
           COUNT(CASE WHEN delay_days <= 0 THEN 1 END) AS on_time_orders,
           COUNT(CASE WHEN delay_days > 0 THEN 1 END) AS late_orders,
           100.0 * COUNT(CASE WHEN delay_days <= 0 THEN 1 END)
                 / NULLIF(COUNT(*), 0) AS on_time_pct,
           100.0 * COUNT(CASE WHEN delay_days > 0 THEN 1 END)
                 / NULLIF(COUNT(*), 0) AS late_pct,
           AVG(delivery_days) AS mean_delivery_days,
           AVG(delay_days) AS mean_delay_days,
           AVG(CASE WHEN delay_days > 0 THEN delay_days END) AS mean_late_delay_days,
           COUNT(CASE WHEN calendar_late = 1 THEN 1 END) AS calendar_late_orders,
           100.0 * COUNT(CASE WHEN calendar_late = 1 THEN 1 END)
                 / NULLIF(COUNT(*), 0) AS calendar_late_pct
    FROM order_fact
    WHERE delivery_eligible = 1
)
SELECT *
FROM order_totals CROSS JOIN delivery_totals;

-- result: review_score_distribution
SELECT review_score,
       COUNT(*) AS reviewed_orders,
       100.0 * COUNT(*) / SUM(COUNT(*)) OVER () AS review_pct
FROM order_fact
WHERE review_score IS NOT NULL
GROUP BY review_score
ORDER BY review_score;

-- result: review_by_timing
WITH reviewed_deliveries AS (
    SELECT CASE WHEN delay_days > 0 THEN 'Late' ELSE 'On time' END AS timing,
           review_score
    FROM order_fact
    WHERE delivery_eligible = 1 AND review_score IS NOT NULL
)
SELECT timing,
       COUNT(*) AS reviewed_orders,
       AVG(1.0 * review_score) AS mean_review_score,
       COUNT(CASE WHEN review_score <= 2 THEN 1 END) AS low_review_orders,
       100.0 * COUNT(CASE WHEN review_score <= 2 THEN 1 END) / COUNT(*) AS low_review_pct
FROM reviewed_deliveries
GROUP BY timing
ORDER BY timing;

-- result: seller_performance
WITH base AS (
    SELECT *
    FROM order_fact
    WHERE delivery_eligible = 1
),
benchmarks AS (
    SELECT 100.0 * COUNT(CASE WHEN purchase_year = 2017 AND delay_days > 0 THEN 1 END)
               / NULLIF(COUNT(CASE WHEN purchase_year = 2017 THEN 1 END), 0) AS market_2017,
           100.0 * COUNT(CASE WHEN purchase_year = 2018 AND delay_days > 0 THEN 1 END)
               / NULLIF(COUNT(CASE WHEN purchase_year = 2018 THEN 1 END), 0) AS market_2018
    FROM base
),
seller_totals AS (
    SELECT s.seller_id,
           COUNT(*) AS orders,
           COUNT(CASE WHEN b.delay_days > 0 THEN 1 END) AS late_orders,
           AVG(b.delivery_days) AS mean_delivery_days,
           AVG(b.delay_days) AS mean_delay_days,
           COUNT(b.review_score) AS reviewed_orders,
           AVG(1.0 * b.review_score) AS mean_review_score,
           COUNT(CASE WHEN b.purchase_year = 2017 THEN 1 END) AS orders_2017,
           COUNT(CASE WHEN b.purchase_year = 2017 AND b.delay_days > 0 THEN 1 END) AS late_2017,
           COUNT(CASE WHEN b.purchase_year = 2018 THEN 1 END) AS orders_2018,
           COUNT(CASE WHEN b.purchase_year = 2018 AND b.delay_days > 0 THEN 1 END) AS late_2018
    FROM seller_order AS s
    JOIN base AS b ON s.order_id = b.order_id
    GROUP BY s.seller_id
    HAVING COUNT(*) >= 100
),
seller_rates AS (
    SELECT seller_totals.*,
           100.0 * late_orders / orders AS late_pct,
           100.0 * late_2017 / NULLIF(orders_2017, 0) AS late_pct_2017,
           100.0 * late_2018 / NULLIF(orders_2018, 0) AS late_pct_2018
    FROM seller_totals
)
SELECT r.*,
       CASE WHEN orders_2017 >= 25 AND orders_2018 >= 25
                 AND late_pct_2017 > market_2017 AND late_pct_2018 > market_2018
            THEN 1 ELSE 0 END AS above_benchmark_both_years
FROM seller_rates AS r CROSS JOIN benchmarks
ORDER BY late_pct DESC, orders DESC, seller_id;

-- result: category_performance
SELECT c.category,
       COUNT(*) AS orders,
       COUNT(CASE WHEN f.delay_days > 0 THEN 1 END) AS late_orders,
       AVG(f.delivery_days) AS mean_delivery_days,
       AVG(f.delay_days) AS mean_delay_days,
       COUNT(f.review_score) AS reviewed_orders,
       AVG(1.0 * f.review_score) AS mean_review_score,
       100.0 * COUNT(CASE WHEN f.delay_days > 0 THEN 1 END) / COUNT(*) AS late_pct
FROM category_order AS c
JOIN order_fact AS f ON c.order_id = f.order_id
WHERE f.delivery_eligible = 1
GROUP BY c.category
HAVING COUNT(*) >= 500
ORDER BY late_pct DESC, orders DESC, c.category;

-- result: state_performance
SELECT customer_state,
       COUNT(*) AS orders,
       COUNT(CASE WHEN delay_days > 0 THEN 1 END) AS late_orders,
       AVG(delivery_days) AS mean_delivery_days,
       AVG(delay_days) AS mean_delay_days,
       COUNT(review_score) AS reviewed_orders,
       AVG(1.0 * review_score) AS mean_review_score,
       100.0 * COUNT(CASE WHEN delay_days > 0 THEN 1 END) / COUNT(*) AS late_pct
FROM order_fact
WHERE delivery_eligible = 1 AND customer_state IS NOT NULL
GROUP BY customer_state
HAVING COUNT(*) >= 500
ORDER BY late_pct DESC, orders DESC, customer_state;

-- result: handoff_performance
WITH handoff AS (
    SELECT CASE WHEN handoff_days > 3 THEN 'Over 3 days' ELSE 'Within 3 days' END AS handoff_group,
           delay_days,
           delivery_days,
           review_score
    FROM order_fact
    WHERE delivery_eligible = 1 AND handoff_days IS NOT NULL
)
SELECT handoff_group,
       COUNT(*) AS orders,
       COUNT(CASE WHEN delay_days > 0 THEN 1 END) AS late_orders,
       AVG(delivery_days) AS mean_delivery_days,
       AVG(delay_days) AS mean_delay_days,
       COUNT(review_score) AS reviewed_orders,
       AVG(1.0 * review_score) AS mean_review_score,
       100.0 * COUNT(CASE WHEN delay_days > 0 THEN 1 END) / COUNT(*) AS late_pct
FROM handoff
GROUP BY handoff_group
ORDER BY handoff_group;
