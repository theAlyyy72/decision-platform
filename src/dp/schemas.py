"""Expected source tables. One place to change when a source system changes."""

EXPECTED_COLUMNS = {
    "customers": ["customer_id", "full_name", "email", "city", "signup_date",
                  "acquisition_channel", "marketing_opt_in"],
    "products": ["product_id", "product_name", "category", "price", "unit_cost", "created_at"],
    "orders": ["order_id", "customer_id", "order_ts", "status", "promised_days", "delivery_days"],
    "order_items": ["order_item_id", "order_id", "product_id", "quantity", "unit_price",
                    "discount_pct"],
    "payments": ["payment_id", "order_id", "payment_method", "amount", "payment_status", "paid_at"],
}