import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from database import Database


def job(digest="a", utc="2026-11-22T15:00:00+00:00"):
    return {"hash": digest, "name": "um.mp4", "path": "um.mp4", "utc": utc,
            "local": utc, "zone": "America/Sao_Paulo", "caption": "Corte", "privacy": "private", "size": 1}


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue.sqlite3"
        self.db = Database(self.path)
        self.addCleanup(self.db.close)

    def test_duplicate_hash_and_slot_conflict_roll_back(self):
        self.db.add([job()])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add([job("b", "2026-11-22T16:00:00+00:00"), job("c")])
        self.assertEqual([row["hash"] for row in self.db.rows()], ["a"])

    def test_recovery_claimed_vs_submitted(self):
        self.db.add([job(), job("b", "2026-11-22T16:00:00+00:00")])
        self.db.change("a", "pending", "uploading")
        self.db.change("b", "pending", "uploading")
        self.db.change("b", "uploading", "submitting")
        recovery = Database(self.path)
        self.addCleanup(recovery.close)
        recovery.recover()
        states = [(row["status"], row["attempts"]) for row in recovery.rows()]
        self.assertEqual(states, [("pending", 1), ("uncertain", 1)])

    def test_expired_rescheduling_and_uncertain_block(self):
        self.db.add([job()])
        self.db.change("a", "pending", "expired")
        new = {**self.db.rows()[0], "previous_utc": job()["utc"], "utc": "2026-11-22T17:00:00+00:00"}
        self.db.reschedule([new])
        self.assertEqual(self.db.rows()[0]["status"], "pending")
        self.assertEqual(self.db.rows()[0]["utc"], new["utc"])
        self.db.change("a", "pending", "uncertain")
        with self.assertRaises(ValueError):
            self.db.reschedule([{**new, "previous_utc": new["utc"], "status": "uncertain"}])
