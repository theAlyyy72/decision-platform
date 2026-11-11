-- customer_kpis: one row, a snapshot as of {{as_of}}. Definitions: docs/kpi_definitions.md.

WITH per_customer AS (
    SELECT o.customer_id,
           COUNT(*) FILTER (WHERE o.status = 'delivered') AS orders_delivered,
           MAX(o.order_ts) FILTER (WHERE o.status <> 'cancelled') AS last_ts,
           COALESCE(SUM(p.amount) FILTER (WHERE p.payment_status = 'paid'), 0) AS net_revenue
    FROM orders o
    LEFT JOIN payments p ON p.order_id = o.order_id
    GROUP BY o.customer_id
),
counts AS (
    SELECT COUNT(*) FILTER (WHERE last_ts IS NOT NULL) AS customers_with_orders,
           COUNT(*) FILTER (WHERE last_ts IS NOT NULL
                            AND datediff('day', CAST(last_ts AS DATE), {{as_of}}) <= 90) AS active_customers_90d,
           COUNT(*) FILTER (WHERE orders_delivered >= 1) AS delivered_customers,
           COUNT(*) FILTER (WHERE orders_delivered >= 2) AS repeat_customers,
           SUM(net_revenue) FILTER (WHERE last_ts IS NOT NULL) AS total_net_revenue
    FROM per_customer
)
SELECT {{as_of}} AS as_of_date,
       customers_with_orders,
       active_customers_90d,
       customers_with_orders - active_customers_90d AS lapsed_customers,
       ROUND(1 - active_customers_90d * 1.0 / NULLIF(customers_with_orders, 0), 4) AS churn_rate,
       delivered_customers,
       repeat_customers,
       ROUND(repeat_customers * 1.0 / NULLIF(delivered_customers, 0), 4) AS repeat_purchase_rate,
       ROUND(total_net_revenue / NULLIF(customers_with_orders, 0), 2) AS avg_lifetime_value
FROM counts
