# KPI definitions

How each KPI in `docs/business_brief.md` is calculated. The SQL models in `sql/` follow this file.
If you change a definition here, change the matching model too.

Common rules
- An order belongs to the day, week (Monday start) or month of `order_ts`.
- Only orders up to the as-of date are counted, so a run can be repeated exactly.
- A cancelled order never counts as customer activity.
- Curated tables hold no names or emails.

| KPI | Definition | Model |
|---|---|---|
| Orders | All orders placed, any status | `revenue_kpis` |
| Revenue (daily, weekly, monthly) | Net revenue: sum of payment `amount` where `payment_status = 'paid'`. Column `grain` picks day, week or month | `revenue_kpis` |
| GMV | Sum of item totals (`quantity x unit_price x (1 - discount)`) of all orders | `revenue_kpis` |
| Average order value | Net revenue / paid orders | `revenue_kpis` |
| Cancellation rate | Cancelled orders / all orders | `revenue_kpis` |
| Return rate | Returned orders / (delivered + returned orders) | `revenue_kpis` |
| On-time delivery rate | Delivered orders with `delivery_days <= promised_days` / delivered orders | `revenue_kpis` |
| Gross margin % | 1 - cost / revenue on items of paid orders. Cost = `quantity x unit_cost` | `revenue_kpis` |
| New customers | Customers whose first non-cancelled order falls in the period | `revenue_kpis` |
| Repeat customers | Active customers in the period minus new customers | `revenue_kpis` |
| 30/60/90-day retention | Share of a monthly cohort (by first non-cancelled order) that places a second order within 30/60/90 days of the first. Only customers whose first order is at least that many days before the as-of date are counted | `retention` |
| Repeat purchase rate | Customers with 2 or more delivered orders / customers with at least 1 delivered order | `customer_kpis` |
| Churn rate | Customers with no non-cancelled order in the 90 days before the as-of date / customers who ever ordered | `customer_kpis` |
| Customer lifetime value | Historical, not predicted: net revenue per customer to date. Per customer in `customer_360.net_revenue`, average in `customer_kpis.avg_lifetime_value` | `customer_360`, `customer_kpis` |
| Top products and categories | Units and revenue from paid orders, ranked | `top_products`, `top_categories` |
| Customer lifecycle | `active` = last non-cancelled order within 90 days, `lapsed` = older, `never_ordered` = none | `customer_360` |
| RFM segments | Recency and frequency scored 1 to 5 by fixed thresholds, monetary by quintile. Segments: champion, loyal, promising, at_risk, hibernating, needs_attention | `rfm` |

Known gaps
- An order whose payment row was quarantined counts as unpaid until the source is fixed.
- A customer whose row was quarantined (for example a wrong signup date format) is missing from `customer_360`, but their orders still count in `revenue_kpis`.
- Items of a product whose row was quarantined are left out of gross margin and the product rankings.
