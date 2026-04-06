# Senior Data & AI Platform Engineer — Take-Home Assignment

## Overview

This assignment evaluates your ability to design and build production-quality Python software for an industrial IoT data platform. You will set up a project from scratch and complete the challenges below.

**You are encouraged to use AI tools** (Copilot, ChatGPT, Claude, etc.). We use them daily. What matters is your understanding of the code, your architectural decisions, and your ability to discuss tradeoffs in the follow-up interview.

**Time budget:** You have **2 days** from when you receive this assignment. If you run out of time, document what you would do next — scoping decisions are part of the evaluation.

---

## Structure

| | Challenge 1 | Challenge 2 | Challenge 3 |
|---|---|---|---|
| **Status** | Required | Optional Bonus | Optional Bonus |
| **Focus** | Data ingestion library + metrics API | LLM-powered feature | Event-driven consumer |
| **Builds on** | — | Challenge 1 | Challenge 1 |

Complete Challenge 1 first. If you have time, Challenges 2 and 3 extend it — you can do either or both in any order.

---

## Part 0 — Project Setup

Set up a production-quality Python project **from scratch**. No starter code is provided — the project structure, tooling, and configuration are all your decisions.

**We intentionally do not prescribe specific tools.** Your choices of package manager, linter, project layout, type checker, test framework, and task runner are part of the evaluation. Pick whatever you believe represents modern Python best practices and be ready to explain why.

### What we expect to see

- **Dependency management**: A reproducible way to install and manage the project's dependencies
- **Project structure**: A clean layout with module boundaries separating concerns
- **Code quality**: Linting, formatting, and type checking configured and enforced
- **Testing**: A test suite covering the core functionality
- **Automation**: A way to run common tasks (lint, typecheck, test) with simple commands
- **Git**: A repository with commit history that reflects your development process
- **README.md**: Setup instructions — a new developer should be able to clone, install, and run tests

---

## Challenge 1 — Data Ingestion Library & Metrics Service (Required)

### Context

You are building software for an industrial compressed air platform. Multiple services need to read, validate, and preprocess sensor data — so you will first build a **reusable library** for data ingestion and transformation, then build a **metrics service** that uses it.

### Provided

- `sensor_data.db` — A SQLite database containing 30 days of compressor sensor data with tables for sensor readings (`timestamp`, `station_id`, `device_id`, `pressure_bar`, `flow_m3h`, `power_kw`, `rpm`, `temperature_c`) and station metadata. The data contains realistic issues: missing values, gaps, sensor flatlines, and noisy readings. In production, this would be a data warehouse like BigQuery or PostgreSQL.
- `sensor_schema.json` — A schema definition describing expected columns, their types, and valid value ranges per sensor type.

### Part 1 — Data Ingestion & Transformation Library

Build a reusable Python library (an importable package, not a script) that:

- Reads sensor data from the SQLite database
- Validates the data against the provided schema (column presence, types, value ranges)
- Handles missing/malformed data with configurable strategies (e.g., drop, fill, interpolate)
- Resamples to a configurable frequency
- Returns clean, structured output
- Reports data quality issues found during processing (e.g., % missing per column, out-of-range values, flatline periods detected)

The library should be **independently usable** — not coupled to any web framework. Think about how the data access layer is designed: in production, the backing store would change (e.g., from SQLite to BigQuery), and ideally consumers of the library wouldn't need to change.

### Part 2 — Metrics Service

Build an API service that uses the library to compute and serve operational metrics:

- An endpoint to trigger processing for a station: fetch data via the library, compute metrics, store results
- An endpoint to retrieve computed metrics for a given station (with optional filtering, e.g., by time range or device)
- A health check endpoint

Example metrics (choose at least 4, or define your own):
- Uptime / active time per device
- Average and peak pressure
- Specific power (power per unit flow)
- Cycle counts (on/off transitions)
- Total flow volume over a period

The service should be a **thin wrapper** — the library does the heavy lifting. Document your metric definitions.

### Part 3 — Testing

Write tests that give you confidence the system works correctly. Consider how you would test the library independently from the API service, and how you would distinguish between tests that can run in isolation and those that require external services.

### Part 4 — Design Document (1 page max)

Answer briefly:
1. How would you version and distribute this library across 25+ services in a monorepo?
2. How would you handle schema evolution — new sensor types, renamed columns, different units — without breaking the library's existing consumers?
3. What would a CI/CD pipeline look like for this service (build, test, deploy)?

