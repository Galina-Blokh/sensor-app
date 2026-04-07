# HTTP API — route guide

Per-path notes for **`sensor_app.api.main:app`**. Add **demo videos** under each heading (Markdown link, image, or embed where your viewer allows it).

**Live reference:** with the server running, open **`/docs`** (Swagger UI) for request/response schemas and “Try it out”.

**Implementation:** `src/sensor_app/api/main.py` · **LLM logic:** `src/sensor_app/llm/` · **Spec:** `spec_ch2.md` (LLM), `spec_ch1.md` (pipeline/metrics).

---

## `GET /`

**Purpose:** Small JSON index of available routes (convenience for discovery).

**Example:**

```http
GET / HTTP/1.1
Host: 127.0.0.1:8000
```

```bash
curl -s http://127.0.0.1:8000/
```

**Video:** _Add link or embed here._

---

## `GET /health`

**Purpose:** Liveness probe; no database work required for a basic OK.

**Example:**

```bash
curl -s http://127.0.0.1:8000/health
```

**Video:** _Add link or embed here._

---

## `POST /process/{station_id}`

**Purpose:** Run the ingestion pipeline for one station (optional `start_time` / `end_time` query params, ISO-8601). Writes or replaces a row in **`metric_snapshots`** for that station + window.

**Examples** (replace `STATION_ID` with a real `station_id` from your readings DB):

```bash
# Full window (defaults inside pipeline / repo semantics)
curl -s -X POST "http://127.0.0.1:8000/process/STATION_ID"

# Bounded window
curl -s -X POST \
  "http://127.0.0.1:8000/process/STATION_ID?start_time=2024-02-01T00:00:00%2B00:00&end_time=2024-02-07T23:59:59%2B00:00"
```

**Video:** _Add link or embed here._

---

## `GET /metrics/{station_id}`

**Purpose:** List stored metric snapshots for the station. Optional query params: `start_time`, `end_time`, `device_id` (filters devices in each snapshot’s JSON).

**Examples:**

```bash
curl -s "http://127.0.0.1:8000/metrics/STATION_ID"

curl -s "http://127.0.0.1:8000/metrics/STATION_ID?device_id=compressor-1"

curl -s "http://127.0.0.1:8000/metrics/STATION_ID?start_time=2024-02-01T00:00:00%2B00:00&end_time=2024-02-28T23:59:59%2B00:00"
```

**Video:** _Add link or embed here._

---

## `POST /llm/metrics-summary`

**Purpose:** Plain-English **operational** summary from **per-device metrics** in a chosen snapshot (or newest in a time window). Body: `station_id`, optional `snapshot_id`, optional `start_time` / `end_time`.

**When to use:** Narrative health / performance from numbers (pressure, flow, uptime, etc.).

**Example bodies** (`Content-Type: application/json`):

```json
{
  "station_id": "STATION_ID"
}
```

```json
{
  "station_id": "STATION_ID",
  "snapshot_id": 12
}
```

```json
{
  "station_id": "STATION_ID",
  "start_time": "2024-02-01T00:00:00+00:00",
  "end_time": "2024-02-28T23:59:59+00:00"
}
```

```bash
curl -s -X POST http://127.0.0.1:8000/llm/metrics-summary \
  -H "Content-Type: application/json" \
  -d '{"station_id":"STATION_ID"}'
```

**Video:** _Add link or embed here._

---

## `POST /llm/data-quality-summary`

**Purpose:** Plain-English summary from stored **`data_quality`** (missing fractions, out-of-range counts, flatlines, row counts, etc.). Same body shape as metrics-summary.

**When to use:** Data consistency, validation issues, “what’s wrong with the data?” style questions.

**Example bodies** (same fields as metrics-summary):

```json
{
  "station_id": "STATION_ID"
}
```

```json
{
  "station_id": "STATION_ID",
  "snapshot_id": 12
}
```

```bash
curl -s -X POST http://127.0.0.1:8000/llm/data-quality-summary \
  -H "Content-Type: application/json" \
  -d '{"station_id":"STATION_ID"}'
```

The model sees your **data_quality** payload for that snapshot; you do not send a separate `question` field on this route (the prompt is fixed server-side).

**Video:** _Add link or embed here._

---

## `POST /llm/query`

**Purpose:** Natural-language **question** → model emits a **JSON plan** (`metric_key`, `agg`, `device_id`) → server runs **Python aggregation** over stored device metrics only. Response includes structured **`facts`** and a short **deterministic** sentence built from the number (not open-ended prose).

**When to use:** You want **one aggregated value** from an allowlisted metric key (`mean` / `max` / `min` / `sum` / `latest`).

**When *not* to use:** Broad qualitative questions (“data consistency”, “potential issues”) — use **`/llm/data-quality-summary`** or **`/llm/metrics-summary`** instead.

**Allowlisted `metric_key` values** (must match stored snapshot device metrics):  
`uptime_seconds`, `average_pressure_bar`, `peak_pressure_bar`, `specific_power_kw_per_m3h`, `cycle_count`, `total_flow_volume_m3`, `thermal_efficiency_temp_per_kw`, `reading_count`, `pressure_std_bar`.

**Allowed `agg` values:** `mean`, `max`, `min`, `sum`, `latest`.

**Example request bodies** (`station_id` + `question`; optional `start_time` / `end_time` to limit which snapshots are aggregated):

```json
{
  "station_id": "STATION_ID",
  "question": "What is the mean discharge pressure across devices?"
}
```

```json
{
  "station_id": "STATION_ID",
  "question": "Peak pressure across all snapshots in the window."
}
```

```json
{
  "station_id": "STATION_ID",
  "question": "Sum total reading counts for the station."
}
```

```json
{
  "station_id": "STATION_ID",
  "question": "Minimum specific_power_kw_per_m3h across devices in this window.",
  "start_time": "2024-02-01T00:00:00+00:00",
  "end_time": "2024-02-28T23:59:59+00:00"
}
```

```json
{
  "station_id": "STATION_ID",
  "question": "Mean uptime in seconds across devices."
}
```

```bash
curl -s -X POST http://127.0.0.1:8000/llm/query \
  -H "Content-Type: application/json" \
  -d "{\"station_id\":\"STATION_ID\",\"question\":\"What is the mean average_pressure_bar across devices?\"}"
```

**Typical response shape** (values depend on data):

```json
{
  "answer": "mean average_pressure_bar across matching devices: 7.3756 (from 2 series values).",
  "facts": {
    "metric_key": "average_pressure_bar",
    "agg": "mean",
    "device_id": null,
    "value": 7.37559681712963,
    "n_values": 2,
    "snapshot_ids_used": [11, 12]
  },
  "model": "gpt-4o-mini"
}
```

**Video:** _Add link or embed here._

---

## Cross-cutting behavior

- **`X-Request-ID`** on every response; optional on request for tracing.
- **Rate limits:** slowapi per route (`SENSOR_APP_RATE_LIMIT_*`); **429** when exceeded.
- **LLM off / misconfigured:** **`503`** on `/llm/*`. Provider errors / timeouts after retries: **`502`**.
- **LLM env and providers:** see root **`README.md`** (configuration table and fallback behavior).

If you add **streaming** or other routes in a branch, extend this file with the same pattern (`## METHOD /path` + **Video:** placeholder).
