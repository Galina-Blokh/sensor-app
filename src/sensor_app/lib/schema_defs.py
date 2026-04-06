"""Parse **sensor_schema.json** into typed structures for validation and DQ.

The JSON mixes **table column** specs (types, required, ``valid_range``) and
**sensor_types** metadata (e.g. ``flatline_threshold_minutes``). This module
extracts only what the pipeline needs so the rest of the code stays decoupled
from raw JSON layout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ColumnSpec:
    """One logical column from ``tables.sensor_readings.columns``."""

    name: str
    dtype: str
    required: bool
    valid_range: tuple[float, float] | tuple[int, int] | None
    unit: str | None


@dataclass(frozen=True)
class LoadedSchema:
    """In-memory view of the schema file used by :mod:`sensor_app.lib.pipeline`.

    Attributes:
        raw: Full parsed JSON (for forward-compatible extensions).
        reading_columns: Ordered column specs for ``sensor_readings``.
        sensor_numeric_columns: Subset of numeric measurement columns (excludes ids/timestamp).
        flatline_threshold_minutes: Map sensor name → minutes from ``sensor_types``.
    """

    raw: dict[str, Any]
    reading_columns: list[ColumnSpec]
    sensor_numeric_columns: list[str]
    flatline_threshold_minutes: dict[str, int]

    @classmethod
    def from_path(cls, path: str | Path) -> LoadedSchema:
        """Load and normalize **path** (UTF-8 JSON).

        Raises:
            FileNotFoundError: If **path** does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        raw_path = Path(path)
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        tables = raw.get("tables", {})
        sr = tables.get("sensor_readings", {}).get("columns", {})
        reading_columns: list[ColumnSpec] = []
        sensor_numeric_columns: list[str] = []
        for name, spec in sr.items():
            vr = spec.get("valid_range")
            valid: tuple[float, float] | tuple[int, int] | None = None
            if vr is not None:
                lo, hi = vr["min"], vr["max"]
                if spec.get("type") == "integer":
                    valid = (int(lo), int(hi))
                else:
                    valid = (float(lo), float(hi))
            col = ColumnSpec(
                name=name,
                dtype=str(spec.get("type", "string")),
                required=bool(spec.get("required", False)),
                valid_range=valid,
                unit=spec.get("unit"),
            )
            reading_columns.append(col)
            # Keys used for range validation + imputation (not ids / timestamp).
            if col.dtype in ("float", "integer") and name not in (
                "timestamp",
                "station_id",
                "device_id",
            ):
                sensor_numeric_columns.append(name)

        st = raw.get("sensor_types", {})
        flatline: dict[str, int] = {}
        for key, meta in st.items():
            m = meta.get("flatline_threshold_minutes")
            if m is not None:
                flatline[key] = int(m)

        return cls(
            raw=raw,
            reading_columns=reading_columns,
            sensor_numeric_columns=sensor_numeric_columns,
            flatline_threshold_minutes=flatline,
        )
