# spec_ch3.md — Event-Driven Consumer

**Project name:** **sensor_app** (extends the same codebase as **spec_ch1.md**).

Extends **spec_ch1.md** (ingestion library and metrics model) with a queue-driven consumer and related API surface.

## 0.1 Runtime model (async)

* The consumer **should** use **`asyncio`** (for example **`asyncio.Queue`** adapter in front of **`queue.Queue`**, or an async worker loop) when integrated with an async HTTP stack so ingestion hooks align with **spec_ch1.md**’s async API.
* **Batch** event handling may use **parallel in-process workers** (thread pool) for **CPU-heavy Polars** validation when throughput requires it; document tradeoffs.

## 1. What to build

Sensor data in production is **event-driven**. The solution **must** include:

* A **consumer** that reads from the queue supplied by **`producer.py`**, processes each message with the **library from spec_ch1.md**, updates **aggregated metrics**, and stores them.
* A **query API** (new or an extension of the API from **spec_ch1.md**) that reads those aggregates and exposes **processing status**.

## 2. Provided artifact

* **`producer.py`** is **read-only** for the implementer: **do not edit** it. Read it to learn the **`SensorEvent`** shape (`event_id`, `event_type`, ISO 8601 `timestamp`, `station_id`, `device_id`, `readings` with possible `None`, optional `metadata`) and the **stream end** signal (**`None` sentinel** or producer stop), as documented in that module.

## 3. Consumer and processing

The consumer **must**:

* **Read** events from the **`queue.Queue`** the producer uses.
* **Validate and process** each event through the **ingestion and validation logic from spec_ch1.md** (same rules as batch work, adapted to per-event or micro-batches as needed).
* **Update stored aggregates** in line with the **metric model from spec_ch1.md**.
* **Survive bad input**: malformed events and processing errors **must not** crash the whole loop; behavior (skip, dead-letter, counters, logging) **must** be **defined and documented**.

**Transport separation.** Queue I/O **must** sit behind an interface so only **adapters** change when moving from `queue.Queue` to **Redis Streams**, **Google Pub/Sub**, or similar. **Core processing code must not** hard-code the in-memory queue type.

## 4. Query API

Expose HTTP (or equivalent) endpoints that **must** support:

* **Aggregated metrics** for a station with **optional filters**: time range, device, metric type. Exact paths are **implementation-defined** but **must** be documented.
* **Processing status**: **events processed**, **errors**, and **current backlog depth** (for example `queue.Queue.qsize()` while using `producer.py`). If the transport later hides depth, **document** the replacement signal (for example consumer lag in seconds).

## 5. Design document (1 page max)

Add or extend a design note (**at most one page**) that **answers both** questions:

1. How **producer** and **consumer** deploy as **independently scalable** services, and how **multiple consumer instances** avoid **processing the same event twice**.
2. What happens when the **consumer lags** the producer, how **lag is detected**, and which **strategies** shrink or bound **backlog** growth.

## 6. Documentation

The **`README.md`** **must** explain how to start **producer and consumer together**, required configuration, and failure behavior.

## 7. Production practices (extends spec_ch1.md)

Apply **spec_ch1.md** section **7** to the consumer, storage layer, and API. **Additionally**:

* **Observability** — Structured logs with **`event_id`**, **`station_id`**, and outcome; metrics for **throughput**, **lag**, **errors**; tracing if multiple processes are involved.
* **SLOs** — **Document** freshness and error-rate targets and what would fire alerts.
* **Secrets** — Broker connection strings and names **from configuration**, not source code.
* **Security** — Match **spec_ch1.md** **authn/z** on extended APIs; **document** TLS and IAM expectations for a real broker.
* **Idempotency** — Assume **at-least-once** delivery: processing **must** be safe when the same **`event_id`** appears twice (dedupe, idempotent writes, or equivalent).
* **Capacity** — **Document** concurrency versus database write limits and partitioning tradeoffs.
* **Retention** — **Document** retention for raw events, aggregates, and dead-letter payloads plus **replay** after outages.
* **Disaster recovery** — **Document** checkpoint storage, recovery, and multi-AZ or replay strategy.

## 8. Hand-off to the rest of the repo

**spec_ch3.md** **assumes spec_ch1.md is already satisfied**. The **README** and design doc **must** tie the streaming path to the **same library and metric definitions** as batch mode unless a divergence is explicitly justified in writing.
