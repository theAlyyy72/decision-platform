-- revenue_kpis: one row per period and grain ('day', 'week', 'month'). Weeks start on Monday.
-- Filter on grain in the dashboard. KPI definitions are in docs/kpi_definitions.md.

WITH item_totals AS (
    SELECT i.order_id,
           SUM(i.line_total) AS gmv,
           SUM(i.line_total) FILTER (WHERE pr.unit_cost IS NOT NULL) AS costed_revenue,
           SUM(i.quantity * pr.unit_cost) AS cost
    FROM order_items i
    LEFT JOIN products pr ON pr.product_id = i.product_id
    GROUP BY i.order_id
),
first_order AS (
    SELECT customer_id, MIN(order_ts) AS first_ts
    FROM orders
    WHERE status <> 'cancelled'
    GROUP BY customer_id
),
order_level AS (
    SELECT o.customer_id, o.order_ts, o.status, o.promised_days, o.delivery_days,
           p.payment_status, p.amount, t.gmv, t.costed_revenue, t.cost, f.first_ts
    FROM orders o
    LEFT JOIN payments p ON p.order_id = o.order_id
    LEFT JOIN item_totals t ON t.order_id = o.order_id
    LEFT JOIN first_order f ON f.customer_id = o.customer_id
),
periods AS (
    SELECT g.grain,
           CASE g.grain WHEN 'day'  THEN CAST(date_trunc('day', ol.order_ts) AS DATE)
                        WHEN 'week' THEN CAST(date_trunc('week', ol.order_ts) AS DATE)
                        ELSE CAST(date_trunc('month', ol.order_ts) AS DATE) END AS period_start,
           CASE g.grain WHEN 'day'  THEN CAST(date_trunc('day', ol.first_ts) AS DATE)
                        WHEN 'week' THEN CAST(date_trunc('week', ol.first_ts) AS DATE)
                        ELSE CAST(date_trunc('month', ol.first_ts) AS DATE) END AS first_period_start,
           ol.customer_id, ol.status, ol.promised_days, ol.delivery_days,
           ol.payment_status, ol.amount, ol.gmv, ol.costed_revenue, ol.cost
    FROM order_level ol
    CROSS JOIN (SELECT 'day' AS grain UNION ALL SELECT 'week' UNION ALL SELECT 'month') g
),
agg AS (
    SELECT
        grain,
        period_start,
        COUNT(*) AS orders,
        COUNT(*) FILTER (WHERE status = 'delivered') AS delivered_orders,
        COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled_orders,
        COUNT(*) FILTER (WHERE status = 'returned') AS returned_orders,
        COUNT(DISTINCT customer_id) FILTER (WHERE status <> 'cancelled') AS active_customers,
        COUNT(DISTINCT customer_id) FILTER (WHERE status <> 'cancelled'
                                            AND first_period_start = period_start) AS new_customers,
        ROUND(SUM(gmv), 2) AS gmv,
        COUNT(*) FILTER (WHERE payment_status = 'paid') AS paid_orders,
        ROUND(COALESCE(SUM(amount) FILTER (WHERE payment_status = 'paid'), 0), 2) AS net_revenue,
        ROUND(SUM(amount) FILTER (WHERE payment_status = 'paid')
              / NULLIF(COUNT(*) FILTER (WHERE payment_status = 'paid'), 0), 2) AS avg_order_value,
        ROUND(1 - SUM(cost) FILTER (WHERE payment_status = 'paid')
                  / NULLIF(SUM(costed_revenue) FILTER (WHERE payment_status = 'paid'), 0), 4) AS gross_margin_pct,
        ROUND(COUNT(*) FILTER (WHERE status = 'cancelled') * 1.0 / COUNT(*), 4) AS cancellation_rate,
        ROUND(COUNT(*) FILTER (WHERE status = 'returned') * 1.0
              / NULLIF(COUNT(*) FILTER (WHERE status IN ('delivered', 'returned')), 0), 4) AS return_rate,
        ROUND(COUNT(*) FILTER (WHERE status = 'delivered' AND delivery_days <= promised_days) * 1.0
              / NULLIF(COUNT(*) FILTER (WHERE status = 'delivered'), 0), 4) AS on_time_delivery_rate
    FROM periods
    GROUP BY grain, period_start
)
SELECT grain, period_start, orders, delivered_orders, cancelled_orders, returned_orders,
       active_customers, new_customers, active_customers - new_customers AS repeat_customers,
       gmv, paid_orders, net_revenue, avg_order_value, gross_margin_pct,
       cancellation_rate, return_rate, on_time_delivery_rate
FROM agg
ORDER BY grain, period_start
