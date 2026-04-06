# spec_ch1.md — Data Ingestion Library & Metrics Service

**Project name:** **sensor_app** (Python package and documentation name; distribution name may be `sensor-app` in packaging metadata).

Defines the core ingestion library, metrics API, and project setup. Optional extensions: **spec_ch2.md**, **spec_ch3.md**.

## 0. Project setup (prerequisite)

Complete the following before the rest of this specification is considered done.

* **Dependency management**: Reproducible installs and lockfiles; use **uv** as the package manager and a **Python 3.11** virtual environment.
* **Project structure**: Layout under **`src/sensor_app/`** with **module boundaries** separating **library** (`sensor_app.lib`, no web framework imports) and **API** (`sensor_app.api`, thin FastAPI layer).
* **Code quality**: **Linting**, **formatting**, and **type checking** configured and enforced (for example in CI and/or pre-commit).
* **Testing**: A **test suite** that covers core library and API behavior.
* **Automation**: One-command (or few-command) ways to run **lint**, **typecheck**, and **test** (for example a Makefile or `uv run` scripts).
* **Continuous integration**: A workflow (for example **GitHub Actions**) that runs format check, lint, typecheck, and tests on each push/PR.
* **Git**: A repository whose **commit history** shows incremental work.
* **`README.md`**: Enough detail that someone can **clone, install, and run tests** without guessing.

## 0.1 Runtime model (async and parallelism)

