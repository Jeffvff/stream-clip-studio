from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    utc TEXT NOT NULL,
    local TEXT NOT NULL,
    zone TEXT NOT NULL,
    caption TEXT NOT NULL,
    privacy TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    evidence TEXT,
    remote_url TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS reserved_time
ON jobs(utc) WHERE status IN ('pending','uploading','submitting','uncertain','scheduled','error');
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY, hash TEXT NOT NULL, status TEXT NOT NULL, message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, timeout=20)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def rows(self, statuses=None):
        sql = "SELECT * FROM jobs"
        if statuses:
            sql += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
        return [dict(row) for row in self.connection.execute(sql + " ORDER BY utc, name", statuses or ())]

    def add(self, jobs):
        with self.transaction():
            for job in jobs:
                self.connection.execute(
                    """INSERT INTO jobs(hash,name,path,utc,local,zone,caption,privacy,size)
                       VALUES(:hash,:name,:path,:utc,:local,:zone,:caption,:privacy,:size)""", job,
                )
                self.event(job["hash"], "pending")

    def event(self, digest, status, message=None):
        self.connection.execute("INSERT INTO operations(hash,status,message) VALUES(?,?,?)", (digest, status, message))

    def change(self, digest, expected, status, *, error=None, evidence=None, url=None):
        with self.transaction():
            changed = self.connection.execute(
                """UPDATE jobs SET status=?, error=?, evidence=COALESCE(?, evidence),
                   remote_url=COALESCE(?, remote_url), updated_at=CURRENT_TIMESTAMP,
                   attempts=attempts+? WHERE hash=? AND status=?""",
                (status, error, evidence, url, int(status == "uploading"), digest, expected),
            ).rowcount
            if changed != 1:
                raise RuntimeError(f"Transição concorrente ou inválida: {expected} -> {status}")
            self.event(digest, status, error or evidence)

    def recover(self):
        # Called only while holding the exclusive worker lock.
        for job in self.rows(("uploading", "submitting")):
            prior = job["status"]
            target = "uncertain" if prior == "submitting" else "pending"
            self.change(job["hash"], prior, target, error="Execução anterior interrompida")

    def reschedule(self, jobs):
        with self.transaction():
            for job in jobs:
                changed = self.connection.execute(
                    """UPDATE jobs SET utc=?, local=?, status='pending', error=NULL,
                       updated_at=CURRENT_TIMESTAMP WHERE hash=? AND utc=? AND status=?
                       AND attempts=? AND status IN ('pending','error','expired')""",
                    (job["utc"], job["local"], job["hash"], job["previous_utc"],
                     job["status"], job["attempts"]),
                ).rowcount
                if changed != 1:
                    raise ValueError("A fila mudou depois da prévia de novos horários")
                self.event(job["hash"], "pending", f"Novo horário aprovado: {job['utc']}")

    def close(self):
        self.connection.close()
