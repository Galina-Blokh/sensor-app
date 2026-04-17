# sensor_app

Industrial compressed-air **sensor ingestion** (`sensor_app.lib`) and a **metrics HTTP API** (`sensor_app.api`) built with **FastAPI**. Ingestion and metrics use **Polars** (and **NumPy** for flatline run scans in the pipeline); CPU-heavy work runs in a **thread pool** via `asyncio.to_thread` so the event loop stays responsive.

See **spec_ch1.md** for the full product spec; **spec_ch2.md** for the LLM integration; **design_doc.md** for monorepo distribution, schema evolution, CI/CD, and production deferrals.

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
│   ├── llm/                    # OpenAI-compatible client + NL features (spec_ch2)
│   └── settings.py             # pydantic-settings (SENSOR_APP_*)
├── tests/                      # unit + integration (see Tests)
├── .github/workflows/ci.yml    # GitHub Actions
├── pyproject.toml / uv.lock
├── .env.example               # LLM/provider template (copy to .env; .env is gitignored)
├── docs/
│   └── api/
│       └── README.md           # per-route API guide (add demo videos per path)
├── Makefile
├── design_doc.md               # “take-home” design answers + production notes
├── spec_ch1.md                 # internal product spec for Challenge 1
├── spec_ch2.md                 # LLM endpoints + ops (Challenge 2)
└── sensor_schema.json          # committed; sensor_data.db is local only (see below)
```

## System design (brief)

1. **Library (`sensor_app.lib`)** — Single entrypoint `run_station_pipeline(...)`: reads via `SensorReadingSource`, validates against `LoadedSchema`, applies missing-data strategy, resamples, emits a **data-quality** report, computes **metrics** in Polars. No HTTP imports; callers can be batch jobs or the API.
2. **API (`sensor_app.api`)** — Thin FastAPI layer: **`async def`** handlers call **`asyncio.to_thread`** for the pipeline and for SQLite metric writes so the event loop is not blocked. **slowapi** enforces per-route limits; **RequestIdMiddleware** adds **`X-Request-ID`** and log correlation.
3. **Persistence** — Raw readings: **`sensor_data.db`**. Computed snapshots: **`metrics.db`** table **`metric_snapshots`** (upsert by station + window for idempotency).
4. **LLM layer (`sensor_app.llm`)** — **OpenAI-compatible** chat API (`POST …/chat/completions`) via **`httpx.AsyncClient`**, **`LLMBackend`** protocol, **`LLMFeatureService`**; optional **`PrimaryWithFallbackBackend`** (cloud + local or two hosts). **`POST /llm/*`** uses **`MetricsStore`** only; NL **query** uses a validated JSON plan plus **Python** aggregation (see **spec_ch2.md** and **LLM endpoints** below).
5. **Broader “system” and ops** — Versioning, schema evolution, full CD, auth, tracing, and SLOs are described in **design_doc.md** and the README **Scope & next steps** section.

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

**Per-route guide (add your demo videos there):** **[docs/api/README.md](docs/api/README.md)** — purpose, when to use each LLM route, examples, and a **Video** placeholder under every path. **OpenAPI / try-it-out:** `http://127.0.0.1:8000/docs`.

### LLM layer (spec_ch2)

NL features use **`sensor_app.llm`** (`httpx.AsyncClient`, OpenAI-compatible **`POST …/chat/completions`**). **`MetricsStore`** only via parameterized SQL; **`/llm/query`** uses a validated JSON plan and **Python** aggregation (see **docs/api/README.md** and **spec_ch2.md**).

**When the LLM is off or misconfigured:** routes return **503** with a short detail (set env vars below). **Provider failures** (timeout, HTTP errors, bad response shape) return **502**.

**Enable LLM routes:** `SENSOR_APP_LLM_ENABLED=true` and **`SENSOR_APP_LLM_API_KEY`**. Set **`SENSOR_APP_LLM_BASE_URL`** and **`SENSOR_APP_LLM_MODEL`** to match your host (see **Providers** below). **Retries** with exponential backoff apply to **429** and **5xx**. Logs record **latency** and token usage when the API returns `usage`; full prompts are **not** logged (only sizes / outcomes).

**Providers (all OpenAI-compatible `…/v1/chat/completions`):**

| Provider | Typical `SENSOR_APP_LLM_BASE_URL` | Models (examples; check host docs) |
|----------|-----------------------------------|-------------------------------------|
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o-mini`, `gpt-4o`, … |
| **Groq** | `https://api.groq.com/openai/v1` | `llama-3.1-8b-instant`, `llama-3.3-70b-versatile`, … ([console](https://console.groq.com/)) |
| **Self-hosted** (vLLM, etc.) | `http://127.0.0.1:<port>/v1` (per your stack) | Model id your server exposes |

The app does **not** download weights; you run the host and choose a **model id** it accepts.

**Secondary / fallback (automatic when configured):** By default **`SENSOR_APP_LLM_FALLBACK_ENABLED`** is **`true`**. If **`SENSOR_APP_LLM_FALLBACK_BASE_URL`** and **`SENSOR_APP_LLM_FALLBACK_MODEL`** are both non-empty, the app chains **primary → secondary** without any extra flag: every request tries the **primary** (e.g. OpenAI GPT) first; only after the primary exhausts retries on a **transient** failure (**timeout**, **5xx**, **429**, transport errors) does it call the **secondary** once (e.g. Groq Llama). Set **`SENSOR_APP_LLM_FALLBACK_ENABLED=false`** to force primary-only even when fallback URL/model are set. **401 / 403 / 400 / 404 / 422** do **not** fall back. Log line **`llm_fallback_invoked`** marks the handoff. Empty **`SENSOR_APP_LLM_FALLBACK_API_KEY`** reuses the **primary** key (set a separate key when the secondary is another vendor). JSON responses include **`model`**: the id of the host that actually produced the completion.

**Cost control (production):** this build relies on **timeouts**, **rate limits**, and **bounded context** (`SENSOR_APP_LLM_MAX_CONTEXT_CHARS`). For heavy traffic, add **caching** keyed by `(snapshot_id, endpoint, prompt hash)` or an async job queue; not implemented here.

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
| `SENSOR_APP_LLM_ENABLED` | Turn on HTTP LLM client for `/llm/*` | `false` |
| `SENSOR_APP_LLM_API_KEY` | Bearer token (required if enabled) | *(empty)* |
| `SENSOR_APP_LLM_BASE_URL` | OpenAI-compatible API base (OpenAI, Groq, self-hosted, …) | `https://api.openai.com/v1` |
| `SENSOR_APP_LLM_MODEL` | Model id for that host | `gpt-4o-mini` |
| `SENSOR_APP_LLM_TIMEOUT_SECONDS` | Per-request timeout | `60` |
| `SENSOR_APP_LLM_MAX_RETRIES` | Retries after first attempt (429 / 5xx) | `3` |
| `SENSOR_APP_LLM_BACKOFF_BASE_MS` | Initial backoff before retry | `400` |
| `SENSOR_APP_LLM_MAX_QUESTION_CHARS` | Max `question` length for `/llm/query` | `2000` |
| `SENSOR_APP_LLM_MAX_CONTEXT_CHARS` | Max JSON sent to the model (truncated) | `80000` |
| `SENSOR_APP_RATE_LIMIT_LLM_SUMMARY` | Limit for `POST /llm/metrics-summary` | `30/minute` |
| `SENSOR_APP_RATE_LIMIT_LLM_QUERY` | Limit for `POST /llm/query` | `30/minute` |
| `SENSOR_APP_RATE_LIMIT_LLM_DQ` | Limit for `POST /llm/data-quality-summary` | `30/minute` |
| `SENSOR_APP_LLM_FALLBACK_ENABLED` | Use secondary API after primary **transient** failure (when URL+model set) | `true` |
| `SENSOR_APP_LLM_FALLBACK_BASE_URL` | Secondary base (another vendor or self-hosted `…/v1`) | *(empty)* |
| `SENSOR_APP_LLM_FALLBACK_API_KEY` | Secondary bearer; empty → reuse primary key | *(empty)* |
| `SENSOR_APP_LLM_FALLBACK_MODEL` | Secondary model id for that host | *(empty)* |

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
- **LLM wiring** (no API key; **mocked** backend + HTTP/retry/fallback units): `tests/test_llm.py`, `tests/test_llm_units.py`
- **Integration** (requires `sensor_data.db`): `tests/test_pipeline_integration.py`, `tests/test_api.py`

There is **no separate “LLM credentials” suite** to skip: `tests/test_llm.py` does not call a real provider. Enable `SENSOR_APP_LLM_*` only when exercising `/llm/*` manually against a live API.

```bash
uv run pytest tests/test_schema_defs.py tests/test_metrics_compute.py   # unit only
uv run pytest tests/test_llm.py tests/test_llm_units.py tests/test_api.py -q --no-cov  # API + LLM
uv run pytest                                                           # all
```

## Scope & next steps

- **In scope (this repo):** spec_ch1 — Polars pipeline library, FastAPI metrics API, SQLite metrics store, `X-Request-ID` + structured log fields, per-route rate limits, tests, design note, GitHub Actions CI. **spec_ch2** — LLM routes, `sensor_app.llm` OpenAI-compatible client (timeouts/retries; **primary→fallback** when fallback URL+model are set, default-on), structured NL query execution, mocked tests. **`.env.example`** — copy to `.env`; LLM primary + fallback vars (never commit real `.env`).
- **Deferred / production (see design_doc.md):** OAuth2/JWT or mTLS on `/process` and `/metrics`; Redis-backed rate limits; Prometheus/OpenTelemetry (latency, error rate, pipeline duration); centralized JSON logging; HA Postgres/BigQuery instead of SQLite; multi-region DR and formal SLOs.
