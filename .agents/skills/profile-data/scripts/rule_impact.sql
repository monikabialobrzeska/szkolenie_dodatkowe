-- Measures how many candidates the ADR-002 rules would produce (tag_inconsistency, stale_sign, bridge_or_tunnel).
-- Keep in sync with docs/ADR/002-detection-rules.md. Comparisons must stay NULL-safe (coalesce).
LOAD spatial;
-- tag_inconsistency: implicit source vs numeric maxspeed
CREATE TEMP TABLE r AS SELECT osm_id, highway, maxspeed, osm_timestamp, bridge, tunnel,
  json_extract_string(tags, '$."source:maxspeed"') AS src, json_extract_string(tags, '$."maxspeed:type"') AS typ,
  json_extract_string(tags, '$."maxspeed:forward"') AS fwd, json_extract_string(tags, '$."maxspeed:backward"') AS bwd,
  ST_Transform(geometry, 'EPSG:4326', 'EPSG:2180', always_xy := true) AS g
  FROM read_parquet('osm_roads.parquet');
CREATE TEMP MACRO allowed(v) AS CASE WHEN v = 'PL:urban' THEN [50] WHEN v = 'PL:rural' THEN [90, 100] WHEN v = 'PL:expressway' THEN [100, 120] WHEN v = 'PL:motorway' THEN [140] WHEN v = 'PL:living_street' THEN [20] WHEN regexp_full_match(v, 'PL:zone\d+') THEN [CAST(regexp_extract(v, '\d+') AS INT)] ELSE NULL END;
SELECT 'source:maxspeed' AS tag, src AS v, maxspeed, count(*) AS n FROM r WHERE allowed(src) IS NOT NULL AND maxspeed IS NOT NULL AND NOT list_contains(allowed(src), CAST(maxspeed AS INT)) GROUP BY ALL
UNION ALL SELECT 'maxspeed:type', typ, maxspeed, count(*) FROM r WHERE allowed(typ) IS NOT NULL AND maxspeed IS NOT NULL AND NOT list_contains(allowed(typ), CAST(maxspeed AS INT)) GROUP BY ALL ORDER BY 1, 4 DESC;
SELECT count(*) AS ways_tag_inconsistency, count(*) FILTER (WHERE highway IN ('motorway','motorway_link','trunk','trunk_link','primary','primary_link','secondary','secondary_link')) AS on_main_roads FROM r WHERE maxspeed IS NOT NULL AND ((allowed(src) IS NOT NULL AND NOT list_contains(allowed(src), CAST(maxspeed AS INT))) OR (allowed(typ) IS NOT NULL AND NOT list_contains(allowed(typ), CAST(maxspeed AS INT))));
SELECT count(*) AS src_and_type_disagree FROM r WHERE allowed(src) IS NOT NULL AND allowed(typ) IS NOT NULL AND src <> typ;
SELECT count(*) AS implicit_source_without_maxspeed FROM r WHERE maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL AND (allowed(src) IS NOT NULL OR allowed(typ) IS NOT NULL);
-- stale_sign estimate: signs with exactly one eligible road within 20 m
CREATE TEMP TABLE s AS SELECT id, CAST(regexp_extract(object_value, 'limit-(\d+)--', 1) AS INT) AS v, last_seen_at, ST_Transform(geom, 'EPSG:4326', 'EPSG:2180', always_xy := true) AS g FROM ST_Read('traffic_signs.geojson');
CREATE TEMP TABLE a AS SELECT s.id, s.v, s.last_seen_at, r.osm_id, r.maxspeed, r.fwd, r.bwd, r.osm_timestamp, r.bridge, r.tunnel FROM s JOIN r ON ST_DWithin(s.g, r.g, 20) WHERE r.highway <> 'service';
CREATE TEMP TABLE one AS SELECT * FROM a WHERE id IN (SELECT id FROM a GROUP BY id HAVING count(*) = 1);
CREATE TEMP TABLE e AS SELECT *, coalesce(TRY_CAST(maxspeed AS INT) = v, false) OR coalesce(TRY_CAST(fwd AS INT) = v, false) OR coalesce(TRY_CAST(bwd AS INT) = v, false) AS is_match, (maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL) AS no_limit, last_seen_at < CAST(osm_timestamp AS TIMESTAMPTZ) AS stale, (bridge IN ('yes','viaduct') OR tunnel IN ('yes','covered')) AS level_new, (coalesce(bridge,'no') <> 'no' OR coalesce(tunnel,'no') <> 'no') AS level_old FROM one;
SELECT count(*) AS one_road_signs, count(*) FILTER (WHERE is_match) AS equal, count(*) FILTER (WHERE NOT is_match AND NOT no_limit) AS differ, count(*) FILTER (WHERE no_limit) AS road_no_limit, count(*) FILTER (WHERE NOT is_match AND stale) AS not_matching_and_stale, count(*) FILTER (WHERE NOT is_match AND coalesce(level_new, false)) AS level_new_rule, count(*) FILTER (WHERE NOT is_match AND level_old) AS level_old_rule FROM e;
SELECT count(DISTINCT id) AS signs_with_2plus_roads FROM a WHERE id NOT IN (SELECT id FROM one);
