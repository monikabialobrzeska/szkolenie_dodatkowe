# Profil danych map-qa (Kraków) — 2026-11-12

Profil wejść dla Speed-Limit Conflict Finder (Stage 1). Wszystkie odczyty były tylko do odczytu; dane wejściowe nie zostały zmienione (SHA-256 przed i po identyczne, sekcja 9). Nie użyto płatnego API.

---

## 1. Polecenia i wersje

| Narzędzie | Wersja |
|---|---|
| uv | 0.13.7 (316c827e0 2026-11-03) |
| Python (uv-managed) | 3.12.16 |
| DuckDB (Python) + rozszerzenie `spatial` | 1.5.6 |
| Databricks CLI | 1.26.0 (zainstalowany; zapytanie wykonano przez REST w `query_databricks.py`) |
| System | Windows 11, Git Bash z Claude Code |

Polecenia, z katalogu głównego repozytorium:

```powershell
# 1. Profil dołączony do danych (profile.sql)
uv run --python 3.12 course-materials/exercise-data/map-qa/profile_data.py

# 2. Dodatkowe zapytania profilujące (tylko SELECT/DESCRIBE, DuckDB in-memory)
#    skrypt i SQL w katalogu scratchpad sesji agenta, nie w repozytorium
uv run --python 3.12 run_sql.py extra_profile.sql  course-materials/exercise-data/map-qa
uv run --python 3.12 run_sql.py extra_profile2.sql course-materials/exercise-data/map-qa

# 3. Jeden odczyt Databricks (zmienne DATABRICKS_* sprawdzone tylko pod kątem obecności)
uv run --python 3.12 course-materials/exercise-data/map-qa/query_databricks.py --statement "SELECT 'osm_roads' AS source, count(*) AS rows FROM workspace.map_qa.osm_roads UNION ALL ... traffic_signs"
```

Uwaga: w poleceniu z zadania była nazwa `profiledata.py`; plik w repozytorium nazywa się `profile_data.py`. `INSTALL spatial` pobrał rozszerzenie DuckDB do profilu użytkownika (poza repozytorium).

---

## 2. Liczności i porównanie z Databricks

Odczyt Databricks: 2026-11-12T12:54:57Z, stan `SUCCEEDED`, `truncated: false`, warehouse ćwiczenia, schemat `workspace.map_qa`.

| Zbiór | Pliki lokalne | Databricks | `sources.json` | Zgodność |
|---|---:|---:|---:|---|
| `osm_roads` | 99 768 | 99 768 | 99 768 | ✅ |
| `overture_segments` | 85 405 | 85 405 | 85 405 | ✅ |
| `photos` | 561 | 561 | 561 | ✅ |
| `photo_road_links` | 561 | 561 | — | ✅ |
| `traffic_signs` | 1 730 | 1 730 | — | ✅ |

Databricks zwraca `count(*)` jako tekst (`"99768"`) mimo typu `BIGINT` w schemacie (format `JSON_ARRAY`). Adapter Stage 2 musi rzutować typy według `schema`.

### OSM: drogi wg `highway`

| highway | drogi | z `maxspeed` | % |
|---|---:|---:|---:|
| service | 72 113 | 1 997 | 3 |
| residential | 11 799 | 5 467 | 46 |
| track | 4 296 | 2 | 0 |
| tertiary | 3 838 | 2 839 | 74 |
| secondary | 2 073 | 1 897 | 92 |
| living_street | 1 938 | 1 140 | 59 |
| primary | 1 586 | 1 539 | 97 |
| unclassified | 880 | 316 | 36 |
| motorway | 459 | 453 | 99 |
| motorway_link | 229 | 192 | 84 |
| secondary_link | 180 | 142 | 79 |
| trunk | 147 | 143 | 97 |
| primary_link | 127 | 110 | 87 |
| trunk_link | 54 | 21 | 39 |
| tertiary_link | 46 | 32 | 70 |
| road / busway | 2 / 1 | 0 / 0 | — |

Suma długości dróg OSM (EPSG:2180): ok. 7 435 km. 23 drogi są krótsze niż 1 m. 560 dróg ma `boundary_crossing` (wychodzi poza bbox).

### Overture: segmenty wg `class`

