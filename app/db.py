"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/db.py
Purpose: All SQLite access in one file (policies, prior claims, garages, claims, audit log).

Why SQLite? Zero setup for learners. In production you would point the same functions at
PostgreSQL (Amazon RDS) because SQLite allows only one writer at a time and lives inside one container.
"""

import json
import sqlite3
from datetime import datetime

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
    policy_number     TEXT PRIMARY KEY,
    holder_name       TEXT,
    phone             TEXT,
    vehicle_reg       TEXT,
    make_model        TEXT,
    engine_cc         INTEGER,
    registration_date TEXT,      -- first registration, used for metal depreciation
    policy_type       TEXT,      -- COMPREHENSIVE or THIRD_PARTY
    start_date        TEXT,
    end_date          TEXT,
    idv               REAL,      -- Insured Declared Value (max the insurer will pay)
    zero_dep          INTEGER    -- 1 if Zero Depreciation add-on was bought
);
CREATE TABLE IF NOT EXISTS prior_claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_number TEXT,
    claim_date    TEXT,
    amount        REAL
);
CREATE TABLE IF NOT EXISTS garages (
    garage_id TEXT PRIMARY KEY,
    name      TEXT,
    city      TEXT,
    watchlist INTEGER            -- 1 = flagged by the fraud team
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id        TEXT PRIMARY KEY,
    policy_number   TEXT,
    docs_dir        TEXT,
    thread_id       TEXT,        -- LangGraph thread, used to resume after human review
    status          TEXT,        -- PROCESSING, PENDING_ADJUSTER, PENDING_SIU, APPROVED, REJECTED, FAILED
    route           TEXT,
    recommendation  TEXT,
    fraud_score     INTEGER,
    payable_amount  REAL,
    final_decision  TEXT,
    final_amount    REAL,
    reviewer        TEXT,
    details_json    TEXT,        -- snapshot of extraction, coverage, assessment, fraud for the UI
    letter          TEXT,
    error           TEXT,
    created_at      TEXT,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id  TEXT,
    ts        TEXT,
    step      TEXT,
    actor     TEXT,              -- 'agent' or the reviewer's name
    details   TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """Open the database; rows behave like dicts (row['policy_number'])."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Reference data (read-only for the agent)
# ---------------------------------------------------------------------------
def get_policy(policy_number: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM policies WHERE policy_number = ?", (policy_number.strip(),)).fetchone()
    return dict(row) if row else None


def count_prior_claims(policy_number: str, before_date: str, months: int = 12) -> int:
    """How many claims this policy had in the `months` before the accident."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM prior_claims WHERE policy_number = ? "
            "AND claim_date < ? AND claim_date >= date(?, ?)",
            (policy_number, before_date, before_date, f"-{months} months"),
        ).fetchone()
    return row[0]


def get_garage(garage_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM garages WHERE garage_id = ?", (garage_id.strip().upper(),)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Claims (written by the agent and by reviewers)
# ---------------------------------------------------------------------------
def save_claim(claim_id: str, **fields) -> None:
    """Insert the claim if new, otherwise update only the given fields."""
    fields["updated_at"] = now()
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        if not exists:
            conn.execute("INSERT INTO claims (claim_id, created_at) VALUES (?, ?)", (claim_id, now()))
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE claims SET {sets} WHERE claim_id = ?", (*fields.values(), claim_id))


def get_claim(claim_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    if not row:
        return None
    claim = dict(row)
    claim["details"] = json.loads(claim.pop("details_json") or "{}")
    return claim


def list_claims(status: str | None = None) -> list[dict]:
    query = ("SELECT claim_id, policy_number, status, route, recommendation, fraud_score, "
             "payable_amount, final_decision, final_amount, reviewer, updated_at FROM claims")
    params: tuple = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    with connect() as conn:
        rows = conn.execute(query + " ORDER BY updated_at DESC", params).fetchall()
    return [dict(r) for r in rows]


def reset_claim(claim_id: str) -> None:
    """Remove an old run of a claim (used when re-processing sample claims and in evals)."""
    with connect() as conn:
        conn.execute("DELETE FROM claims WHERE claim_id = ?", (claim_id,))
        conn.execute("DELETE FROM audit_log WHERE claim_id = ?", (claim_id,))


# ---------------------------------------------------------------------------
# Audit trail: every step the agent or a human takes is recorded here
# ---------------------------------------------------------------------------
def add_audit(claim_id: str, step: str, actor: str, details: dict) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit_log (claim_id, ts, step, actor, details) VALUES (?, ?, ?, ?, ?)",
                     (claim_id, now(), step, actor, json.dumps(details, default=str)))


def get_audit(claim_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT ts, step, actor, details FROM audit_log WHERE claim_id = ? ORDER BY id",
                            (claim_id,)).fetchall()
    return [{**dict(r), "details": json.loads(r["details"])} for r in rows]
