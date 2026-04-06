"""Ingestion and transformation **library** (no FastAPI / Starlette imports).

Typical entrypoint for batch or service-side code::

    from sensor_app.lib import run_station_pipeline, PipelineConfig, MissingDataStrategy

The API layer calls :func:`run_station_pipeline` inside ``asyncio.to_thread`` so
Polars/SQLite work does not block the event loop.
"""

from sensor_app.lib.pipeline import MissingDataStrategy, PipelineConfig, run_station_pipeline

__all__ = ["MissingDataStrategy", "PipelineConfig", "run_station_pipeline"]