| class | segmenty | z regułą prędkości |
|---|---:|---:|
| service | 60 968 | 1 624 |
| residential | 11 185 | 5 129 |
| track | 3 663 | 2 |
| tertiary | 3 102 | 2 180 |
| living_street | 2 278 | 1 378 |
| secondary | 1 335 | 1 192 |
| primary | 916 | 840 |
| unknown | 844 | 2 |
| unclassified | 816 | 262 |
| motorway | 211 | 172 |
| trunk | 87 | 66 |

72 558 z 85 405 segmentów (85%) nie ma żadnej reguły `speed_limits`. Wszystkie 14 205 reguł ma jednostkę `km/h`; 880 ma warunek `when`, 1 925 zakres `between`.

### Zdjęcia

| Źródło | zdjęcia | sekwencje | z `heading` | z autorem / licencją |
|---|---:|---:|---:|---:|
| Panoramax | 461 | 56 | 446 | 461 / 461 |
| Mapillary | 100 | 52 | 100 | 100 / 100 |

Daty zdjęć: 2014-07-14 – 2026-10-18. Panoramy: 31. Plików w `offline/`: 40 (+ `ATTRIBUTION.csv` z 40 wierszami).

### Znaki (Mapillary)

| Wartość km/h | 70 | 40 | 50 | 30 | 20 | 60 | 110 | 80 | 10 | 5 | 15 | 90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| znaki | 696 | 321 | 236 | 211 | 72 | 64 | 55 | 37 | 20 | 8 | 5 | 5 |

Wszystkie 1 730 `object_value` pasują do wzorca `regulatory--maximum-speed-limit-<N>--g<k>`. `first_seen_at` od 2014-05-02, `last_seen_at` do 2026-10-28.

---

## 3. Typy kolumn (schemat)

**`osm_roads.parquet`** (26 kolumn): `osm_id` BIGINT, `osm_version` BIGINT, `osm_timestamp` VARCHAR, `node_ids` BIGINT[], `start_node`/`end_node` BIGINT, `boundary_crossing` BOOLEAN, `tags` VARCHAR (JSON), `highway`, `maxspeed`, `surface`, `lanes`, `oneway`, `lit`, `sidewalk`, `sidewalk:left`, `sidewalk:right`, `junction`, `ref`, `name`, `access`, `motor_vehicle`, `bridge`, `tunnel`, `layer` — wszystkie VARCHAR; `geometry` GEOMETRY('EPSG:4326'). **`maxspeed` i `layer` są tekstem**, nie liczbą.

**`overture_segments.parquet`** (14 kolumn): `id` VARCHAR, `geometry` GEOMETRY('OGC:CRS84'), `class`, `subclass`, `name` VARCHAR, `sources` STRUCT[] (m.in. `record_id`, `update_time`, `between`), `connectors` STRUCT[], `speed_limits` STRUCT[] (`max_speed{value INTEGER, unit}`, `is_max_speed_variable`, `when{during, heading, using, recognized, mode, vehicle[]}`, `between DOUBLE[]`), `road_surface`, `road_flags`, `access_restrictions`, `level_rules` STRUCT[], `bbox` STRUCT, `version` INTEGER.

**`photos.csv`** (21 kolumn, typy wykryte przez DuckDB): `photo_id`, `filename`, `source`, `url`, `source_url`, `sequence`, `license`, `license_url`, `author`, `source_sha256`, `sha256`, `transformation` VARCHAR; `longitude`, `latitude`, `heading`, `horizontal_accuracy_m` DOUBLE; `captured_at` TIMESTAMPTZ; `bytes`, `width`, `height` BIGINT; `is_panorama` BOOLEAN.

**`photo_road_links.csv`** (11 kolumn): `photo_id`, `overture_id`, `match_method`, `candidate_flags` VARCHAR; `osm_way_id`, `osm_candidate_count` BIGINT; `distance_m`, `way_bearing`, `bearing_diff`, `directed_bearing_diff`, `overture_distance_m` DOUBLE.

**`traffic_signs.geojson`** (przez `ST_Read`): `id`, `object_value`, `object_type`, `source`, `license`, `source_url` VARCHAR; `first_seen_at`, `last_seen_at` TIMESTAMPTZ; `image_ids` VARCHAR[]; `geom` GEOMETRY('EPSG:4326').

---

## 4. CRS i geometria

