"""
Sensor Data Event Producer

Requires Python 3.11+

This producer simulates a real-time stream of compressor sensor data events.
It reads from the provided SQLite database and publishes events into a Python queue,
simulating how data would flow through a production message system.

The stream ends with a sentinel value (None) after all database rows have been
replayed, or when stop() is called.

Usage:
    from producer import SensorEventProducer
    import queue

    q = queue.Queue()
    producer = SensorEventProducer(db_path="sensor_data.db", event_queue=q)
    producer.start()

    # Consume events from the queue
    while True:
        event = q.get()
        if event is None:  # Sentinel value signals end of stream
            break
        process(event)

You do NOT need to modify this file. Read it to understand the event format,
then build a consumer that processes these events.
"""

from __future__ import annotations

import json
import queue
import random
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class SensorEvent:
    """A single sensor reading event from a compressor device.

    This is the event contract. Your consumer should be able to process these events.

    Attributes:
        event_id: Unique identifier for this event.
        event_type: Always "sensor_reading" for sensor data events.
        timestamp: ISO 8601 timestamp of the sensor reading (UTC).
        station_id: The station this reading belongs to.
        device_id: The specific compressor device.
        readings: Dictionary of sensor name -> value. Values may be None (missing sensor data).
        metadata: Optional additional context about the event.
    """

    event_id: str
    event_type: str
    timestamp: str
    station_id: str
    device_id: str
    readings: dict[str, float | int | None]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensorEvent:
        return cls(**data)


