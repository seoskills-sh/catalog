-- BigQuery Log File Analyzer — Googlebot crawl rollup (Standard SQL).
-- Parameterized: @window_days (INT64), @sitemap (ARRAY<STRING>), @priority (ARRAY<STRUCT<prefix STRING, weight FLOAT64>>).
-- Replace the {{...}} identifiers from `column_map` before submission (identifiers cannot be bound as query params).
-- ALWAYS submit with dryRun first and maximumBytesBilled = 10 GiB.

WITH src AS (
  SELECT
    TIMESTAMP({{ts}})                              AS ts,
    LOWER(SPLIT({{path}}, '?')[OFFSET(0)])         AS path,          -- strip query string for normalization
    SAFE_CAST({{status}} AS INT64)                 AS status,
    {{ua}}                                         AS ua,
    {{path}}                                       AS raw_path
  FROM `{{logs_table}}`
  WHERE {{ts}} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @window_days DAY)  -- keep partition pruning
    AND {{ua}} LIKE '%Googlebot%'
),
classified AS (
  SELECT
    path, raw_path, status,
    CASE
      WHEN status BETWEEN 200 AND 299 THEN '2xx'
      WHEN status BETWEEN 300 AND 399 THEN '3xx'
      WHEN status BETWEEN 400 AND 499 THEN '4xx'
      WHEN status BETWEEN 500 AND 599 THEN '5xx'
      ELSE 'other'
    END AS status_class,
    REGEXP_CONTAINS(raw_path, r'[?&]') AS is_parameterized
  FROM src
),
status_breakdown AS (
  SELECT status_class, COUNT(*) AS hits
  FROM classified GROUP BY status_class
),
wasted_paths AS (
  SELECT path, ANY_VALUE(status) AS sample_status, COUNTIF(is_parameterized) AS param_hits, COUNT(*) AS hits
  FROM classified
  WHERE status NOT BETWEEN 200 AND 299 OR is_parameterized
  GROUP BY path
  ORDER BY hits DESC
  LIMIT 20
),
per_path AS (
  SELECT path, COUNT(*) AS hits FROM classified GROUP BY path
),
orphaned AS (
  SELECT p.path, p.hits
  FROM per_path p
  WHERE ARRAY_LENGTH(@sitemap) > 0 AND p.path NOT IN UNNEST(@sitemap)
  ORDER BY p.hits DESC
  LIMIT 50
),
under_crawled AS (
  SELECT pr.prefix, pr.weight,
         COALESCE(SUM(pp.hits), 0)                       AS hits,
         SAFE_DIVIDE(COALESCE(SUM(pp.hits), 0), @window_days) AS crawl_rate_per_day
  FROM UNNEST(@priority) pr
  LEFT JOIN per_path pp ON STARTS_WITH(pp.path, pr.prefix)
  GROUP BY pr.prefix, pr.weight
  HAVING pr.weight >= 0.7 AND crawl_rate_per_day < 1.0
)
SELECT
  (SELECT ARRAY_AGG(STRUCT(status_class, hits)) FROM status_breakdown) AS status_breakdown,
  (SELECT ARRAY_AGG(STRUCT(path, sample_status, param_hits, hits)) FROM wasted_paths) AS wasted_paths,
  (SELECT ARRAY_AGG(STRUCT(path, hits)) FROM orphaned) AS orphaned,
  (SELECT ARRAY_AGG(STRUCT(prefix, weight, hits, crawl_rate_per_day)) FROM under_crawled) AS under_crawled,
  (SELECT SUM(hits) FROM status_breakdown) AS total_googlebot_hits;
