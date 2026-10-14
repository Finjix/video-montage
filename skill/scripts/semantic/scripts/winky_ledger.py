#!/usr/bin/env python3
"""Portable, task-local checkpoint ledger. It never calls an external model."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  summary TEXT NOT NULL,
  state_ref TEXT,
  source_ref TEXT,
  correlation_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  decision_key TEXT NOT NULL,
  decision_value TEXT NOT NULL,
  authority TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def required(value: str, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "ledger.sqlite")
    connection.executescript(SCHEMA)
    return connection


def checkpoint(args: argparse.Namespace) -> dict:
    task_id = required(args.task_id, "task_id")
    phase = required(args.phase, "phase")
    summary = required(args.summary, "summary")
    correlation = str(args.correlation_id or uuid.uuid4())
    with connect(args.root) as connection:
        connection.execute(
            "INSERT INTO checkpoints(task_id,phase,occurred_at,summary,state_ref,source_ref,correlation_id) VALUES(?,?,?,?,?,?,?)",
            (task_id, phase, now_iso(), summary, args.state_ref or None, args.source_ref or None, correlation),
        )
    return {"action": "checkpoint_recorded", "task_id": task_id, "phase": phase, "correlation_id": correlation}


def decision(args: argparse.Namespace) -> dict:
    task_id = required(args.task_id, "task_id")
    with connect(args.root) as connection:
        connection.execute(
            "INSERT INTO decisions(task_id,decision_key,decision_value,authority,source_ref,occurred_at) VALUES(?,?,?,?,?,?)",
            (task_id, required(args.key, "key"), required(args.value, "value"), required(args.authority, "authority"), required(args.source_ref, "source_ref"), now_iso()),
        )
    return {"action": "decision_recorded", "task_id": task_id}


def status(args: argparse.Namespace) -> dict:
    task_id = required(args.task_id, "task_id")
    with connect(args.root) as connection:
        checkpoints = connection.execute(
            "SELECT phase,occurred_at,summary,state_ref,source_ref,correlation_id FROM checkpoints WHERE task_id=? ORDER BY checkpoint_id",
            (task_id,),
        ).fetchall()
        decisions = connection.execute(
            "SELECT decision_key,decision_value,authority,source_ref,occurred_at FROM decisions WHERE task_id=? ORDER BY decision_id",
            (task_id,),
        ).fetchall()
    return {
        "task_id": task_id,
        "checkpoints": [dict(zip(("phase", "occurred_at", "summary", "state_ref", "source_ref", "correlation_id"), row)) for row in checkpoints],
        "decisions": [dict(zip(("key", "value", "authority", "source_ref", "occurred_at"), row)) for row in decisions],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    check = commands.add_parser("checkpoint")
    check.add_argument("--task-id", required=True)
    check.add_argument("--phase", required=True)
    check.add_argument("--summary", required=True)
    check.add_argument("--state-ref")
    check.add_argument("--source-ref")
    check.add_argument("--correlation-id")
    choose = commands.add_parser("decision")
    choose.add_argument("--task-id", required=True)
    choose.add_argument("--key", required=True)
    choose.add_argument("--value", required=True)
    choose.add_argument("--authority", choices=("user", "local_rule", "system"), required=True)
    choose.add_argument("--source-ref", required=True)
    current = commands.add_parser("status")
    current.add_argument("--task-id", required=True)
    args = parser.parse_args()
    if args.command == "init":
        with connect(args.root):
            pass
        result = {"action": "initialized", "root": str(args.root.resolve())}
    elif args.command == "checkpoint":
        result = checkpoint(args)
    elif args.command == "decision":
        result = decision(args)
    else:
        result = status(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
