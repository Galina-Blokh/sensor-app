"""Industrial sensor **ingestion** and **metrics** package.

Public layout:

* ``sensor_app.lib`` — Framework-free pipeline (Polars), validation, metric math.
* ``sensor_app.api`` — Thin FastAPI layer; defers CPU work to a thread pool.
* ``sensor_app.settings`` — Pydantic settings from environment / ``.env``.

Import the API app as ``sensor_app.api.main:app`` for ASGI servers (e.g. uvicorn).
"""

__version__ = "0.1.0"