| Zbiór | CRS (metadane) | Kodowanie | Typ geometrii | Puste | NULL | Niepoprawne (`ST_IsValid`) |
|---|---|---|---|---:|---:|---:|
| OSM | EPSG:4326 (GeoParquet `crs.id`) | WKB | LineString (100%) | 0 | 0 | 0 |
| Overture | brak `crs` w metadanych → domyślnie OGC:CRS84 wg specyfikacji GeoParquet | WKB | LineString (100%) | 0 | 0 | 0 |
| Znaki | EPSG:4326 (GeoJSON) | — | Point (100%) | 0 | 0 | 0 |
| Zdjęcia | lon/lat w kolumnach, WGS84 | — | — | — | 0 | — |

- Wszystkie zdjęcia i znaki leżą w bbox `19.80, 49.95, 20.15, 50.15`; każda droga OSM go przecina.
- **DuckDB 1.5.6 zwraca kolumnę `geometry` jako typ `GEOMETRY` z CRS także bez załadowanego `spatial`** (typ zawiera pełny PROJJSON). Nie jest to surowy BLOB WKB.
- Dystanse w raporcie liczono po `ST_Transform(..., 'EPSG:4326', 'EPSG:2180', always_xy := true)`.

---

## 5. Duplikaty

| Klucz | Duplikaty |
|---|---:|
| `osm_roads.osm_id` | 0 |
| `overture_segments.id` | 0 |
| `traffic_signs.id` | 0 |
| `photos.photo_id` / `filename` / `sha256` | 0 / 0 / 0 |
| `photo_road_links.photo_id` | 0 (dokładnie jeden wiersz na zdjęcie) |
| Identyczna geometria OSM (WKB) | 0 |
| **Znaki w identycznym punkcie** | **2 pary** |

Pary znaków w tym samym punkcie: `264045078794875` / `266154541917262` (obie 40 km/h) oraz `304714361170519` (40) / `911105329741888` (**70**). Druga para to sprzeczne wartości w jednym miejscu.

---

## 6. Relacje kluczy `photo_id` i pozostałe

| Relacja | Wynik |
|---|---|
| `photo_road_links.photo_id` → `photos.photo_id` | 561/561, brak sierot |
| `photos.photo_id` bez wiersza w links | 0 |
| `links.osm_way_id` → `osm_roads.osm_id` | 558 niepustych, 0 brakujących |
| `links.overture_id` → `overture_segments.id` | 558 niepustych, 0 brakujących |
| Prefiks `photo_id` ↔ `source` | `mapillary_*` = Mapillary (100), `panoramax_*` = Panoramax (461), zgodne |
| Zdjęcia Mapillary wskazane w `traffic_signs.image_ids` | **100/100** (próbka Mapillary wybrana przez znaki) |
| **Różne drogi OSM z jakimkolwiek zdjęciem** | **230 z 99 768** |

`match_method`: `osm_source_reference` 557, `spatial_fallback` 1 (`mapillary_1127617272466620`, flaga `overture_spatial_only`), `none` 3 (Panoramax, flaga `no_osm_within_25m`).

Flagi `candidate_flags` (zdjęcie może mieć kilka): `ambiguous_candidates` 65, `camera_against_way_direction` 35, `panorama_heading_ambiguous` 31, `boundary_crossing` 27, `poor_heading_alignment` 27, `missing_heading` 15, `service_way` 14, `no_osm_within_25m` 3, `overture_spatial_only` 1. Bez flag: 415.

Dystans zdjęcie–droga: min 0, mediana 2,43 m, p95 9,82 m, max 22,17 m.

---

## 7. `maxspeed`: rozkład i formaty

### Kolumna `maxspeed`

| Format | Drogi |
|---|---:|
| brak (NULL) | 83 478 |
| liczba całkowita | 16 290 |
| `mph`, `km/h`, `PL:*`, `none`/`signals`/`walk`, wiele wartości (`;`/`,`), inne | **0** |

Wartości: 50 (5 737), 30 (4 802), 20 (2 053), 70 (1 294), 40 (1 245), 140 (214), 90 (184), 60 (181), 100 (167), 10 (157), 120 (134), 80 (61), 5 (48), 25 (7), 15 (4), **4 (2)**.

### Tagi `maxspeed*` w `tags` (liczba dróg z kluczem)

