"""Small transactional store. SQLite is a serialized local-demo fallback, not PostgreSQL parity."""

from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path=":memory:"):
        self.path = str(path)
        self.postgres = self.path.startswith(("postgresql://", "postgres://"))
        self.lock = threading.RLock()
        self.depth = 0
        if self.postgres:
            import psycopg

            self.conn = psycopg.connect(self.path, autocommit=True)
            self.conn.execute(Path(__file__).with_name("schema.sql").read_text())
        else:
            self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            self.conn.execute("PRAGMA busy_timeout=10000")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS records (collection TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(collection,id))"
            )
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS allocations (id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, starts REAL NOT NULL, ends REAL NOT NULL, owner TEXT NOT NULL, CHECK(ends > starts))"
            )

    def execute(self, sql, args=()):
        return self.conn.execute(sql.replace("?", "%s") if self.postgres else sql, args)

    @contextmanager
    def transaction(self):
        with self.lock:
            outer = self.depth == 0
            if outer:
                self.conn.execute("BEGIN" if self.postgres else "BEGIN IMMEDIATE")
                if self.postgres:
                    self.conn.execute("SELECT pg_advisory_xact_lock(842731)")
            self.depth += 1
            try:
                yield self
                if outer:
                    self.conn.execute("COMMIT")
            except BaseException:
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            finally:
                self.depth -= 1

    def get(self, collection, id):
        with self.lock:
            row = self.execute("SELECT body FROM records WHERE collection=? AND id=?", (collection, id)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, collection, id, value):
        with self.lock:
            self.execute(
                "INSERT INTO records(collection,id,body) VALUES(?,?,?) ON CONFLICT(collection,id) DO UPDATE SET body=excluded.body",
                (collection, id, json.dumps(value)),
            )
        return value

    def list(self, collection):
        with self.lock:
            return [
                json.loads(r[0])
                for r in self.execute(
                    "SELECT body FROM records WHERE collection=? ORDER BY id", (collection,)
                ).fetchall()
            ]

    def delete(self, collection, id):
        with self.lock:
            self.execute("DELETE FROM records WHERE collection=? AND id=?", (collection, id))

    def allocate(self, resource_id, starts, ends, owner):
        if self.execute(
            "SELECT id FROM allocations WHERE resource_id=? AND starts < ? AND ends > ?", (resource_id, ends, starts)
        ).fetchone():
            raise ValueError("Resource no longer available")
        self.execute(
            "INSERT INTO allocations(id,resource_id,starts,ends,owner) VALUES(?,?,?,?,?)",
            (owner + ":" + resource_id, resource_id, starts, ends, owner),
        )

    def release(self, owner):
        self.execute("DELETE FROM allocations WHERE owner=?", (owner,))

    def available(self, resources, starts, ends):
        return all(
            not self.execute(
                "SELECT id FROM allocations WHERE resource_id=? AND starts < ? AND ends > ?", (r, ends, starts)
            ).fetchone()
            for r in resources
        )

    def close(self):
        self.conn.close()
