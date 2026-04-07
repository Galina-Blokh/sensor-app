# spec_ch2.md — LLM-powered features

**Project name:** **sensor_app** (extends the same service as **spec_ch1.md**).

This document is the internal product spec for the **LLM integration** on top of **spec_ch1.md** (metrics pipeline, `metric_snapshots`, and the core HTTP API).

---

## 0.1 Runtime model (async)

* LLM HTTP calls use **`httpx.AsyncClient`** from **`async def`** route handlers so the event loop is not blocked while waiting on the provider.
* **Timeouts**, **retries**, and **exponential backoff** apply to transient failures (e.g. **429**, **5xx**, timeouts, transport errors). Failures surface as **HTTP errors** (see §7), not hung connections.

---

## 1. Scope

The metrics service gains a **dedicated** `sensor_app.llm` package: **OpenAI-compatible** `POST …/v1/chat/completions`, **`LLMBackend`** protocol, **`LLMFeatureService`**, and optional **`PrimaryWithFallbackBackend`** (second base URL + model after primary transient failure). Handlers stay thin; vendor details live in the LLM layer and **settings** (`SENSOR_APP_*`).

**No SQL** is built from user text or model output. LLM routes read stored data only through **`MetricsStore`** with **parameterized** queries.

---

## 2. Required features (three endpoints)

All three are implemented as **`POST /llm/*`** routes next to the spec_ch1 API.

### 2.1 Natural language metrics summary

* **Route:** `POST /llm/metrics-summary`
* **Input (JSON):** `station_id`; optional `snapshot_id`; optional `start_time` / `end_time` (ISO-8601, same window semantics as `GET /metrics` for choosing which stored row(s) to consider; default is newest in range).
* **Behavior:** Loads the resolved **`StoredSnapshot`**, builds a **bounded** JSON context of per-device metric scalars (see §4), calls the model once with a fixed system prompt, returns **plain-English operational** prose plus **`snapshot_id`** and **`model`** (completing host’s model id).
* **Intent:** Station health / performance narrative from **computed device metrics** (pressure, flow, uptime, etc.)—not arbitrary SQL.

### 2.2 Natural language query over stored metrics

* **Route:** `POST /llm/query`
* **Input (JSON):** `station_id`, `question` (length-capped); optional `start_time` / `end_time`.
* **Behavior:**
  1. **`MetricsStore.list_snapshots`** returns matching rows **newest-first** (optional window overlap filter).
  2. The model returns **one JSON object** only: **`metric_key`**, **`agg`**, **`device_id`** (string or null). Keys are **validated** against allowlists (`sensor_app.llm.allowed_keys`).
  3. **Python** gathers numeric values from stored snapshot JSON and applies **`agg`**. No second model call for the numeric answer; **`format_query_answer`** turns **`facts`** into a short **deterministic** sentence.
* **Intent:** Questions such as average pressure or total reading count over the snapshots in scope, grounded in **exactly** the same metric shape as **spec_ch1.md** / **`GET /metrics`**.
* **Not in scope for this route:** Open-ended data-quality narratives or multi-metric reports—use §2.3 or §2.1 instead. The query path returns **one aggregated scalar** (or empty) per request.

### 2.3 Data quality report (natural language)

* **Route:** `POST /llm/data-quality-summary`
* **Input (JSON):** Same shape as §2.1 (`station_id`, optional `snapshot_id`, optional window).
* **Behavior:** Loads **`data_quality`** (and related scores) from the resolved snapshot—the same structure produced by the **spec_ch1** pipeline—calls the model with a DQ-focused system prompt, returns prose plus **`snapshot_id`** and **`model`**.

---

## 3. Natural language query — execution rules

### 3.1 Allowlists

* **`metric_key`:** Must be one of the per-device keys produced by **`metrics_compute`** / stored under each device’s **`metrics`** map:  
  `uptime_seconds`, `average_pressure_bar`, `peak_pressure_bar`, `specific_power_kw_per_m3h`, `cycle_count`, `total_flow_volume_m3`, `thermal_efficiency_temp_per_kw`, `reading_count`, `pressure_std_bar`.
* **`agg`:** `mean` | `max` | `min` | `sum` | `latest`.

### 3.2 Reading values from snapshots

