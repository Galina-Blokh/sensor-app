# spec_ch2.md — LLM-Powered Feature

**Project name:** **sensor_app** (extends the same service as **spec_ch1.md**).

Extends **spec_ch1.md** (metrics service and ingestion library) with LLM-backed endpoints and integration.

## 0.1 Runtime model (async)

* LLM HTTP clients **should** use **async** I/O (`httpx.AsyncClient` or provider async SDKs) from **`async def`** route handlers so the API does not block the event loop while waiting on the model.
* Apply **timeouts** and **retries** compatible with async (async sleep / tenacity async, or manual loops).

## 1. What to build

The metrics service delivered under **spec_ch1.md** **must** gain an **LLM integration**. Pick **one concrete provider or stack** (cloud API, local server, etc.) and **wire it in**; the spec cares about **structure and behavior**, not the brand name.

The integration **must** stay **maintainable**: clear boundaries, minimal coupling between routes and vendor SDKs.

## 2. Features to implement

**All three** features below are **required**:

1. **Natural language metrics summary**  
   An endpoint that accepts **computed metrics** (or identifiers the service can resolve to them) and returns a **plain-English station health summary**, in the spirit of: *“Station X has been running at 92% uptime with pressure trending 5% above target over the last week.”*

2. **Natural language query interface**  
   An endpoint that accepts **plain English questions** (for example about average pressure last week) and answers them by **translating** into access paths over **computed metrics** exactly as stored and exposed under **spec_ch1.md**. **Do not** silently bypass that model with ad-hoc raw SQL unless the **README** explicitly documents that exception and why.

3. **Data quality report generator**  
   An endpoint that returns a **natural-language summary** of **data quality issues** from ingestion (for example flatlines and missing percentages), grounded in the **library’s quality reporting defined in spec_ch1.md** wherever that data exists.

## 3. What the implementation must include

**Architecture.** LLM calls go through a **small, dedicated layer** (for example a client interface plus configuration). HTTP handlers **must not** embed vendor-specific SDK calls directly if that prevents swapping providers later.

**Operations.** Every LLM call path **must** have **timeouts**, **retries**, and **exponential or bounded backoff** where transient failures or rate limits occur. Failed calls **must** surface as **clear API errors**, not hung connections.

**Testing.** Automated tests **must mock the LLM**. Assertions **must** cover **wiring, configuration, error handling, and retry behavior**. Assertions **must not** depend on the literal text the model returns.

## 4. Documentation

The **`README.md`** (or a linked doc) **must** list:

* How to configure the LLM (environment variables, keys, base URLs, model id). Document that the wire protocol is **OpenAI-compatible** so **any** matching host applies; **`.env.example`** lists primary and optional fallback variables (README has provider URL/model examples).
* How to run the test suite including LLM-related tests.
* How to **skip** LLM integration tests in environments without credentials, if applicable.

## 5. Production practices (extends spec_ch1.md)

Apply **spec_ch1.md** section **7** to the service as a whole. **Additionally**, for LLM code paths:

* **Observability** — Log **latency**, **token usage when the API exposes it**, and **outcome** (success, timeout, rate limit) with **correlation IDs**. **Do not** log full prompts if they could contain sensitive data unless **redacted**.
* **Resilience** — Define behavior when the model is **down** (synchronous error, cached summary, async job, etc.) and **document** it.
* **Secrets** — API keys and endpoints **only** via secrets or configuration, never hard-coded.
* **Security** — Protect NL endpoints with the **same authn/z model** as the rest of the API where **spec_ch1.md** established one. **Bound input size** and encoding. **Never** build SQL with string concatenation from user or model text; use **parameterized** queries or structured access only.
* **Cost control** — Where expensive generation exists, **document** optional caching, idempotency, or budgets; implement at least **timeouts** and **clear failure modes**.

## 6. Hand-off to the rest of the repo

**spec_ch2.md** **assumes spec_ch1.md is already satisfied**. The **README** and tests **must** show how these endpoints fit **next to** the existing metrics API.
