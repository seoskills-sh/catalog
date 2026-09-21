-- GA4 Attribution Path Modeler — multi-touch credit over the GA4 BigQuery export.
-- Standard SQL. Params: @start,@end (YYYYMMDD strings for _TABLE_SUFFIX),
-- @conversion_events (ARRAY<STRING>), @lookback (INT64 days).
-- Submit dryRun first; set maximumBytesBilled = 50 GiB.
-- Replace `PROJECT.DATASET` with {project}.{dataset} before submission.

DECLARE epsilon FLOAT64 DEFAULT 1e-9;

WITH events AS (
  SELECT
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    event_name,
    TIMESTAMP_MICROS(event_timestamp) AS ts,
    -- channel derivation: prefer collected_traffic_source (newer export), else event traffic_source
    LOWER(COALESCE(collected_traffic_source.manual_source, traffic_source.source, '(direct)')) AS src,
    LOWER(COALESCE(collected_traffic_source.manual_medium, traffic_source.medium, '(none)'))   AS med,
    ecommerce.purchase_revenue AS purchase_revenue,
    (SELECT value.double_value FROM UNNEST(event_params) WHERE key = 'value') AS event_value
  FROM `PROJECT.DATASET.events_*`
  WHERE _TABLE_SUFFIX BETWEEN @start AND @end
),
sessions AS (
  SELECT
    user_pseudo_id, session_id,
    MIN(ts) AS session_start,
    ANY_VALUE(src) AS src, ANY_VALUE(med) AS med,
    CASE
      WHEN ANY_VALUE(med) = 'organic' THEN 'Organic Search'
      WHEN REGEXP_CONTAINS(ANY_VALUE(med), r'cpc|ppc|paid') THEN 'Paid Search'
      WHEN ANY_VALUE(med) IN ('(none)', '') AND ANY_VALUE(src) = '(direct)' THEN 'Direct'
      WHEN REGEXP_CONTAINS(ANY_VALUE(med), r'social') OR REGEXP_CONTAINS(ANY_VALUE(src), r'facebook|instagram|twitter|x\.com|linkedin|tiktok|reddit') THEN 'Social'
      WHEN ANY_VALUE(med) = 'email' THEN 'Email'
      WHEN ANY_VALUE(med) = 'referral' THEN 'Referral'
      ELSE 'Other'
    END AS channel
  FROM events
  WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id
),
conversions AS (
  SELECT user_pseudo_id, ts AS conv_ts,
         COALESCE(purchase_revenue, event_value, 1.0) AS conv_value
  FROM events
  WHERE event_name IN UNNEST(@conversion_events)
),
paths AS (
  SELECT
    c.user_pseudo_id, c.conv_ts, c.conv_value,
    ARRAY_AGG(s.channel ORDER BY s.session_start) AS touch_channels
  FROM conversions c
  JOIN sessions s
    ON s.user_pseudo_id = c.user_pseudo_id
   AND s.session_start <= c.conv_ts
   AND s.session_start >= TIMESTAMP_SUB(c.conv_ts, INTERVAL @lookback DAY)
  GROUP BY c.user_pseudo_id, c.conv_ts, c.conv_value
),
-- dedupe consecutive repeats into an ordered touch list
dedup AS (
  SELECT conv_value,
    ARRAY(
      SELECT ch FROM UNNEST(touch_channels) ch WITH OFFSET o
      WHERE o = 0 OR ch != touch_channels[OFFSET(o - 1)]
    ) AS path
  FROM paths
),
credited AS (
  SELECT
    path, conv_value, ARRAY_LENGTH(path) AS n,
    ch, o AS pos
  FROM dedup, UNNEST(path) ch WITH OFFSET o
),
model AS (
  SELECT
    ch AS channel,
    -- last click: only the final touch
    SUM(IF(pos = n - 1, conv_value, 0)) AS last_click,
    -- linear
    SUM(conv_value / n) AS linear,
    -- position-based 40/20/40
    SUM(
      CASE
        WHEN n = 1 THEN conv_value
        WHEN pos = 0 OR pos = n - 1 THEN conv_value * 0.4
        ELSE conv_value * 0.2 / GREATEST(n - 2, 1)
      END
    ) AS position_based
  FROM credited
  GROUP BY ch
)
SELECT
  (SELECT COUNT(*) FROM conversions) AS conversion_count,
  (SELECT ROUND(SUM(conv_value), 2) FROM conversions) AS total_value,
  ARRAY_AGG(STRUCT(channel, ROUND(last_click, 2) AS last_click,
                   ROUND(linear, 2) AS linear, ROUND(position_based, 2) AS position_based)
            ORDER BY position_based DESC) AS by_channel,
  (SELECT ROUND(SAFE_DIVIDE(SUM(position_based), GREATEST(SUM(last_click), epsilon)), 3)
   FROM model WHERE channel = 'Organic Search') AS organic_assist_ratio
FROM model;
