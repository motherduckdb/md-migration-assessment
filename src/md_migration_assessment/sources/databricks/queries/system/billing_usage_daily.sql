-- md-assess-extract: billing_usage_daily
-- Daily DBU usage per product, SKU and warehouse from system.billing.usage,
-- priced at list from system.billing.list_prices (the price row in effect
-- when the usage ended). List cost, not the negotiated rate. Job, cluster
-- and user identities are not collected.
SELECT
    u.usage_date AS usage_date,
    u.billing_origin_product AS billing_origin_product,
    u.sku_name AS sku_name,
    u.usage_unit AS usage_unit,
    u.usage_type AS usage_type,
    u.usage_metadata.warehouse_id AS warehouse_id,
    u.product_features.is_serverless AS is_serverless,
    sum(u.usage_quantity) AS usage_quantity,
    sum(u.usage_quantity * p.pricing.default) AS list_cost,
    max(p.currency_code) AS currency_code
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
    ON p.sku_name = u.sku_name AND p.cloud = u.cloud AND p.usage_unit = u.usage_unit
    AND u.usage_end_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_end_time < p.price_end_time)
WHERE u.usage_date >= CAST(current_timestamp() - INTERVAL {window_days} DAYS AS DATE)
{workspace_filter:u}
GROUP BY 1, 2, 3, 4, 5, 6, 7