`source:maxspeed` 11 671 · `maxspeed:type` 2 578 · `maxspeed:bus` 1 031 · `maxspeed:trailer` 1 028 · `maxspeed:hgv` 1 007 · `maxspeed:forward` 261 · `maxspeed:backward` 247 · `source:maxspeed:forward` 225 · `source:maxspeed:backward` 217 · `maxspeed:conditional` 92 · `maxspeed:variable` 82 · `check_date:maxspeed` 79 · `maxspeed:source` 23 · `zone:maxspeed` 19 · `maxspeed:lanes` 10 · warianty kierunkowe bus/hgv/trailer/type ≤ 12 każdy.

### Kierunkowe

- 283 drogi mają `maxspeed:forward` lub `:backward`. Tylko 4 mają też zwykłe `maxspeed`.
- **36 tylko `forward`, 22 tylko `backward`, bez `maxspeed`**: limit w drugim kierunku jest nieznany.
- **5 wartości implicit w tagach kierunkowych:** `PL:urban` (`osm_id` 907138970, 1362930293, 1446272667, 1491960205, 1491960207; residential/tertiary).
- Pozostałe wartości kierunkowe są liczbami (10–90, w tym 75).

### Warunkowe i zmienne

- `maxspeed:conditional`: 92 drogi, wszystkie `motorway`, jedna wartość `110 @ (Mo-Fr 06:00-18:00)`.
- `maxspeed:variable=yes`: 82 drogi, wszystkie `motorway`, wszystkie mają też `maxspeed`.
- Znaki 110 km/h: 55 (pasują do limitu warunkowego, nie do `maxspeed`).

### Źródło limitu (`source:maxspeed`) a wartość

| `source:maxspeed` | `maxspeed` (drogi) |
|---|---|
| PL:urban | 50 (4 214), NULL (58), **70 (55)**, 30 (22), 40 (17), 60 (5) |
| PL:zone30 | 30 (3 024), 20 (5), **50 (4)** |
| PL:living_street | 20 (951), 30 (9) |
| PL:rural | 90 (131), 100 (64), 70 (15), 50 (2), NULL (1) |
| PL:motorway | 140 (209) |
| PL:expressway | 120 (127), NULL (7), 80 (7), 100 (4) |

Inne wartości `source:maxspeed`: `sign` 2 090, `PL:zone20` 303, `PL:zone40` 257, `PL:zone10` 42, `zone30` 26, `PL:sign` 14, oraz pojedyncze nieregularne (`PL;zone40`, `PL:zone30;sign`, `PL:sign_B-33`, `PL:trunk`, `survey`, `PL:zone25`, `PL:zone`).

---

## 8. Braki

| Pole | Braki |
|---|---|
| `photos.heading` | 15 NULL (23 zdjęcia mają poprawne `0.0` = północ) |
| `photos.horizontal_accuracy_m` | 315 / 561 |
| `photos.captured_at`, `sequence`, `author`, `license` | 0 |
| `photo_road_links.osm_way_id` / `overture_id` / `distance_m` / `way_bearing` | 3 (zdjęcia bez drogi w 25 m) |
| `osm_roads.maxspeed` | 83 478 / 99 768 (84%) |
| **Drogi główne bez jakiegokolwiek limitu** (`maxspeed`, `:forward`, `:backward`) | **290**: secondary 125, secondary_link 38, motorway_link 37, trunk_link 33, primary 30, primary_link 17, motorway 6, trunk 4 |
| `osm_roads.name` / `oneway` | 80 639 / 87 180 |
| Overture `speed_limits` | 72 558 / 85 405 segmentów bez reguły |
| `traffic_signs` — znaki z `last_seen_at` < 2025-01-01 | **1 200 / 1 730 (69%)** |

`bridge`: `yes` 584, `viaduct` 195, `boardwalk` 1, `culvert` 1. **`tunnel`: `building_passage` 605, `yes` 221, `covered` 1.** `layer` ≠ 0: 1 207.

---

## 9. Integralność wejść

SHA-256 przed i po profilowaniu (identyczne):

```
b429a97aef4e9e9f6c9cf760081a215eeb14cdc97b678812730a318b0e61d32b  osm_roads.parquet
cf4ee780992cfab94eeda799ef6aa6ee67c86539d14aef572e937b35b8259760  overture_segments.parquet
9cc6b79f5fc147a774e43fb685db61b5bc3e2e2d15c38daa4cf665ba6e657e9c  photos.csv
d71d0857ae0ffc3eeae395a67d2f9ea2bcb869e1fab44c222d2755e60e70763b  photo_road_links.csv
fcae7cbea2b192c45616c957748630b0fa1943f713227e46baccfd99439f6435  traffic_signs.geojson
29133842006f84c90a94291d267fab95d58509c2721db097b213370ab9e7e50b  sources.json
```

