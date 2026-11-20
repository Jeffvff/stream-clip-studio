import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from schedule_builder import absolute_time, build_slots


class ScheduleTest(unittest.TestCase):
    now = datetime(2026, 11, 20, 11, tzinfo=timezone.utc)
    config = {"start_date": "2026-11-20", "timezone": "America/Sao_Paulo", "per_day": 2,
              "times": ["12:00", "16:00"], "weekdays": [0, 1, 2, 3, 4]}

    def test_local_weekday_and_reserved_other_hour(self):
        slots = build_slots(self.config, self.now, 3, {"2026-11-20T16:00:00+00:00"})
        self.assertEqual([d.isoformat() for d in slots], [
            "2026-11-20T12:00:00-03:00", "2026-11-23T12:00:00-03:00", "2026-11-23T16:00:00-03:00",
        ])

    def test_intervals_and_lead_minutes(self):
        config = {**self.config, "times": [], "first_time": "08:00", "interval_minutes": 20}
        self.assertEqual([d.strftime("%H:%M") for d in build_slots(config, self.now, 2, set())],
                         ["08:20", "08:40"])

    def test_dst_gap_and_ambiguity(self):
        zone = ZoneInfo("America/New_York")
        self.assertIsNone(absolute_time(datetime(2026, 3, 8, 2, 30), zone))
        self.assertIsNone(absolute_time(datetime(2026, 11, 1, 1, 30), zone))

    def test_horizon(self):
        config = {**self.config, "start_date": "2026-12-01"}
        self.assertEqual(build_slots(config, self.now, 3, set()), [])
