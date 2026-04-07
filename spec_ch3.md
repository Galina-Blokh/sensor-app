# spec_ch3.md — Event-Driven Consumer

**Project name:** **sensor_app** (extends the same codebase as **spec_ch1.md**).

This file is the **product spec** for **Challenge 3 — Event-Driven Consumer** in the take-home assignment (**optional bonus**, same weighting as in the assignment). It extends **spec_ch1.md** (ingestion library and metrics model) with a queue-driven consumer and related API surface. **spec_ch2.md** (LLM) is independent: if both are in scope, the streaming consumer and `/llm/*` routes can coexist without changing the event contract.

**Assignment mapping (take-home Challenge 3):**

| Assignment section | Where it lives in this spec |
|--------------------|----------------------------|
| Part 1 — Consumer & processing | §§1, 3 (consumer, transport separation, errors) |
| Part 2 — Query API | §4 (metrics + processing status) |
| Part 3 — Design note (two questions) | §5 |

## 0.1 Runtime model (async)

* The consumer **should** use **`asyncio`** (for example **`asyncio.Queue`** adapter in front of **`queue.Queue`**, or an async worker loop) when integrated with an async HTTP stack so ingestion hooks align with **spec_ch1.md**’s async API.
* **Batch** event handling may use **parallel in-process workers** (thread pool) for **CPU-heavy Polars** validation when throughput requires it; document tradeoffs.

## 1. What to build

Sensor data in production is **event-driven**. The solution **must** include:

* A **consumer** that reads from the queue supplied by **`producer.py`**, processes each message with the **library from spec_ch1.md**, updates **aggregated metrics**, and stores them.
* A **query API** (new or an extension of the API from **spec_ch1.md**) that reads those aggregates and exposes **processing status**.

## 2. Provided artifact

* **`producer.py`** — **ready-to-run** producer (assignment wording): **read-only** for the implementer (**do not modify** the file). Read it to learn the event format and how events are pushed into the shared queue. The **`SensorEvent`** shape includes `event_id`, `event_type`, ISO 8601 `timestamp`, `station_id`, `device_id`, `readings` with possible `None`, optional `metadata`, and the **stream end** signal (**`None` sentinel** or producer stop), as documented in that module.

## 3. Consumer and processing

The consumer **must** satisfy the assignment’s Part 1:

* **Read** events from the **queue provided by the producer** (in-repo: shared **`queue.Queue`**).
* **Validate and process** each event using the **library from Challenge 1 / spec_ch1.md** (same validation and pipeline rules as batch mode, adapted to per-event or micro-batches as needed).
* **Compute and store aggregated metrics** consistent with the **metric model from spec_ch1.md**.
* **Handle errors gracefully**: malformed events and processing failures **must not** crash the whole loop; behavior (skip, dead-letter, counters, logging) **must** be **defined and documented**.

**Transport separation (assignment wording).** Swapping the in-memory queue for a production broker (**e.g. Redis Streams, Pub/Sub**) **must** only require changing the **transport / adapter** implementation, **not** the core processing logic. Queue I/O **must** sit behind an interface; **core processing code must not** hard-code the in-memory queue type.

## 4. Query API

Expose HTTP (or equivalent) endpoints satisfying assignment **Part 2** (may **extend the API from Challenge 1 / spec_ch1.md**):

* **Aggregated metrics** for a station with **optional filters**: **time range**, **device**, **metric type** (assignment list). Exact paths are **implementation-defined** but **must** be documented.
* **Processing status** at minimum what the assignment calls out: **events consumed** (processed), **errors encountered**, plus counters or rates you define. **Additionally** (recommended for the in-memory demo): **current backlog depth** (e.g. `queue.Queue.qsize()` while using `producer.py`). If the transport later hides depth, **document** the replacement signal (e.g. consumer lag in seconds).

## 5. Design document (1 page max)

Add or extend a design note (**at most one page**) answering assignment **Part 3** — **both** questions, in line with the take-home wording:

1. How would you **deploy the producer and consumer as independently scalable services**? Include how **multiple consumer instances** would **coordinate to avoid processing the same event twice**.
2. What happens when the **consumer falls behind the producer**? How would you **detect** that, and what **strategies** would you use to **handle the growing backlog**?

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
