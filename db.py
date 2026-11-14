"""
SQLite persistence layer for Gemini Web2API dashboard.
Tables: requests, accounts, config
Auto-retention: requests older than 7 days are purged on init.
"""
import sqlite3
import time
import os
import threading

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.db")
_local = threading.local()


def _conn():
    """Thread-local connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    """Create tables and run retention purge."""
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            model TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            duration_ms REAL DEFAULT 0,
            status INTEGER DEFAULT 200,
            error TEXT,
            account_name TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp);
        CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model);

        CREATE TABLE IF NOT EXISTS accounts (
            name TEXT PRIMARY KEY,
            cookie_file TEXT DEFAULT '',
            auth_user TEXT,
            priority INTEGER DEFAULT 100,
            enabled INTEGER DEFAULT 1,
            healthy INTEGER DEFAULT 1,
            error_disabled INTEGER DEFAULT 0,
            total_requests INTEGER DEFAULT 0,
            total_errors INTEGER DEFAULT 0,
            consecutive_errors INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            cookie_expiry REAL DEFAULT 0,
            usage_limit INTEGER DEFAULT 0,
            usage_count INTEGER DEFAULT 0,
            last_used REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # Retention: delete requests older than 7 days
    cutoff = time.time() - 7 * 86400
    conn.execute("DELETE FROM requests WHERE timestamp < ?", (cutoff,))
    conn.commit()


def log_request(model, prompt_tokens, completion_tokens, total_tokens,
                duration_ms, status, error=None, account_name=""):
    """Insert a request record."""
    conn = _conn()
    conn.execute(
        """INSERT INTO requests
           (timestamp, model, prompt_tokens, completion_tokens, total_tokens,
            duration_ms, status, error, account_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (time.time(), model or "unknown", prompt_tokens or 0, completion_tokens or 0,
         total_tokens or 0, duration_ms or 0, status or 200, error or "", account_name or ""),
    )
    conn.commit()


def get_stats():
    """Aggregate stats from the requests table."""
    conn = _conn()
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(prompt_tokens),0) AS prompt,
                  COALESCE(SUM(completion_tokens),0) AS completion,
                  COALESCE(SUM(total_tokens),0) AS total_tok,
                  COALESCE(AVG(duration_ms),0) AS avg_ms,
                  SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors
           FROM requests"""
    ).fetchone()
    return {
        "total_requests": row["total"],
        "prompt_tokens": row["prompt"],
        "completion_tokens": row["completion"],
        "total_tokens": row["total_tok"],
        "avg_latency_ms": round(row["avg_ms"], 2),
        "error_count": row["errors"],
    }


def get_accounts():
    """Return all account rows."""
    conn = _conn()
    rows = conn.execute("SELECT * FROM accounts ORDER BY priority").fetchall()
    return [dict(r) for r in rows]


def upsert_account(name, **kwargs):
    """Insert or update an account row."""
    conn = _conn()
    existing = conn.execute("SELECT name FROM accounts WHERE name=?", (name,)).fetchone()
    if existing:
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values())
        conn.execute(f"UPDATE accounts SET {sets} WHERE name=?", vals + [name])
    else:
        cols = ["name"] + list(kwargs.keys())
        placeholders = ", ".join(["?"] * len(cols))
        conn.execute(
            f"INSERT INTO accounts ({', '.join(cols)}) VALUES ({placeholders})",
            [name] + list(kwargs.values()),
        )
    conn.commit()


def update_account(name, **kwargs):
    """Update an existing account row."""
    conn = _conn()
    if kwargs:
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values())
        conn.execute(f"UPDATE accounts SET {sets} WHERE name=?", vals + [name])
        conn.commit()


def delete_account(name):
    conn = _conn()
    conn.execute("DELETE FROM accounts WHERE name=?", (name,))
    conn.commit()


def get_requests_paginated(page=1, limit=50, model=None, status=None, account=None):
    """Return paginated request log."""
    conn = _conn()
    where = []
    params = []
    if model:
        where.append("model = ?")
        params.append(model)
    if status:
        where.append("status = ?")
        params.append(int(status))
    if account:
        where.append("account_name = ?")
        params.append(account)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * limit
    total = conn.execute(f"SELECT COUNT(*) AS cnt FROM requests{clause}", params).fetchone()["cnt"]
    rows = conn.execute(
        f"SELECT * FROM requests{clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {
        "requests": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit if limit else 1,
    }


def get_token_usage_over_time(period="hour"):
    """Return token usage bucketed over time."""
    conn = _conn()
    now = time.time()
    if period == "hour":
        start = now - 3600
        bucket_size = 60  # 1-min buckets
    elif period == "week":
        start = now - 7 * 86400
        bucket_size = 3600  # 1-hour buckets
    else:  # day
        start = now - 86400
        bucket_size = 300  # 5-min buckets

    rows = conn.execute(
        """SELECT
             CAST(timestamp / ? AS INTEGER) * ? AS bucket,
             SUM(prompt_tokens) AS prompt,
             SUM(completion_tokens) AS completion,
             SUM(total_tokens) AS total_tok,
             COUNT(*) AS count
           FROM requests
           WHERE timestamp >= ?
           GROUP BY bucket
           ORDER BY bucket""",
        (bucket_size, bucket_size, start),
    ).fetchall()
    return {
        "period": period,
        "bucket_size_sec": bucket_size,
        "buckets": [dict(r) for r in rows],
    }


def get_config():
    conn = _conn()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_config(key, value):
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()


def get_model_breakdown():
    """Return per-model request counts and token sums."""
    conn = _conn()
    rows = conn.execute(
        """SELECT model, COUNT(*) AS count,
                  SUM(total_tokens) AS tokens
           FROM requests GROUP BY model ORDER BY count DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


# Initialise the schema on import.
# init_db() was defined but never called, so a fresh install had no tables and
# every dashboard query (/requests, /tokens, model breakdown) raised
# "no such table: requests" — which surfaced as a 502 on the status page.
try:
    init_db()
except Exception as _e:  # never let a metrics-table problem stop the API
    import sys as _sys
    print(f"[db] init_db failed: {_e}", file=_sys.stderr)
