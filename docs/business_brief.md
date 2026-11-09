# Business brief: NovaMart (simulated online retailer)

NovaMart is a fictional online retailer with ~50,000 customers. All data is synthetic.

## Goals
- One trusted place for sales, customer and product data
- Spot customers likely to churn before they leave
- Daily KPI view for the business team, with alerts when something breaks

## Source data (daily batch)
customers, products, orders, order_items, payments

## KPIs
Revenue (daily, weekly, monthly), orders, average order value, new vs repeat customers,
repeat purchase rate, 30/60/90-day retention, churn rate, top products and categories,
cancellation rate, customer lifetime value

## Service levels
- Daily data ready by 07:00
- Alert if data is more than 26 hours old
- Pipeline stops and alerts if more than 5% of rows fail validation

## Constraints
- Monthly AWS spend under 10 USD
- No real personal data