-- top_categories: units, revenue and share of revenue per category, from paid orders.

WITH paid_items AS (
    SELECT i.product_id, i.quantity, i.line_total
    FROM order_items i
    JOIN payments p ON p.order_id = i.order_id
    WHERE p.payment_status = 'paid'
),
by_category AS (
    SELECT pr.category,
           SUM(pi.quantity) AS units_sold,
           ROUND(SUM(pi.line_total), 2) AS revenue
    FROM paid_items pi
    JOIN products pr ON pr.product_id = pi.product_id
    GROUP BY pr.category
)
SELECT category, units_sold, revenue,
       ROUND(revenue * 1.0 / SUM(revenue) OVER (), 4) AS revenue_share,
       RANK() OVER (ORDER BY revenue DESC) AS revenue_rank
FROM by_category
ORDER BY revenue_rank
