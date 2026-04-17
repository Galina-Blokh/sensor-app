# sensor_app

Industrial compressed-air **sensor ingestion** (`sensor_app.lib`) and a **metrics HTTP API** (`sensor_app.api`) built with **FastAPI**. Ingestion and metrics use **Polars** (and **NumPy** for flatline run scans in the pipeline); CPU-heavy work runs in a **thread pool** via `asyncio.to_thread` so the event loop stays responsive.

See **spec_ch1.md** for the full product spec; **design_doc.md** for monorepo distribution, schema evolution, CI/CD, and production deferrals.

## Project structure

```text
.
├── src/sensor_app/
│   ├── lib/                    # Framework-free ingestion + metrics math
│   │   ├── pipeline.py         # load → validate → impute → resample → DQ → metrics JSON
│   │   ├── metrics_compute.py  # per-device operational metrics (Polars)
│   │   ├── metrics_store.py    # SQLite persistence for snapshots
│   │   ├── repository.py       # readings source (SQLite impl + protocol)
│   │   └── schema_defs.py      # sensor_schema.json loader
│   ├── api/
│   │   ├── main.py             # FastAPI app, routes, slowapi limits
│   │   └── middleware.py       # X-Request-ID + logging filter
│   └── settings.py             # pydantic-settings (SENSOR_APP_*)
├── tests/                      # unit + integration (see Tests)
├── .github/workflows/ci.yml    # GitHub Actions
├── pyproject.toml / uv.lock
├── Makefile
├── design_doc.md               # “take-home” design answers + production notes
├── spec_ch1.md                 # internal product spec for Challenge 1
└── sensor_schema.json          # committed; sensor_data.db is local only (see below)
```

## System design (brief)

1. **Library (`sensor_app.lib`)** — Single entrypoint `run_station_pipeline(...)`: reads via `SensorReadingSource`, validates against `LoadedSchema`, applies missing-data strategy, resamples, emits a **data-quality** report, computes **metrics** in Polars. No HTTP imports; callers can be batch jobs or the API.
2. **API (`sensor_app.api`)** — Thin FastAPI layer: **`async def`** handlers call **`asyncio.to_thread`** for the pipeline and for SQLite metric writes so the event loop is not blocked. **slowapi** enforces per-route limits; **RequestIdMiddleware** adds **`X-Request-ID`** and log correlation.
3. **Persistence** — Raw readings: **`sensor_data.db`**. Computed snapshots: **`metrics.db`** table **`metric_snapshots`** (upsert by station + window for idempotency).
4. **Broader “system” and ops** — Versioning, schema evolution, full CD, auth, tracing, and SLOs are described in **design_doc.md** and the README **Scope & next steps** section.

## Prerequisites

- Python **3.11+**
- **uv** (recommended)

## Setup

```bash
cd sensor_app   # repository root (folder may still be named censor-app on disk)
uv sync --all-groups
source .venv/bin/activate   # Linux / macOS / Git Bash
```

### Readings database (`sensor_data.db`)

The repo ships **`sensor_data.db`** (~111 MB). **GitHub rejects files over 100 MB**, so this file is **not committed**. Copy it from your **bundle** into the repository root (next to `sensor_schema.json`) before running integration tests or the API against real data. **`sensor_schema.json`** is in the repo. CI runs **unit tests** and **skips** integration when the DB is missing.

## Run checks (local)

```bash
make check          # format (writes), lint --fix, mypy, pytest
# or match CI without reformatting:
make ci
```

**Continuous integration (YAML):** the workflow file is **`.github/workflows/ci.yml`** at the repository root (relative path). The **`.github`** directory is hidden on some systems (dot-folder); enable “hidden files” in your file explorer or open it directly in the editor by path. It runs on `push` / `pull_request` to `main` or `master`: `uv sync`, `ruff format --check`, `ruff check`, `mypy`, `pytest`. Integration tests **skip** if `sensor_data.db` is absent.

## Run the API

```bash
make run
# or: uv run uvicorn sensor_app.api.main:app --reload --app-dir src
# alternate port: make run PORT=8001
```

- Index: `GET /` — short JSON map of routes (optional convenience).
- OpenAPI: `http://127.0.0.1:8000/docs`
- Health: `GET /health`
- Process station: `POST /process/{station_id}?start_time=&end_time=` (ISO-8601, optional)
- Read snapshots: `GET /metrics/{station_id}?start_time=&end_time=&device_id=`

### Request correlation

