#!/usr/bin/env python3
"""Run ``producer.SensorEventProducer`` and FastAPI with the in-process queue consumer.

From the repository root (next to ``producer.py``)::

    uv run python run_streaming_stack.py

Environment (optional): ``PORT``, ``STREAM_SPEED_MULTIPLIER``, ``SENSOR_APP_*`` as in README.
"""

from __future__ import annotations

import os
import queue

import uvicorn

from producer import SensorEventProducer
from sensor_app.api.main import create_app
from sensor_app.settings import Settings


def main() -> None:
    q: queue.Queue[object] = queue.Queue()
    settings = Settings(stream_consumer_enabled=True)
    producer = SensorEventProducer(
        db_path=settings.sensor_db_path,
        event_queue=q,
        speed_multiplier=float(os.environ.get("STREAM_SPEED_MULTIPLIER", "5000")),
    )
    app = create_app(settings, event_queue=q)
    producer.start()
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
