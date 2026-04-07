"""Parse ``producer.py`` event dicts into pipeline-shaped reading rows.

``producer`` publishes ``dict`` payloads (from :meth:`SensorEvent.to_dict`). Malformed
or injected error events return ``None`` so the consumer can count errors without
crashing the loop.
"""

from __future__ import annotations

from typing import Any

# Same logical columns as ``producer.SensorEventProducer.SENSOR_COLUMNS``.
READING_NUMERIC_KEYS = (
    "discharge_pressure",
    "air_flow_rate",
    "power_consumption",
    "motor_speed",
    "discharge_temp",
)


def parse_sensor_reading_event(event: Any) -> dict[str, Any] | None:
    """Return one flat row for :func:`sensor_app.lib.pipeline.run_pipeline_on_dataframe`, or ``None``."""
    if not isinstance(event, dict):
        return None
    required = ("event_id", "event_type", "timestamp", "station_id", "device_id", "readings")
    if not all(k in event for k in required):
        return None
    if event.get("event_type") != "sensor_reading":
        return None
    readings = event.get("readings")
    if not isinstance(readings, dict):
        return None
    row: dict[str, Any] = {
        "timestamp": event["timestamp"],
        "station_id": str(event["station_id"]),
        "device_id": str(event["device_id"]),
    }
    for k in READING_NUMERIC_KEYS:
        v = readings.get(k)
        if v is None:
            row[k] = None
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        else:
            row[k] = float(v)
    return row