class SensorEventProducer:
    """Produces sensor data events from the SQLite database into a queue.

    The producer reads historical sensor data and replays it as events,
    simulating a real-time data stream. It introduces realistic production
    conditions: occasional delays, out-of-order events, and malformed events.

    Args:
        db_path: Path to the sensor_data.db SQLite database.
        event_queue: A queue.Queue instance to publish events into.
        speed_multiplier: How fast to replay events. 1.0 = real-time,
            100.0 = 100x faster. Default is 1000.0 for practical testing.
        batch_size: Number of rows to read from the database at a time.
        inject_errors: Whether to inject malformed events (for testing error handling).
        error_rate: Fraction of events that will be malformed (0.0 to 1.0).
    """

    SENSOR_COLUMNS = [
        "discharge_pressure",
        "air_flow_rate",
        "power_consumption",
        "motor_speed",
        "discharge_temp",
    ]

    def __init__(
        self,
        db_path: str = "sensor_data.db",
        event_queue: queue.Queue[dict[str, Any] | None] | None = None,
        speed_multiplier: float = 1000.0,
        batch_size: int = 100,
        inject_errors: bool = True,
        error_rate: float = 0.02,
    ) -> None:
        self.db_path = db_path
        self.event_queue: queue.Queue[dict[str, Any] | None] = event_queue or queue.Queue()
        self.speed_multiplier = speed_multiplier
        self.batch_size = batch_size
        self.inject_errors = inject_errors
        self.error_rate = error_rate
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._events_produced = 0
        self._counter_lock = threading.Lock()
        self._producer_error: BaseException | None = None

    @property
    def events_produced(self) -> int:
        with self._counter_lock:
            return self._events_produced

    @property
    def error(self) -> BaseException | None:
        """If the producer thread crashed, this holds the exception."""
        return self._producer_error

    def start(self) -> None:
        """Start producing events in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Producer is already running")
        self._stop_event.clear()
        self._events_produced = 0
        self._thread = threading.Thread(target=self._produce, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the producer to stop and wait for it to finish.

        The producer thread sends the sentinel (None) on exit, so this method
        just signals and waits. Safe to call even if the stream already ended.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _produce(self) -> None:
        """Main production loop. Reads from DB and publishes events.

        Always sends a sentinel (None) on exit, whether the stream finishes
        naturally, is stopped, or crashes. The DB connection is always closed.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sensor_readings ORDER BY timestamp ASC")

            prev_timestamp: datetime | None = None
            batch: list[dict[str, Any]] = []

            while not self._stop_event.is_set():
                row = cursor.fetchone()
                if row is None:
                    # Flush remaining batch
                    self._publish_batch(batch)
                    break

                row_dict = dict(row)
                current_timestamp = datetime.fromisoformat(row_dict["timestamp"])

                # Simulate time delay between readings
                if prev_timestamp is not None:
                    delta = (current_timestamp - prev_timestamp).total_seconds()
                    sleep_time = delta / self.speed_multiplier
                    if sleep_time > 0:
                        # Publish accumulated batch before sleeping
                        self._publish_batch(batch)
                        batch = []
                        time.sleep(min(sleep_time, 0.1))  # Cap sleep to keep things moving

                event = self._row_to_event(row_dict)
                batch.append(event)

                if len(batch) >= self.batch_size:
                    self._publish_batch(batch)
                    batch = []

                prev_timestamp = current_timestamp
        except Exception as exc:
            self._producer_error = exc
        finally:
            conn.close()
            # Always send sentinel so the consumer never blocks forever
            self.event_queue.put(None)

    def _publish_batch(self, batch: list[dict[str, Any]]) -> None:
        """Publish a batch of events, optionally shuffling for realism."""
        if not batch:
            return

        # Occasionally shuffle batch to simulate out-of-order delivery
        if random.random() < 0.05:
            random.shuffle(batch)

        for event in batch:
            if self._stop_event.is_set():
                return
            self.event_queue.put(event)
            with self._counter_lock:
                self._events_produced += 1

    def _row_to_event(self, row: dict[str, Any]) -> dict[str, Any]:
        """Convert a database row to a sensor event dict."""
        # Occasionally inject malformed events
        if self.inject_errors and random.random() < self.error_rate:
            return self._make_malformed_event(row)

        readings: dict[str, float | int | None] = {}
        for col in self.SENSOR_COLUMNS:
            readings[col] = row.get(col)

        event = SensorEvent(
            event_id=str(uuid4()),
            event_type="sensor_reading",
            timestamp=row["timestamp"],
            station_id=row["station_id"],
            device_id=row["device_id"],
            readings=readings,
            metadata={
                "source": "producer_v1",
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return event.to_dict()

    def _make_malformed_event(self, row: dict[str, Any]) -> dict[str, Any]:
        """Create a deliberately malformed event for error handling testing."""
        error_type = random.choice(["missing_field", "bad_type", "corrupt"])

        if error_type == "missing_field":
            # Missing required fields
            return {
                "event_id": str(uuid4()),
                "event_type": "sensor_reading",
                # Missing timestamp, station_id, or device_id
                "readings": {"discharge_pressure": row.get("discharge_pressure")},
                "metadata": {"error_injected": True},
            }
        elif error_type == "bad_type":
            # Wrong types in readings
            return {
                "event_id": str(uuid4()),
                "event_type": "sensor_reading",
                "timestamp": row["timestamp"],
                "station_id": row["station_id"],
                "device_id": row["device_id"],
                "readings": {
                    "discharge_pressure": "not_a_number",
                    "air_flow_rate": [1, 2, 3],
                },
                "metadata": {"error_injected": True},
            }
        else:
            # Completely corrupt event
            return {
                "event_id": str(uuid4()),
                "garbage": "this is not a valid event",
                "random_number": random.randint(0, 9999),
            }


def main() -> None:
    """Run the producer standalone for testing."""
    print("Starting sensor event producer...")
    print("Press Ctrl+C to stop.\n")

    q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    producer = SensorEventProducer(db_path="sensor_data.db", event_queue=q)
    producer.start()

    try:
        events_consumed = 0
        while True:
            event = q.get(timeout=10.0)
            if event is None:
                print(f"\nEnd of stream. Total events: {events_consumed}")
                break
            events_consumed += 1
            if events_consumed % 500 == 0:
                print(
                    f"  [{events_consumed} events] "
                    f"Latest: station={event.get('station_id')} "
                    f"device={event.get('device_id')} "
                    f"time={event.get('timestamp', 'N/A')}"
                )
    except KeyboardInterrupt:
        print(f"\nStopping... ({producer.events_produced} events produced)")
        producer.stop()
    except queue.Empty:
        print("\nNo events received for 10 seconds. Stopping.")
        producer.stop()


if __name__ == "__main__":
    main()
