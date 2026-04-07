"""Runtime **configuration** loaded from the environment.

All fields can be set via ``SENSOR_APP_<FIELD_NAME>`` (uppercase), e.g.::

    export SENSOR_APP_SENSOR_DB_PATH=/data/readings.db

Optional ``.env`` in the working directory is merged in (see ``pydantic-settings``).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed service configuration (paths, pipeline defaults, thresholds).

    Attributes:
        sensor_db_path: SQLite file with ``sensor_readings`` / metadata.
        metrics_db_path: SQLite file for persisted ``metric_snapshots`` rows.
        schema_path: JSON contract for validation and flatline thresholds.
        default_resample_rule: Pandas-style offset passed through the pipeline
            (e.g. ``5min`` → Polars ``5m``).
        missing_data_strategy: One of ``drop``, ``ffill``, ``interpolate``.
        power_on_motor_threshold_rpm: RPM above which a device counts as *on*
            for uptime/cycles (with power threshold below).
        power_on_power_threshold_kw: kW above which a device counts as *on*.
        flow_epsilon_m3h: Minimum |flow| (m³/h) to include points in specific-power mean.
        rate_limit_root: slowapi limit for ``GET /`` (e.g. ``120/minute``).
        rate_limit_health: Limit for ``GET /health``.
        rate_limit_process: Limit for ``POST /process/{station_id}`` (CPU-heavy).
        rate_limit_metrics: Limit for ``GET /metrics/{station_id}``.
        llm_enabled: When true and ``llm_api_key`` is set, LLM routes use the HTTP backend.
        llm_api_key: Bearer token for the OpenAI-compatible chat API (never log).
        llm_base_url: API base including ``/v1`` (OpenAI, Groq, self-hosted, …).
        llm_model: Model id accepted by that host (e.g. ``gpt-4o-mini``, Groq ``llama-3.1-8b-instant``).
        llm_timeout_seconds: Per-request HTTP timeout for LLM calls.
        llm_max_retries: Retries on 429 / 5xx after the first attempt.
        llm_backoff_base_ms: Initial backoff before retries (exponential growth).
        llm_max_question_chars: Max length for NL query text (UTF-8 code points).
        llm_max_context_chars: Max JSON context sent to the model (truncated tail).
        rate_limit_llm_summary: slowapi limit for ``POST /llm/metrics-summary``.
        rate_limit_llm_query: Limit for ``POST /llm/query``.
        rate_limit_llm_dq: Limit for ``POST /llm/data-quality-summary``.
        llm_fallback_enabled: When true (default) and fallback URL/model are set, call a **secondary**
            host after primary **transient** failures. Set to **false** to use primary only.
        llm_fallback_base_url: Secondary base (e.g. Groq or local OpenAI-compatible URL).
        llm_fallback_api_key: Secondary bearer; if empty, **primary** ``llm_api_key`` is reused.
        llm_fallback_model: Secondary model id for that host.
        stream_consumer_enabled: When true and an ``event_queue`` is wired into
            :func:`sensor_app.api.main.create_app`, run the background queue reader
            (``spec_ch3``).
        stream_flush_min_events: Flush buffered valid readings to the pipeline after
            this many rows (per process).
        stream_max_seen_event_ids: Bounded de-duplication set for ``event_id``
            (at-least-once safe).
        rate_limit_stream_status: slowapi limit for ``GET /stream/status``.
        rate_limit_stream_flush: slowapi limit for ``POST /stream/flush``.
    """

    model_config = SettingsConfigDict(
        env_prefix="SENSOR_APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sensor_db_path: str = "sensor_data.db"
    metrics_db_path: str = "metrics.db"
    schema_path: str = "sensor_schema.json"
    default_resample_rule: str = "5min"
    missing_data_strategy: str = "interpolate"
    power_on_motor_threshold_rpm: float = 1.0
    power_on_power_threshold_kw: float = 0.5
    flow_epsilon_m3h: float = 1e-3
    rate_limit_root: str = "120/minute"
    rate_limit_health: str = "120/minute"
    rate_limit_process: str = "30/minute"
    rate_limit_metrics: str = "60/minute"
    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    llm_backoff_base_ms: int = 400
    llm_max_question_chars: int = 2000
    llm_max_context_chars: int = 80_000
    rate_limit_llm_summary: str = "30/minute"
    rate_limit_llm_query: str = "30/minute"
    rate_limit_llm_dq: str = "30/minute"
    llm_fallback_enabled: bool = True
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    stream_consumer_enabled: bool = False
    stream_flush_min_events: int = 2000
    stream_max_seen_event_ids: int = 100_000
    rate_limit_stream_status: str = "120/minute"
    rate_limit_stream_flush: str = "30/minute"
