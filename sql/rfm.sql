-- rfm: one row per customer with at least one non-cancelled order, scored as of {{as_of}}.
-- Recency (r_score) and frequency (f_score) use fixed business thresholds, so a score means the
-- same thing in every run. Monetary (m_score) is a quintile, 5 = highest spend.

WITH per_customer AS (
    SELECT o.customer_id,
           COUNT(*) FILTER (WHERE o.status <> 'cancelled') AS frequency,
           MAX(o.order_ts) FILTER (WHERE o.status <> 'cancelled') AS last_ts,
           COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'paid'), 0) AS monetary
    FROM orders o
    LEFT JOIN payments p ON p.order_id = o.order_id
    GROUP BY o.customer_id
),
buyers AS (
    SELECT customer_id, frequency, monetary,
           datediff('day', CAST(last_ts AS DATE), {{as_of}}) AS recency_days
    FROM per_customer
    WHERE frequency > 0
),
scored AS (
    SELECT customer_id, recency_days, frequency, monetary,
           CASE WHEN recency_days <= 30 THEN 5
                WHEN recency_days <= 60 THEN 4
                WHEN recency_days <= 90 THEN 3
                WHEN recency_days <= 180 THEN 2
                ELSE 1 END AS r_score,
           CASE WHEN frequency >= 5 THEN 5 ELSE frequency END AS f_score,
           NTILE(5) OVER (ORDER BY monetary, customer_id) AS m_score
    FROM buyers
)
SELECT customer_id, recency_days, frequency, ROUND(monetary, 2) AS monetary,
       r_score, f_score, m_score,
       CAST(r_score AS VARCHAR) || CAST(f_score AS VARCHAR) || CAST(m_score AS VARCHAR) AS rfm_code,
       CASE WHEN r_score >= 4 AND f_score >= 4 THEN 'champion'
            WHEN r_score >= 3 AND f_score >= 3 THEN 'loyal'
            WHEN r_score >= 4 AND f_score <= 2 THEN 'promising'
            WHEN r_score <= 2 AND f_score >= 3 THEN 'at_risk'
            WHEN r_score <= 2 THEN 'hibernating'
            ELSE 'needs_attention' END AS segment
FROM scored
ORDER BY customer_id