* **HTTP service**: Use **FastAPI** with **`async def`** route handlers so the server stays non-blocking under concurrent requests.
* **CPU-bound library work**: Run ingestion, validation, resampling, and metric computation **off the event loop** (for example **`asyncio.to_thread`**) so file/SQLite I/O and **Polars**/**NumPy** work do not block other requests. (This repo uses **Polars** for columnar transforms; **NumPy** is used for small flatline scans.)
* **Parallelism**: Where independent per-device or per-chunk work exists and profiling shows benefit, **parallelize inside the worker** (for example **`concurrent.futures.ThreadPoolExecutor`**). **Do not** claim asyncio parallelism for CPU-bound dataframe work without offloading.
* **SQLite note**: Use a **single-writer** discipline for metrics persistence; keep transactions short. Document that **production** would likely use PostgreSQL or a warehouse with a different concurrency story.
* **HTTP cross-cutting (recommended)**: **Correlation** via **`X-Request-ID`** (client-supplied or generated) echoed on responses and attached to structured logs; **basic rate limiting** on expensive routes (in-memory acceptable for demos; Redis or gateway in production).

## 1. Overview

The deliverable is **production-quality Python** for an **industrial compressed air** platform:

1. A **reusable data ingestion and transformation library** — an **importable package** (`sensor_app.lib`), not a standalone script, **with no FastAPI imports**.
2. A **metrics service** (API) that **only orchestrates**: it stays a **thin wrapper** and delegates heavy work to the library.

The **`README.md` must** record **assumptions**, **scope**, and **planned next steps** so the submission stands on its own.

## 2. Provided resources

The implementation **must** use these repository artifacts:

* **`sensor_data.db`**: SQLite with compressor time series and **station metadata**. Readings use the same logical fields as **`sensor_schema.json`** (see below). Expect missing values, gaps, flatlines, and noise. (In production this would be a warehouse such as BigQuery or PostgreSQL; the library design should not hard-code SQLite forever.)
* **`sensor_schema.json`**: Column expectations, types, and valid ranges. The canonical reading columns in this repo are: **`timestamp`**, **`station_id`**, **`device_id`**, **`discharge_pressure`** (bar), **`air_flow_rate`** (m³/h), **`power_consumption`** (kW), **`motor_speed`** (RPM), **`discharge_temp`** (°C), plus **station metadata** as defined in the JSON.

## 3. Data ingestion and transformation library (`sensor_app.lib`)

The library **must** be **usable on its own** and **must not** depend on a web framework.

The **data access layer must** be abstracted so the backing store could change (for example SQLite → BigQuery) **without** breaking callers that use the public library API.

### 3.1 Required behavior

* **Read** sensor data from the SQLite database through that abstraction.
* **Validate** rows against `sensor_schema.json`: columns present, types correct, values in range.
* **Handle missing and malformed data** with **configurable strategies** (at minimum support ideas in the same family as **drop**, **forward-fill**, **interpolate**, and **document any extra strategies** implemented).
* **Resample** time series to a **configurable** frequency (pandas-style offset aliases mapped to Polars durations, or equivalent).
* **Return** clean, structured output suitable for downstream code (for example a **Polars** `DataFrame` or typed records).
* **Produce a data-quality report** for each run: at least **missing share per column**, **out-of-range counts**, **flatline detection** (use **`sensor_types.*.flatline_threshold_minutes`** from the schema where applicable), and **any other quality signals** that fit this dataset.

## 4. Metrics service (`sensor_app.api`)

Expose an HTTP API that **uses the library** to compute metrics and persist results.

### 4.1 Required endpoints

The API **must** provide all of the following **capabilities** (exact paths are allowed to differ if documented):

1. **Trigger processing** for a station: load data through the library, compute metrics, **persist** results.
2. **Read back** stored metrics for a station, with **optional filters** (for example time range and device).
3. **Health check** suitable for process monitoring.

A concrete shape that satisfies (1)–(3) is:

* `POST /process/{station_id}` — run the pipeline for one station and store outputs.
* `GET /metrics/{station_id}` — return stored metrics; support query parameters such as `start_time`, `end_time`, `device_id` if applicable.
* `GET /health` — liveness and/or readiness as appropriate.

An optional **`GET /`** index that documents the above is allowed.

**Application entrypoint:** `uvicorn sensor_app.api.main:app` with **`src`** on **`PYTHONPATH`** (or an editable install; for example `--app-dir src`).

### 4.2 Required metrics

Compute and expose **all** of the following **baseline** metrics (map from schema column names: pressure → **`discharge_pressure`**, flow → **`air_flow_rate`**, power → **`power_consumption`**, speed → **`motor_speed`**, temperature → **`discharge_temp`**):

* Uptime / active time per device  
* Average and peak pressure  
* Specific power (power per unit flow)  
* Cycle counts (on/off transitions)  
* Total flow volume over a period  

**Also** define **two or three additional** metrics, implement them, and **document every metric** (baseline + extra) in **`README.md`**, including how the service computes and stores them.

## 5. Testing

* **Tests must** run the **library without** going through the HTTP API where core logic is concerned.
* **Tests must** separate **fast isolated** tests from tests that need a **real database file**, containers, or other external pieces, and **document** how to run each kind.
* **Coverage must** include validation rules, transformation strategies, and API contracts for the required endpoints.

## 6. Design document (1 page max)

The repository **must** include a short design write-up (**at most one page**) that **answers all** of the following:

1. How this library would be **versioned and distributed** across **25+ services** in a **monorepo**.
2. How **schema evolution** (new sensor types, renamed columns, unit changes) would be handled **without breaking** existing consumers.
3. What **CI/CD** would look like for this service (**build, test, deploy**).

## 7. Cross-cutting production practices

These items **extend** the core requirements above. **They still bind the implementer as follows:**

* Anything implemented in code **must** match what the README and design doc say.
* Anything **not** implemented in code **must** still be **addressed in prose** in the README and/or design doc (what you would do in production and what you skipped here).

Apply the list below accordingly:

* **Observability** — **Structured logging** (stable fields, correlation or request IDs), **service and domain metrics** (latency, errors, pipeline duration, rows processed, data-quality counts), and **distributed tracing** where work crosses components or async boundaries.
* **SLOs and alerting** — Define **service level objectives** (for example API availability, processing latency, data freshness). Describe **what would be alerted** (error spikes, queue depth, failed jobs, SLO burn).
* **Secrets and configuration** — **No secrets in git**. Load secrets from environment, a secret manager, or mounted files. Keep **configuration** (database URLs, flags, tuning) out of code; validate at startup; **list required environment variables** in the README.
* **Security** — **Authentication and authorization** on operational endpoints where reasonable for this project. **Least privilege** for database credentials. **Validate inputs**; safe defaults; rate limiting and audit logging where appropriate.
* **Idempotency** — Repeating **process** requests for the same station and logical window **must not corrupt** stored metrics (use deterministic writes, versioning, idempotency keys, or equivalent). Deployments **must** remain safe with health checks; migrations **must** be backward compatible or rolled out in phases when needed.
* **Capacity and cost** — Explain in writing **read/write cost**, full scans versus incremental work, expected API load, and warehouse cost drivers. **Document** what you would watch in production.
* **Data retention** — **Document** retention for raw readings versus aggregates, archival, and how reprocessing interacts with retention.
* **Disaster recovery** — **Document** RPO/RTO-style thinking, backups (SQLite for this repo versus managed DB later), multi-AZ or multi-region stance, and how you would **restore** and **verify** metric correctness.

## 8. Relationship to other spec files

**spec_ch1.md** is **required** baseline work for this repository’s sensor pipeline and metrics API. **spec_ch2.md** and **spec_ch3.md** are optional addenda; use them only when those features are in scope for the product.
