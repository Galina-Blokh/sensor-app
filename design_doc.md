# Design note (sensor_app)

## 1. Library versioning in a monorepo

Publish **`sensor_app.lib`** as an internal package (Poetry/uv workspace or `hatch` monorepo). Use **semantic versioning** and **changelog** per release. Consumers pin `sensor-app-lib~=x.y` in `pyproject.toml`. Breaking schema or public API changes require a **major** bump; additive columns and optional fields are **minor**. CI runs compatibility tests against a **consumer matrix** (pinned ranges) before tagging.

## 2. Schema evolution

Treat **`sensor_schema.json`** as a **versioned contract** (`version` field). Support **additive** changes (new optional columns, new sensor types) without breaking old readers. For **renames**, ship a **mapping layer** (alias table or transform rules) for N releases, then deprecate. For **unit changes**, store **canonical SI** in the pipeline and expose **display units** via metadata. Unknown columns are ignored or quarantined based on a **strict mode** flag so existing services keep stable behavior.

## 3. CI/CD (build, test, deploy)

**In-repo CI:** `.github/workflows/ci.yml` at the repository root (inside the hidden **`.github/`** folder) — checkout, **uv** sync, `ruff format --check`, `ruff check`, `mypy`, `pytest` on Ubuntu. Local equivalent: `make ci` (no format writes).

**Broader CD:** build **OCI image** on main; push to registry; deploy to **Kubernetes** (or similar) with **rolling updates**, **readiness** on `/health`, and **DB migrations** as a separate job. **Secrets** from the environment or secret manager; platform scrapes **metrics** and **logs**. **Rollback** via previous image tag.

## 4. API hardening in this submission

- **Request IDs:** `RequestIdMiddleware` sets/propagates **`X-Request-ID`** and binds a **`contextvars.ContextVar`** so `sensor_app` logs can include stable **`request_id`** fields.
- **Rate limiting:** **slowapi** with per-route limits from **`SENSOR_APP_RATE_LIMIT_*`** (in-memory store). Production would move limits to **Redis** or an **API gateway** and add auth.
- **Security / observability not fully built here:** no JWT on operational routes yet; no Prometheus exporter yet — see README “Deferred / production”.

## 5. Production vs this repo (brief)

This submission uses **SQLite** and **thread-offloaded Polars/NumPy** for clarity. Production would move reads to **BigQuery/Postgres**, use **connection pooling**, **short transactions** for metric snapshots, and **horizontal scaling** of stateless API pods behind a load balancer with **WAF/rate limits** at the edge.

## 6. spec_ch1 §7 cross-cutting (summary)

| Area | In code | In prose |
|------|---------|----------|
| Observability | Request ID header + log field; startup log | Full tracing/metrics deferred |
| SLOs / alerting | — | Would alert on 5xx, latency p99, failed pipelines |
| Secrets / config | pydantic-settings, env-only paths | No secrets in git |
| Security | Input validation, rate limits | Auth deferred |
| Idempotency | Snapshot upsert by station + window | — |
| Capacity / cost | — | Documented: full-window scans costly; incremental in prod |
| Retention / DR | — | Raw vs aggregate retention; backup/restore for metrics DB |

## 7. spec_ch2 — LLM integration (brief)

**Architecture:** `sensor_app.llm` holds an **OpenAI-compatible** chat client (`httpx.AsyncClient`, timeouts, retries with backoff). The HTTP shape is **`POST {base}/v1/chat/completions`** (same as OpenAI’s API), so **any** compatible host works (examples: **OpenAI**, **Groq**, self-hosted stacks). FastAPI routes under **`POST /llm/*`** call that layer; they do **not** embed vendor SDKs in handlers. **`PrimaryWithFallbackBackend`** chains a second host after **transient** primary failure.

**Data access:** Snapshots load only through **`MetricsStore`** (parameterized SQL). NL **query** uses a model-produced **JSON plan** validated against allowlisted metric keys and aggregations; **numeric answers** come from Python over stored JSON, not SQL built from free text.

**Failure modes:** LLM disabled or missing key → **503**; provider/parse failures → **502**. **Fallback:** `SENSOR_APP_LLM_FALLBACK_*` wires a second base URL + model (e.g. **Groq** after **OpenAI**); it is **on by default** when URL+model are set—primary is always tried first, then one secondary call after transient primary failure (see README). **Cost / abuse:** input length caps, per-route rate limits, no full prompt logging (see README).

**Tests:** `tests/test_llm.py` and `tests/test_llm_units.py` mock backends and exercise retries/fallback logic (no live API in CI). **Configuration:** README + **`.env.example`** (`SENSOR_APP_LLM_*`).
