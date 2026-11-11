-- retention: one row per cohort month (month of the customer's first non-cancelled order).
-- N-day retention = share of the cohort that places a second order within N days of the first.
-- Only customers whose first order is at least N days before {{as_of}} count towards N-day retention.

WITH ranked AS (
    SELECT customer_id, order_ts,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_ts, order_id) AS rn
    FROM orders
    WHERE status <> 'cancelled'
),
per_customer AS (
    SELECT customer_id,
           MIN(order_ts) FILTER (WHERE rn = 1) AS first_ts,
           MIN(order_ts) FILTER (WHERE rn = 2) AS second_ts
    FROM ranked
    GROUP BY customer_id
),
cohorts AS (
    SELECT customer_id,
           CAST(date_trunc('month', first_ts) AS DATE) AS cohort_month,
           CAST(first_ts AS DATE) AS first_date,
           datediff('day', first_ts, second_ts) AS days_to_second
    FROM per_customer
),
counts AS (
    SELECT cohort_month,
           COUNT(*) AS cohort_size,
           COUNT(*) FILTER (WHERE days_to_second IS NOT NULL) AS ever_repeated,
           COUNT(*) FILTER (WHERE datediff('day', first_date, {{as_of}}) >= 30) AS eligible_30d,
           COUNT(*) FILTER (WHERE datediff('day', first_date, {{as_of}}) >= 30
                            AND days_to_second <= 30) AS retained_30d,
           COUNT(*) FILTER (WHERE datediff('day', first_date, {{as_of}}) >= 60) AS eligible_60d,
           COUNT(*) FILTER (WHERE datediff('day', first_date, {{as_of}}) >= 60
                            AND days_to_second <= 60) AS retained_60d,
           COUNT(*) FILTER (WHERE datediff('day', first_date, {{as_of}}) >= 90) AS eligible_90d,
           COUNT(*) FILTER (WHERE datediff('day', first_date, {{as_of}}) >= 90
                            AND days_to_second <= 90) AS retained_90d
    FROM cohorts
    GROUP BY cohort_month
)
SELECT counts.*,
       ROUND(retained_30d * 1.0 / NULLIF(eligible_30d, 0), 4) AS retention_30d,
       ROUND(retained_60d * 1.0 / NULLIF(eligible_60d, 0), 4) AS retention_60d,
       ROUND(retained_90d * 1.0 / NULLIF(eligible_90d, 0), 4) AS retention_90d
FROM counts
ORDER BY cohort_month