- Every response includes **`X-Request-ID`**. Send your own UUID (or trace id) in **`X-Request-ID`** to correlate with upstream systems; otherwise the server generates one.
- Application logs under the `sensor_app` logger include **`request_id=...`** when a request is active (see `sensor_app.api.middleware`).

### Rate limiting

Per-client limits use **slowapi** (in-memory; production would use Redis or a gateway). Tune with **`SENSOR_APP_RATE_LIMIT_*`** (see the configuration table below). Exceeded limits return **429** with slowapi’s JSON body.

## Configuration (environment)

| Variable | Meaning | Default |
|----------|---------|---------|
| `SENSOR_APP_SENSOR_DB_PATH` | SQLite readings DB | `sensor_data.db` |
| `SENSOR_APP_METRICS_DB_PATH` | SQLite metrics snapshots | `metrics.db` |
| `SENSOR_APP_SCHEMA_PATH` | JSON schema path | `sensor_schema.json` |
| `SENSOR_APP_DEFAULT_RESAMPLE_RULE` | Offset alias (`5min` → Polars `5m`) | `5min` |
| `SENSOR_APP_MISSING_DATA_STRATEGY` | `drop` \| `ffill` \| `interpolate` | `interpolate` |
| `SENSOR_APP_POWER_ON_MOTOR_THRESHOLD_RPM` | Motor “on” threshold for uptime/cycles | `1.0` |
| `SENSOR_APP_POWER_ON_POWER_THRESHOLD_KW` | Power “on” threshold (kW) | `0.5` |
| `SENSOR_APP_FLOW_EPSILON_M3H` | Minimum absolute flow (m³/h) for specific-power mean | `0.001` |
| `SENSOR_APP_RATE_LIMIT_ROOT` | slowapi limit for `GET /` | `120/minute` |
| `SENSOR_APP_RATE_LIMIT_HEALTH` | Limit for `GET /health` | `120/minute` |
| `SENSOR_APP_RATE_LIMIT_PROCESS` | Limit for `POST /process/...` | `30/minute` |
| `SENSOR_APP_RATE_LIMIT_METRICS` | Limit for `GET /metrics/...` | `60/minute` |

## Repository artifacts

- **`sensor_data.db`** — readings (`discharge_pressure`, …) and `station_metadata` (**obtain locally** from the assignment; **gitignored**).
- **`sensor_schema.json`** — validation ranges and flatline thresholds (**in repo**).

## Metric definitions

**Baseline (per device, over the processed window after clean + resample):**

1. **uptime_seconds** — Sum of intervals where both endpoints are “on” (`motor_speed` > threshold or `power_consumption` > threshold).
2. **average_pressure_bar** — Mean `discharge_pressure`.
3. **peak_pressure_bar** — Max `discharge_pressure`.
4. **specific_power_kw_per_m3h** — Mean of `power_consumption / air_flow_rate` where flow > ε.
5. **cycle_count** — Off → on transitions using the same on/off rule.
6. **total_flow_volume_m3** — Trapezoidal integration of flow (m³/h) × Δt (hours).

**Additional (same per-device payload):**

7. **thermal_efficiency_temp_per_kw** — Mean temperature / mean power while power > threshold (unitless ratio for this exercise).
8. **pressure_std_bar** — Standard deviation of discharge pressure (stability signal).
9. **data_quality_score** (also in `metrics.extras`) — 0–100 heuristic from missing share and out-of-range rate after validation.

Snapshots are stored in **`metric_snapshots`** (see `sensor_app.lib.metrics_store`). Re-running `POST /process` for the same `station_id` and time window **replaces** the prior row (idempotent for that key).

## Tests

- **Unit** (no real DB): `tests/test_schema_defs.py`, `tests/test_metrics_compute.py`
- **Integration** (requires `sensor_data.db`): `tests/test_pipeline_integration.py`, `tests/test_api.py`

```bash
uv run pytest tests/test_schema_defs.py tests/test_metrics_compute.py   # unit only
uv run pytest                                                           # all
```

## Scope & next steps

- **In scope (this repo):** spec_ch1 — Polars pipeline library, FastAPI metrics API, SQLite metrics store, `X-Request-ID` + structured log fields, per-route rate limits, tests, design note, GitHub Actions CI.
- **Deferred / production (see design_doc.md):** OAuth2/JWT or mTLS on `/process` and `/metrics`; Redis-backed rate limits; Prometheus/OpenTelemetry (latency, error rate, pipeline duration); centralized JSON logging; HA Postgres/BigQuery instead of SQLite; multi-region DR and formal SLOs.
