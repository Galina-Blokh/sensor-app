"""**Data access** for raw sensor readings.

Implementations return **Polars** frames so the pipeline stays columnar end-to-end.
SQLite is used here; production would swap in a warehouse client implementing the
same :class:`SensorReadingSource` protocol.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Protocol

import polars as pl


class SensorReadingSource(Protocol):
    """Abstract source of ``sensor_readings``-shaped rows for one station."""

    def load_readings(
        self,
        station_id: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Load rows for **station_id**, optionally bounded by **[start, end]** inclusive.

        Returns:
            DataFrame with UTC ``timestamp`` and float sensor columns (empty schema if no rows).
        """


def _empty_readings_frame() -> pl.DataFrame:
    """Typed empty frame so downstream code can branch on ``is_empty()`` without guessing dtypes."""
    return pl.DataFrame(
        schema={
            "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "station_id": pl.Utf8,
            "device_id": pl.Utf8,
            "discharge_pressure": pl.Float64,
            "air_flow_rate": pl.Float64,
            "power_consumption": pl.Float64,
            "motor_speed": pl.Float64,
            "discharge_temp": pl.Float64,
        }
    )


class SqliteSensorRepository:
    """Parameterized SQLite reads (no string formatting of user input)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def load_readings(
        self,
        station_id: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Fetch ordered rows for one station; cast types for consistent math in Polars."""
        query = (
            "SELECT timestamp, station_id, device_id, discharge_pressure, "
            "air_flow_rate, power_consumption, motor_speed, discharge_temp "
            "FROM sensor_readings WHERE station_id = ?"
        )
        params: list[object] = [station_id]
        if start is not None:
            query += " AND timestamp >= ?"
            params.append(start.isoformat())
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(end.isoformat())
        query += " ORDER BY device_id, timestamp"
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            if not rows:
                return _empty_readings_frame()
            col_names = [d[0] for d in cur.description]
        # Column dict avoids ambiguous row-oriented constructors across Polars versions.
        data = {col_names[i]: [row[i] for row in rows] for i in range(len(col_names))}
        df = pl.DataFrame(data)
        df = df.with_columns(pl.col("timestamp").str.to_datetime(time_zone="UTC"))
        numeric = [c for c in col_names if c not in ("timestamp", "station_id", "device_id")]
        # Normalize INTEGER motor_speed etc. to float for shared expressions with pressures/flows.
        return df.with_columns([pl.col(c).cast(pl.Float64) for c in numeric])
