"""Pytest fixtures shared across API and pipeline tests.

**Integration vs unit**

* Fixtures that depend on **`sensor_data.db`** are **session-scoped** where possible.
  If the file is missing (e.g. fresh CI clone without artifacts), those tests
  **skip** with a clear message so ``uv run pytest`` still passes for unit-only
  workflows when desired.
* **`integration_slice`** picks the busiest station and a **short** wall-clock
  window (~6h from the series start) so full-pipeline tests stay fast enough for
  local and CI feedback loops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Repository root (parent of ``tests/``)."""
    return ROOT


@pytest.fixture(scope="session")
def schema_path(repo_root: Path) -> str:
    """Absolute path to ``sensor_schema.json``."""
    return str(repo_root / "sensor_schema.json")


@pytest.fixture(scope="session")
def sensor_db_path(repo_root: Path) -> str:
    """Path to bundled SQLite readings DB, or skip integration tests if absent."""
    p = repo_root / "sensor_data.db"
    if not p.is_file():
        pytest.skip("sensor_data.db missing — integration tests skipped")
    return str(p)


@pytest.fixture()
def sample_station_id(sensor_db_path: str) -> str:
    """Any ``station_id`` present in ``sensor_readings`` (for ad-hoc experiments)."""
    import sqlite3

    with sqlite3.connect(sensor_db_path) as conn:
        row = conn.execute(
            "SELECT station_id FROM sensor_readings LIMIT 1",
        ).fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture(scope="session")
def integration_slice(sensor_db_path: str) -> tuple[str, str, str]:
    """``(station_id, window_start_iso, window_end_iso)`` for a compact integration window.

    Chooses the station with the most rows, then caps the window to **6 hours**
    from that station's first timestamp (or the full span if shorter).
    """
    import sqlite3
    from datetime import datetime, timedelta

    with sqlite3.connect(sensor_db_path) as conn:
        row = conn.execute(
            """
            SELECT station_id, MIN(timestamp), MAX(timestamp)
            FROM sensor_readings
            GROUP BY station_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    sid, t_min_s, t_max_s = row
    t0 = datetime.fromisoformat(str(t_min_s).replace("Z", "+00:00"))
    t_end = datetime.fromisoformat(str(t_max_s).replace("Z", "+00:00"))
    # Keep this small: full-station / full-history runs belong in manual or load tests.
    t1 = min(t0 + timedelta(hours=6), t_end)
    if t1 <= t0:
        t1 = t_end
    return (str(sid), t0.isoformat(), t1.isoformat())
