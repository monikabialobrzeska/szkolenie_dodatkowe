# Test fixtures

Small slices of the **real** pinned map-qa snapshot (`course-materials/exercise-data/map-qa/`), extracted read-only by
`build_fixtures.py` (run from `app/`: `uv run python tests/fixtures/build_fixtures.py`).

- `roads.geojson` — OSM ways of every case below plus every way within 30 m of a selected sign (EPSG:4326, `tags` as JSON).
- `signs.geojson` — selected Mapillary speed-limit signs (EPSG:4326).
- `cases.json` — case name → selected id and the selection query.

Synthetic values exist only inside test code and are marked `# synthetic` there (mph, `PL:expressway` in `maxspeed`,
`50;70`, `none`, the 20.0 m boundary geometry).

| Case | Id | Selection |
|---|---|---|
| `numeric` | way 19846887 | `maxspeed = '50' AND fwd IS NULL AND bwd IS NULL AND src IS NULL AND typ IS NULL AND cond IS NULL AND highway = 'residential'` |
| `directional_both` | way 22810656 | `maxspeed IS NULL AND fwd IS NOT NULL AND bwd IS NOT NULL AND fwd <> bwd AND fwd NOT LIKE 'PL:%' AND bwd NOT LIKE 'PL:%'` |
| `forward_only` | way 24559626 | `maxspeed IS NULL AND fwd IS NOT NULL AND bwd IS NULL` |
| `implicit_in_backward` | way 907138970 | `osm_id = 907138970` |
| `conditional_motorway` | way 19793303 | `cond IS NOT NULL` |
| `variable_motorway` | way 119039118 | `var = 'yes'` |
| `main_no_limit` | way 77882926 | `highway = 'secondary' AND maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL` |
| `main_link_no_limit` | way 22939434 | `highway = 'motorway_link' AND maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL` |
| `tertiary_no_limit` | way 19803315 | `highway = 'tertiary' AND maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL` |
| `bridge_yes` | way 19796385 | `bridge = 'yes' AND maxspeed IS NOT NULL` |
| `building_passage` | way 83675713 | `tunnel = 'building_passage' AND maxspeed IS NOT NULL` |
| `urban_70` | way 21920953 | `src = 'PL:urban' AND maxspeed = '70'` |
| `rural_100` | way 27150435 | `src = 'PL:rural' AND maxspeed = '100'` |
| `zone40_30` | way 111856373 | `src = 'PL:zone40' AND maxspeed = '30'` |
| `source_sign` | way 4757979 | `src = 'sign' AND maxspeed IS NOT NULL` |
| `type_rural_50` | way 135095251 | `typ = 'PL:rural' AND maxspeed = '50'` |
| `sign_equal` | sign 1009182420826232 | `SELECT id FROM one WHERE TRY_CAST(maxspeed AS INT) = v AND fwd IS NULL AND bwd IS NULL` |
| `sign_differ_fresh` | sign 1042247297698500 | `SELECT id FROM one WHERE TRY_CAST(maxspeed AS INT) <> v AND fwd IS NULL AND bwd IS NULL AND cond IS NULL AND var IS NULL AND coalesce(bridge,'no') = 'no' AND coalesce(tunnel,'no') = 'no' AND last_seen_at >= edited` |
| `sign_differ_stale` | sign 10032945853452691 | `SELECT id FROM one WHERE TRY_CAST(maxspeed AS INT) <> v AND fwd IS NULL AND bwd IS NULL AND cond IS NULL AND var IS NULL AND coalesce(bridge,'no') = 'no' AND coalesce(tunnel,'no') = 'no' AND last_seen_at < edited` |
| `sign_multi_two_roads` | sign 1002528968512774 | `SELECT id FROM elig GROUP BY id HAVING count(*) = 2` |
| `sign_service_only` | sign 1002363373868199 | `SELECT id FROM a GROUP BY id HAVING count(*) FILTER (WHERE highway <> 'service') = 0` |
| `sign_directional` | sign 1067368908321588 | `SELECT id FROM one WHERE fwd IS NOT NULL OR bwd IS NOT NULL` |
| `sign_road_no_limit` | sign 1018278936185808 | `SELECT id FROM one WHERE maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL AND cond IS NULL` |