---

## Challenge 2 — LLM-Powered Feature (Optional Bonus)

### Context

Add an LLM-powered capability to your metrics service. You may use any LLM provider or framework — a cloud API, a locally served model, or anything in between. We care about the design and integration, not the specific provider.

### Implement one or more of the following

- **Natural language metrics summary**: An endpoint that takes computed metrics and generates a plain-English station health summary (e.g., "Station X has been running at 92% uptime with pressure trending 5% above target over the last week")
- **Natural language query interface**: An endpoint that accepts questions in plain English (e.g., "what was the average pressure at station X last week?") and translates them into queries over the computed data
- **Data quality report generator**: An endpoint that produces a natural language summary of data quality issues found during ingestion (e.g., "Device compressor_3 had a 4-hour flatline period on March 12th, and 23% of temperature readings were missing")

### What we're looking for

- How you integrate LLM calls into an existing service architecture (clean separation, swappable provider)
- How you handle errors, latency, retries, and backoffs (e.g., what if the LLM is unavailable or slow?)
- How you test LLM-dependent code — the LLM should be treated as a black box in tests. Mock it so tests verify your integration plumbing (connection setup, error handling, retry logic), not response quality

---

## Challenge 3 — Event-Driven Consumer (Optional Bonus)

### Context

In production, sensor data doesn't arrive via API calls — it flows through an event-driven architecture. A separate producer service (already built) publishes sensor data events into a queue. Your job is to build the consumer that processes them.

### Provided

- `producer.py` — A ready-to-run producer that generates sensor data events and pushes them into a Python queue. You do not need to modify this file, but you should read it to understand the event format.

### Part 1 — Consumer & Processing

Build a consumer that:

- Reads events from the queue provided by the producer
- Validates and processes each event using the library from Challenge 1
- Computes and stores aggregated metrics
- Handles errors gracefully (malformed events, processing failures)

Design the consumer so that swapping the in-memory queue for a production message system (e.g., Redis Streams, Pub/Sub) would only require changing the transport implementation, not the processing logic.

### Part 2 — Query API

Expose the processed results via a query API (this can extend the API from Challenge 1):

- Retrieve aggregated metrics for a station, with optional filters (time range, device, metric type)
- Retrieve processing status (e.g., events consumed, errors encountered)

### Part 3 — Design Document (1 page max)

Answer briefly:
1. How would you deploy the producer and consumer as independently scalable services? Consider how multiple consumer instances would coordinate to avoid processing the same event twice.
2. What happens when the consumer falls behind the producer? How would you detect this, and what strategies would you use to handle the growing backlog?

---

## How these relate to the role

These challenges mirror our daily work. Our platform consists of 25+ Python microservices that ingest, validate, and process industrial sensor data at scale. Shared libraries provide reusable data processing logic consumed across services. Events flow between decoupled producers and consumers via typed schemas. And an LLM-powered assistant helps users query and understand station data. The skills tested here are the skills you'll use every day.

---

## Submission

- Share as a **Git repository** (GitHub, GitLab, or zip with `.git` intact)
- Commit history should show your development process
- Include any assumptions or decisions in your README

---

## Evaluation Criteria

| Area | Weight |
|---|---|
| **Project setup & dev standards** | 25% |
| **Core implementation** | 30% |
| **Data modeling & typing** | 10% |
| **Testing** | 20% |
| **Design document** | 15% |

Challenges 2 and 3 are evaluated as bonuses — they can raise your score but their absence won't lower it. Which bonus you choose (or whether you tackle both) is itself useful signal.

### What separates good from great:

- **Good**: Working solution with clear separation of concerns, tests pass, quality tooling configured and integrated into the workflow
- **Great**: Clean module boundaries, thoughtful data modeling, strong test coverage with meaningful assertions, design doc that shows real production experience, awareness of scale and operational concerns

---

## Follow-Up Interview

The take-home is a starting point for a deeper technical conversation. Be prepared to:

- Walk through your code and explain your decisions
- Discuss tradeoffs you considered (and rejected)
- Extend the solution live (e.g., "how would you add X?")
- Discuss how you would implement challenges you didn't attempt — even without code, your design thinking matters
- Answer questions about Python internals, testing strategy, data engineering at scale, and production operations

This is where we assess depth of understanding — not just what you built, but *why* you built it that way.
