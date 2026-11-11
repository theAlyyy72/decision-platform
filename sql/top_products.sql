-- top_products: every product with units and revenue from paid orders, ranked.

WITH paid_items AS (
    SELECT i.product_id, i.quantity, i.line_total
    FROM order_items i
    JOIN payments p ON p.order_id = i.order_id 
    WHERE p.payment_status = 'paid'
),
by_product AS (
    SELECT pr.product_id, pr.product_name, pr.category,
           COALESCE(SUM(pi.quantity), 0) AS units_sold,
           ROUND(COALESCE(SUM(pi.line_total), 0), 2) AS revenue
    FROM products pr
    LEFT JOIN paid_items pi ON pi.product_id = pr.product_id
    GROUP BY pr.product_id, pr.product_name, pr.category
)
SELECT product_id, product_name, category, units_sold, revenue,
       RANK() OVER (ORDER BY revenue DESC) AS revenue_rank,
       RANK() OVER (PARTITION BY category ORDER BY revenue DESC) AS rank_in_category
FROM by_product
ORDER BY revenue_rank, product_id
