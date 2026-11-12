# mapqa — wyszukiwarka konfliktów ograniczeń prędkości (Kraków, Stage 1)

Narzędzie CLI w Pythonie, które porównuje ograniczenia prędkości w OSM ze znakami Mapillary i ze spójnością tagów OSM, a następnie tworzy uporządkowaną **kolejkę kandydatów do ręcznego przeglądu** oraz przeglądarkę wyników (mapa + tabela). Narzędzie nigdy nie twierdzi, że mapa jest błędna — wskazuje miejsca do sprawdzenia.

Specyfikacja: [`docs/PRD.md`](../docs/PRD.md), [`docs/mvp-scope.md`](../docs/mvp-scope.md), [`docs/ADR/`](../docs/ADR/), profil danych [`reports/map-qa/profile.md`](../reports/map-qa/profile.md), plan [`docs/implementation-plan.md`](../docs/implementation-plan.md).

## Wymagania

- Windows / macOS / Linux, [uv](https://docs.astral.sh/uv/) (instaluje Python 3.12 dla projektu; systemowy Python nie jest używany).
- Rozszerzenie DuckDB `spatial` — instaluje się jednorazowo przy pierwszym użyciu z dostępem do internetu.
- Przeglądarka Chrome lub Edge do `viewer.html` (mapa wymaga internetu do kafelków i biblioteki Leaflet; tabela i szczegóły działają offline).

## Szybki start (z katalogu `app/`)

```powershell
uv sync                         # zależności z uv.lock
uv run mapqa check-source       # czy wszystkie 6 plików wejściowych jest dostępnych
uv run mapqa run                # pełny przebieg (~1 min), wyniki w reports/map-qa/stage1/
uv run mapqa view               # otwiera viewer.html w przeglądarce
```

## Polecenia

| Polecenie | Co robi |
|---|---|
| `mapqa run [--config] [--source] [--data-dir] [--output-dir] [--distance-m]` | Uruchamia wszystkie sprawdzenia i zapisuje wyniki atomowo (przy błędzie stare wyniki zostają nietknięte). |
| `mapqa check-source [--config] [--source] [--data-dir]` | Sprawdza źródło i wymagane kolumny bez uruchamiania sprawdzeń. |
| `mapqa view [--output-dir] [--no-open]` | Odtwarza `viewer.html` z ostatniego przebiegu i otwiera go. |

Źródła danych: `files` (zaimplementowane), `databricks` i `public_api` (rozpoznawane, kończą się kodem 4 „not available yet”). Kolejność ustawień: opcja CLI → zmienna `MAPQA_DATA_SOURCE` / `MAPQA_CONFIG` → plik TOML → domyślne. Przykład konfiguracji: [`mapqa.example.toml`](mapqa.example.toml) (skopiuj do `app/mapqa.toml`, plik jest ignorowany przez git). Sekrety tylko w zmiennych środowiskowych — klucz wyglądający na sekret w TOML kończy się kodem 2.

**Kody wyjścia:** 0 sukces · 1 błąd wewnętrzny · 2 konfiguracja/opcje · 3 brak lub nieczytelne wejście (albo brak wyników dla `view`) · 4 źródło niedostępne.

## Wyniki (`reports/map-qa/stage1/`)

| Plik | Dla kogo | Zawartość |
|---|---|---|
| `review_queue.csv` | analityk, arkusz | kandydaci w kolejności rangi, wartości surowe i zinterpretowane, dystans, daty znaku, zdjęcia |
| `review_queue.geojson` | QGIS | to samo z geometrią (punkt znaku lub linia drogi), EPSG:4326 |
| `viewer.html` | analityk | mapa + filtrowana tabela + szczegóły kandydata (droga OSM porównana ze znakiem, zdjęcia, linki) |
| `summary.md` | lider zespołu | liczności wg typu × stanu, powodu, klasy drogi, pominięte rekordy, ustawienia, atrybucja |
| `run_summary.json` | agent / automatyzacja | źródło, konfiguracja, liczności wejść, SHA-256 przed/po, liczności wyników |

Kolejka i GeoJSON są deterministyczne: dwa przebiegi na tych samych danych dają identyczne bajty.

## Sprawdzenia (ADR-002)

| Typ | Stan | Co wykrywa |
|---|---|---|
| `missing_limit` | conflict | droga główna (motorway…secondary + łączniki) bez żadnego `maxspeed*` |
| `tag_inconsistency` | conflict | `source:maxspeed` / `maxspeed:type` (np. `PL:urban`) nie dopuszcza wartości `maxspeed` |
| `sign_road` | needs_review | znak Mapillary w promieniu 20 m od drogi z inną wartością; powody: `multiple_candidate_roads`, `bridge_or_tunnel`, `variable_limit`, `conditional_limit`, `stale_sign`, `unparseable_value`, `road_limit_missing`, `direction_unknown`, `sign_road_mismatch` |
| `unparseable_value` | needs_review | wartość `maxspeed*`, której nie da się zinterpretować |

Wynik na przypiętym snapshocie (2026-11-12): 1896 kandydatów — 290 `missing_limit`, 161 `tag_inconsistency`, 1445 `sign_road` (w tym 1205 niejednoznacznych, 182 `stale_sign`).

## Testy

```powershell
uv run pytest -m "not integration"   # testy jednostkowe (~1 s)
uv run pytest                        # wszystko, w tym prawdziwe CLI na pełnym snapshocie (~2,5 min)
uv run python tests/fixtures/build_fixtures.py   # odtworzenie fixtures z prawdziwych danych
```

Licencje danych i atrybucja: [`course-materials/exercise-data/map-qa/ATTRIBUTION.md`](../course-materials/exercise-data/map-qa/ATTRIBUTION.md).
