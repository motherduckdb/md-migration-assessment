-- md-assess-extract: served_entities
-- Model serving endpoints and what they serve (custom, foundation or
-- external models, feature specs) from system.serving.served_entities:
-- the latest config row per served entity.
SELECT
    endpoint_name AS endpoint_name,
    served_entity_name AS served_entity_name,
    entity_type AS entity_type,
    entity_name AS entity_name,
    task AS task,
    change_time AS change_time,
    endpoint_delete_time AS endpoint_delete_time
FROM system.serving.served_entities
QUALIFY row_number() OVER (PARTITION BY served_entity_id ORDER BY change_time DESC) = 1
