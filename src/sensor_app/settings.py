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
        llm_base_url: Provider base URL (e.g. ``https://api.openai.com/v1``).
        llm_model: Chat model id passed to the provider.
        llm_timeout_seconds: Per-request HTTP timeout for LLM calls.
        llm_max_retries: Retries on 429 / 5xx after the first attempt.
        llm_backoff_base_ms: Initial backoff before retries (exponential growth).
        llm_max_question_chars: Max length for NL query text (UTF-8 code points).
        llm_max_context_chars: Max JSON context sent to the model (truncated tail).
        rate_limit_llm_summary: slowapi limit for ``POST /llm/metrics-summary``.
        rate_limit_llm_query: Limit for ``POST /llm/query``.
        rate_limit_llm_dq: Limit for ``POST /llm/data-quality-summary``.
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
