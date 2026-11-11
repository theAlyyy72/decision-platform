-- customer_360: one row per customer, everything we know about them as of {{as_of}}.
-- No name or email on purpose: curated tables hold no personal data.
-- Cancelled orders do not count as activity (first/last order, lifecycle, favourite category).

WITH order_level AS (
    SELECT o.customer_id, o.order_ts, o.status, p.payment_status, p.amount
    FROM orders o
    LEFT JOIN payments p ON p.order_id = o.order_id
),
per_customer AS (
    SELECT customer_id,
           COUNT(*) AS orders_total,
           COUNT(*) FILTER (WHERE status = 'delivered') AS orders_delivered,
           COUNT(*) FILTER (WHERE status = 'cancelled') AS orders_cancelled,
           COUNT(*) FILTER (WHERE status = 'returned') AS orders_returned,
           CAST(MIN(order_ts) FILTER (WHERE status <> 'cancelled') AS DATE) AS first_order_date,
           CAST(MAX(order_ts) FILTER (WHERE status <> 'cancelled') AS DATE) AS last_order_date,
           COUNT(*) FILTER (WHERE payment_status = 'paid') AS paid_orders,
           SUM(amount) FILTER (WHERE payment_status = 'paid') AS net_revenue
    FROM order_level
    GROUP BY customer_id
),
favourite_category AS (
    SELECT customer_id, category
    FROM (
        SELECT o.customer_id, pr.category,
               ROW_NUMBER() OVER (PARTITION BY o.customer_id
                                  ORDER BY COUNT(*) DESC, pr.category) AS rn
        FROM order_items i
        JOIN orders o ON o.order_id = i.order_id
        JOIN products pr ON pr.product_id = i.product_id
        WHERE o.status <> 'cancelled'
        GROUP BY o.customer_id, pr.category
    ) ranked
    WHERE rn = 1
),
combined AS (
    SELECT c.customer_id, c.city, c.acquisition_channel, c.marketing_opt_in,
           (c.email IS NOT NULL) AS has_email,
           c.signup_date,
           datediff('day', c.signup_date, {{as_of}}) AS tenure_days,
           COALESCE(pc.orders_total, 0) AS orders_total,
           COALESCE(pc.orders_delivered, 0) AS orders_delivered,
           COALESCE(pc.orders_cancelled, 0) AS orders_cancelled,
           COALESCE(pc.orders_returned, 0) AS orders_returned,
           COALESCE(pc.paid_orders, 0) AS paid_orders,
           ROUND(COALESCE(pc.net_revenue, 0), 2) AS net_revenue,
           pc.first_order_date,
           pc.last_order_date,
           datediff('day', pc.last_order_date, {{as_of}}) AS days_since_last_order,
           fc.category AS favourite_category
    FROM customers c
    LEFT JOIN per_customer pc ON pc.customer_id = c.customer_id
    LEFT JOIN favourite_category fc ON fc.customer_id = c.customer_id
    WHERE c.signup_date <= {{as_of}}
)
SELECT combined.*,
       (orders_delivered >= 2) AS is_repeat_customer,
       CASE WHEN last_order_date IS NULL THEN 'never_ordered'
            WHEN days_since_last_order <= 90 THEN 'active'
            ELSE 'lapsed' END AS lifecycle_status
FROM combined
ORDER BY customer_id