* Persisted device payloads use **`devices[].metrics`** (see **`_metrics_to_json`** in **`pipeline.py`**). Aggregation code accepts **`metrics`** and falls back to **`values`** for older or test-shaped JSON.

### 3.3 Aggregation semantics

* Snapshots are passed **newest-first** (`list_snapshots` + `ORDER BY computed_at DESC`).
* **`mean` / `max` / `min` / `sum`:** Collect one float per **(snapshot × device)** matching **`device_id`** (or all devices if null), then aggregate across that list. **`snapshot_ids_used`** lists **all** snapshot ids that were candidates in the filtered list (even if some devices lack the metric).
* **`latest`:** Uses **only** the newest snapshot (`snaps[0]`). Collects matching devices there; **`n_values`** is the count of scalar inputs. If there is **more than one** value (multiple devices), the implementation uses the **mean** of those values as the single **`value`** (documented behavior; for a single device set **`device_id`** in the plan).

### 3.4 Snapshot history and filters

* **`metric_snapshots`** upserts on **`(station_id, window_start, window_end)`** (see **spec_ch1.md**). Re-processing the **same** window **replaces** the row; older runs for that key are not retained.
* Optional **`start_time` / `end_time`** on **`/llm/query`** narrow which windows **`list_snapshots`** returns (overlap logic in **`MetricsStore`**).

### 3.5 Planner context

* **`multi_snapshot_query_context`** feeds the model **devices[].values**-shaped metric slices plus **`data_quality`** and **`data_quality_score`** so questions can be grounded in DQ text; execution still only aggregates **allowlisted `metric_key`** scalars.

---

## 4. Context building (LLM payloads)

* **`snapshot_metrics_context`:** Exposes station, window, **`devices`** with per-device scalars normalized to a **`values`** key in the JSON sent to the model (reads stored **`metrics`**, fallback **`values`**).
* **`snapshot_dq_context`:** **`data_quality`** blob as stored.
* **`dumps_bounded`:** Truncates serialized JSON by character budget (**`SENSOR_APP_LLM_MAX_CONTEXT_CHARS`**).

---

## 5. Architecture and modules

| Area | Location |
|------|----------|
| Routes, Pydantic bodies, 503/502 mapping | `sensor_app.api.main` |
| Chat transport, retries, fallback chain | `sensor_app.llm.client` |
| Prompts + orchestration | `sensor_app.llm.service` |
| Plan parse/validate + aggregation | `sensor_app.llm.query_execute` |
| Allowlists | `sensor_app.llm.allowed_keys` |
| Snapshot → JSON for prompts | `sensor_app.llm.context` |

---

## 6. Testing

* **LLM is mocked** in automated tests; assertions cover **wiring**, **validation**, **retry/fallback**, and **Python aggregation**, not literal model wording.
* **`tests/test_llm.py`**, **`tests/test_llm_units.py`** — no live API key required.

---

## 7. Operations and API errors

* **503:** LLM disabled or not configured (`SENSOR_APP_LLM_ENABLED`, missing key, etc.).
* **502:** Provider failure after retries, malformed provider JSON, invalid query plan from model (`LLMError` from service).
* **400:** e.g. **`question`** over max length.
* **Correlation:** **`X-Request-ID`**; structured logs with **`request_id`**, latency, outcome (no full prompt logging by default).
* **Fallback:** Configurable secondary host; **401 / 403 / 400 / 404 / 422** on primary do **not** trigger fallback. See root **`README.md`** for env vars.

---

## 8. Documentation

* Root **`README.md`:** Configure LLM (**`.env.example`**), providers, fallback, rate limits, how to run tests.
* **`docs/api/README.md`:** Per-route guide, when to use each **`/llm/*`** path, request/response examples, placeholders for demo media.

---

## 9. Production practices (extends spec_ch1.md)

Apply **spec_ch1.md** §7 to the service as a whole. For LLM paths additionally: observability (§7), secrets via env only, bound question/context size, no SQL from model text, documented cost/latency controls (timeouts, limits, bounded context; optional caching left to future work).

---

## 10. Hand-off

**spec_ch2.md** assumes **spec_ch1.md** is satisfied. Core routes **`GET /`**, **`GET /health`**, **`POST /process/{station_id}`**, **`GET /metrics/{station_id}`** remain unchanged in role; LLM routes are additive under **`POST /llm/*`**.
