"""Generate sport-telemetry activity files, including a corrected re-delivery.

Two wrinkles make the medallion layers earn their keep:

* one activity is delivered **twice** — the second file corrects the first — so
  Silver has to upsert rather than append;
* the values are the sensitive kind a platform is judged on: precise GPS, heart
  rate, and athlete names.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

EVENTS = (
    ("ugm-100k", "UGM Trail 100K", 100.0),
    ("dieng-42k", "Dieng Caldera 42K", 42.0),
    ("semeru-21k", "Semeru Hills 21K", 21.0),
    ("city-5k", "City Night Run 5K", 5.0),
)
NAMES = ("ada lovelace", "bayu pratama", "chen wei", "dewi kartika", "eko santoso", "farah amin")
START = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)


@dataclass
class GenerationStats:
    files: int
    activities: int
    corrected: int


def _activity(rng: random.Random, event: tuple, index: int, started: datetime) -> dict:
    event_id, name, distance_km = event
    hours = distance_km / rng.uniform(6.5, 9.5)
    pace = round(hours * 3600 / max(distance_km, 1), 1)
    return {
        "activity_id": f"{event_id}-a{index:03d}",
        "event_id": event_id,
        "event_name": name,
        "athlete_name": rng.choice(NAMES),
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "distance_km": round(distance_km * rng.uniform(0.97, 1.03), 2),
        "duration_s": int(hours * 3600),
        "pace_sec_per_km": pace,
        "elevation_gain_m": int(distance_km * rng.uniform(18, 42)),
        "heart_rate_avg": rng.randint(128, 168),
        "heart_rate_max": rng.randint(168, 192),
        # precise enough to be a home address, which is the point of masking it
        "gps_lat": round(-7.6 + rng.uniform(-0.45, 0.45), 6),
        "gps_lon": round(110.4 + rng.uniform(-0.45, 0.45), 6),
    }


def generate(
    source_dir: Path, activities_per_event: int = 6, seed: int = 31, batches: int = 2
) -> GenerationStats:
    """Write one file per batch, and redeliver one activity with corrected values."""
    rng = random.Random(seed)
    source_dir.mkdir(parents=True, exist_ok=True)

    all_activities: list[dict] = []
    for batch in range(batches):
        stamp = START + timedelta(weeks=batch)
        payload = []
        for event in EVENTS:
            for index in range(activities_per_event):
                payload.append(_activity(rng, event, index + batch * activities_per_event, stamp))
        (source_dir / f"activities-{batch:02d}.json").write_text(
            "\n".join(json.dumps(record) for record in payload) + "\n"
        )
        all_activities.extend(payload)

    # Re-delivery of one activity with a corrected heart rate and distance, which
    # Silver must upsert rather than duplicate.
    corrected = dict(all_activities[0])
    corrected["heart_rate_avg"] = corrected["heart_rate_avg"] + 7
    corrected["distance_km"] = round(corrected["distance_km"] + 0.8, 2)
    (source_dir / "activities-99-corrected.json").write_text(json.dumps(corrected) + "\n")

    return GenerationStats(files=batches + 1, activities=len(all_activities), corrected=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/source"))
    parser.add_argument("--activities-per-event", type=int, default=6)
    parser.add_argument("--batches", type=int, default=2)
    parser.add_argument("--seed", type=int, default=31)
    args = parser.parse_args()

    stats = generate(args.source, args.activities_per_event, args.seed, args.batches)
    print(json.dumps(stats.__dict__, indent=2))


if __name__ == "__main__":
    main()