---

## 10. Pięć faktów, które zmieniają projekt

> Stan 2026-11-12: wszystkie pięć faktów oraz odpowiedzi na pytania z §11 wprowadzono do `docs/PRD.md` (AC-17a, AC-17b, §12), `docs/mvp-scope.md` (§1.5, pozycja 5a) i ADR-000/001/002 (sekcje „Revision 2026-11-12”). Pytanie 3 (próbka precyzji) pozostaje otwarte.

1. **Geometria nie przychodzi jako WKB.** DuckDB 1.5.6 zwraca typ `GEOMETRY` z CRS nawet bez `spatial`. ADR-001 §4.4 zakłada odczyt WKB. Adapter musi jawnie konwertować geometrię (`ST_AsWKB` przy załadowanym `spatial`) albo wczytywać GeoParquet przez GeoPandas. Overture nie ma `crs` w metadanych, więc przyjmujemy OGC:CRS84 jawnie. W Databricks geometria jest BINARY WKB, więc oba adaptery dają różne typy, a test zgodności (AC-05b) musi to wyrównać.
2. **Reguła `bridge_or_tunnel` w ADR-002 dawałaby fałszywe przypadki.** 605 z 827 tagów `tunnel` to `building_passage` (przejazd pod budynkiem na poziomie ulicy). Reguła powinna objąć tylko `tunnel ∈ {yes, covered}` oraz `bridge ∈ {yes, viaduct}` albo użyć `layer ≠ 0`. Inaczej każdy znak przy bramie kamienicy trafi do `needs_review`.
3. **Zdjęcia pokrywają tylko 230 z 99 768 dróg.** Większość kandydatów będzie miała `photo_count = 0` („no photos linked”). Dowód zdjęciowy (AC-22) w Stage 1 jest wyjątkiem, a nie regułą. Ręczna próbka 30 kandydatów `sign_road_mismatch` (mvp-scope §4) wymaga innych zdjęć (Mapillary/Panoramax online) albo doboru kandydatów z pokryciem.
4. **69% znaków (1 200 / 1 730) widziano ostatnio przed 2025 r., najstarsze w 2014 r.** Pytanie otwarte nr 3 z PRD (stary znak kontra nowsza edycja OSM) dotyczy więc większości danych, a nie wyjątków. Bez reguły wieku znaku kolejka `sign_road` będzie zdominowana przez prawdopodobnie nieaktualne obserwacje.
5. **Interpretacja implicit i kierunków nie jest martwym kodem.** W tagach kierunkowych jest 5 wartości `PL:urban`, a 58 dróg ma limit tylko w jednym kierunku i bez `maxspeed`. Do tego 55 znaków 110 km/h odpowiada `maxspeed:conditional` na autostradzie. ADR-002 §3.3 musi jawnie definiować brakujący kierunek jako „nieznany”, a nie „brak limitu”. Tabela `PL:*` jest potrzebna już w Stage 1, a test zgodności liczności dla `unparseable_value` (0) pozostaje prawdziwy.

---

## 11. Trzy pytania do człowieka

1. **Wiek znaku:** czy znak z `last_seen_at` starszym niż `osm_timestamp` drogi (albo starszym niż N lat, np. 3) ma dawać `needs_review` z osobnym powodem (np. `stale_sign`), być wykluczony z kolejki, czy tylko pokazany jako dowód? Dotyczy ok. 69% znaków.
2. **Nowe tanie sprawdzenie:** 55 dróg ma `source:maxspeed=PL:urban` i `maxspeed=70`, a 4 drogi `PL:zone30` i `50`. To wewnętrzna sprzeczność tagów OSM, deterministyczna i bez zgadywania kierunku. Czy dodać ją do MVP jako typ kandydata (wymaga zmiany PRD), czy zostawić na później?
3. **Zdjęcia do próbki precyzji:** przy 230 drogach ze zdjęciem czy ręczną próbkę 30 kandydatów robimy na kandydatach z `photo_count > 0` (ryzyko obciążenia próbki), czy analityk przegląda zdjęcia online w Mapillary/Panoramax przez `source_url` (wolniej, ale reprezentatywnie)?
