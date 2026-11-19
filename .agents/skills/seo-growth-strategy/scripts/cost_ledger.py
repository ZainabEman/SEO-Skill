"""Atomic spending reservations on one authoritative SQLite volume."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import sqlite3

from run_state import now

UNIT = Decimal("0.000001")

def amount(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > 1_000_000:
            raise ValueError("Invalid USD amount")
        scaled = number / UNIT
        if scaled != scaled.to_integral_value():
            raise ValueError("Use at most six decimal places")
        return int(scaled)
    except InvalidOperation as exc:
        raise ValueError("Unknown USD amount") from exc

def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()

def plan(run_id, tool, parameters, maximum, pricing_source, max_credits, account):
    if not pricing_source or not account or type(max_credits) is not int or max_credits < 0:
        raise ValueError("Verified pricing, account and maximum credits are required")
    payload = {"run": run_id, "tool": tool, "parameters": parameters, "max_usd_units": amount(maximum),
               "max_credits": max_credits, "pricing_source": pricing_source, "account": account}
    return {**payload, "fingerprint": fingerprint(payload)}

@dataclass(frozen=True)
class Balance:
    credits: int
    observed_from: str

class Ledger:
    def __init__(self, path, per_run="5.00", per_month="25.00"):
        self.path = path
        self.per_run = amount(per_run)
        self.per_month = amount(per_month)
        with closing(sqlite3.connect(path, timeout=20)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, run TEXT, account TEXT, booked TEXT, approved_by TEXT,
                reserved INTEGER, actual INTEGER, reserved_credits INTEGER, actual_credits INTEGER,
                state TEXT, request TEXT, settled TEXT)""")

    def reserve(self, request, approval, balance):
        payload = {k: v for k, v in request.items() if k != "fingerprint"}
        key = fingerprint(payload)
        maximum = payload["max_usd_units"]
        if (request["fingerprint"] != key or not approval or approval.get("fingerprint") != key
                or not approval.get("approved_by") or amount(approval.get("usd")) < maximum
                or type(approval.get("credits")) is not int or approval["credits"] < payload["max_credits"]):
            raise ValueError("Exact explicit approval is required")
        booked = now()
        if (not isinstance(balance, Balance) or type(balance.credits) is not int
                or balance.credits < payload["max_credits"]):
            raise ValueError("Confirmed sufficient provider balance is required")
        observed = datetime.fromisoformat(balance.observed_from)
        age = (datetime.fromisoformat(booked) - observed).total_seconds()
        if not 0 <= age <= 300:
            raise ValueError("Refresh the provider balance")
        with closing(sqlite3.connect(self.path, timeout=20, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM calls WHERE id = ?", (key,)).fetchone():
                raise ValueError("Call already reserved or completed")
            run_used = db.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM calls WHERE run = ?",
                                  (payload["run"],)).fetchone()[0]
            month_used, inflight = db.execute("""SELECT
                COALESCE(SUM(CASE WHEN substr(booked,1,7)=? OR actual IS NULL
                     THEN COALESCE(actual,reserved) ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN actual IS NULL THEN reserved_credits
                    WHEN settled >= ? THEN actual_credits ELSE 0 END),0)
                FROM calls WHERE account=?""", (booked[:7], observed.isoformat(), payload["account"])).fetchone()
            if inflight + payload["max_credits"] > balance.credits:
                raise ValueError("Provider balance already reserved")
            if run_used + maximum > self.per_run or month_used + maximum > self.per_month:
                raise ValueError("Budget ceiling exceeded")
            if db.execute("SELECT 1 FROM calls WHERE account=? AND state='overage'", (payload["account"],)).fetchone():
                raise ValueError("Provider overage needs administrator review")
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,NULL,?,NULL,'pending',?,NULL)",
                       (key, payload["run"], payload["account"], booked, approval["approved_by"],
                        maximum, payload["max_credits"], json.dumps(payload, sort_keys=True)))
            db.commit()
        return key

    def reconcile(self, key, usd, credits):
        actual = amount(usd)
        if type(credits) is not int or credits < 0:
            raise ValueError("Actual provider charges are required")
        with closing(sqlite3.connect(self.path, timeout=20, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reserved,reserved_credits,state FROM calls WHERE id=?", (key,)).fetchone()
            if not row or row[2] != "pending":
                raise ValueError("No pending reservation")
            state = "overage" if actual > row[0] or credits > row[1] else "settled"
            db.execute("UPDATE calls SET actual=?,actual_credits=?,state=?,settled=? WHERE id=?",
                       (actual, credits, state, now(), key))
            db.commit()
        return state
