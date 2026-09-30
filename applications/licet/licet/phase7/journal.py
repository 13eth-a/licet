"""crash-safe mutation quarantine for real portal sessions"""
import sqlite3
from pathlib import Path


class MutationJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (operation TEXT PRIMARY KEY, record TEXT NOT NULL, state TEXT NOT NULL)')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def reserve(self, record: str, operation: str) -> bool:
        if not record or not operation:
            return False
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM attempts WHERE operation=? OR (record=? AND state='UNKNOWN')", (operation, record)).fetchone():
                return False
            db.execute("INSERT INTO attempts VALUES (?, ?, 'UNKNOWN')", (operation, record))
        return True

    def verified(self, record: str, operation: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE attempts SET state='VERIFIED_SUCCESS' WHERE record=? AND operation=?", (record, operation))
