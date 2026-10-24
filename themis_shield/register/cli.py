"""Synthetic Operator CLI. Role switches are assertions, not authentication."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import getpass
import json
from pathlib import Path
import sqlite3
import sys

from ..timezone import business_timezone
from .client import JobStatus
from .models import REASONS
from .sqlite_store import SqliteRegister


def display_time(value: str | None) -> str:
    if value is None:
        return "—"
    return datetime.fromisoformat(value).astimezone(business_timezone()).strftime(
        "%d/%m/%Y %H:%M:%S %Z"
    )


def parser():
    result = argparse.ArgumentParser(
        prog="themis register",
        description="SYNTHETIC REHEARSAL ONLY; not production approval authority.",
    )
    result.add_argument("--db", type=Path)
    result.add_argument(
        "--holiday", action="append", default=[], type=date.fromisoformat,
        help="Approved local holiday in the business timezone, YYYY-MM-DD; repeatable.",
    )
    result.add_argument("--calendar-version", default="NO-HOLIDAYS-SUPPLIED")
    commands = result.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--status", choices=[status.value for status in JobStatus])
    showing = commands.add_parser("show")
    showing.add_argument("job_id")
    for command in ("approve", "refuse", "escalate", "withdraw"):
        decision = commands.add_parser(command)
        decision.add_argument("job_id")
        decision.add_argument(
            "--as-role",
            choices=["operator", "security-admin", "review-board", "requester"],
            default="requester" if command == "withdraw" else "operator",
        )
        decision.add_argument("--reason", required=True, help="Fixed reason code or exact safe template.")
        decision.add_argument(
            "--identity",
            default=None,
            help="Opaque demo actor identity; not printed. No authentication is implied.",
        )
        decision.add_argument("--board-decision-id")
        decision.add_argument(
            "--board-outcome",
            choices=["Standard", "Normal", "Emergency", "Refer-Admin", "Refer-Governance"],
        )
        if command == "approve":
            decision.add_argument("--duration-days", type=int)
    commands.add_parser("expire")
    report = commands.add_parser("report")
    report.add_argument("--unsuppressed-synthetic", action="store_true")
    export = commands.add_parser("export-interim")
    export.add_argument("--output", type=Path, required=True)
    commands.add_parser("reason-codes")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "reason-codes":
            for code, template in REASONS.items():
                print(f"{code}: {template}")
            return 0
        store = SqliteRegister(
            args.db, holidays=frozenset(args.holiday),
            calendar_version=args.calendar_version,
        )
        if args.command == "list":
            print("SYNTHETIC REGISTER — no production approval authority")
            print("ID\tStatus\tRoute\tMarking\tOperator due (local)")
            for row in store.list_jobs(args.status):
                print(
                    f'{row["job_id"]}\t{row["status"]}\t{row["route"]}\t'
                    f'{row["marking_class"]}\t{display_time(row["due_utc"])}'
                )
        elif args.command == "show":
            row = store.get_details(args.job_id)
            # Deliberate safe view, not dumping an object or restricted tables.
            print("SYNTHETIC REGISTER — no production approval authority")
            for label, value in (
                ("ID", row["job_id"]), ("Status", row["status"]),
                ("Summary (guarded, not certified)", row["problem_summary_deid"]),
                ("PII counts", row["pii_counts_json"]),
                ("Documents", row["source_document_count"]),
                ("Words", row["source_word_count"]),
                ("Marking", row["marking_class"]), ("Route", row["route"]),
                ("Sensitive path", row["sensitive_path"]),
                ("Requested days", row["requested_duration_days"]),
                ("Tier", row["tier"]), ("Approver role", row["approver_role"]),
                ("Operator due", display_time(row["due_utc"])),
                ("Expiry", display_time(row["expiry_utc"])),
            ):
                print(f"{label}: {value if value is not None else '—'}")
        elif args.command in {"approve", "refuse", "escalate", "withdraw"}:
            if args.command == "withdraw":
                store.withdraw(
                    args.job_id, args.as_role, args.reason,
                    requester_identity=args.identity or getpass.getuser(),
                )
            else:
                parameters = {
                    "approver_identity": args.identity or "named-synthetic-operator",
                    "board_decision_id": args.board_decision_id,
                    "decision_outcome": args.board_outcome,
                }
                if args.command == "approve":
                    parameters["approved_duration_days"] = args.duration_days
                getattr(store, args.command)(
                    args.job_id, args.as_role, args.reason, **parameters,
                )
            row = store.get_details(args.job_id)
            print(f'SYNTHETIC: {row["job_id"]}: {row["status"]}')
            if row["status"] == JobStatus.APPROVED.value:
                print(f'Expiry: {display_time(row["expiry_utc"])}')
                print("Approval does not send anything; requester must explicitly re-run.")
        elif args.command == "expire":
            for job_id in store.expire_due():
                print(f"SYNTHETIC: {job_id}: Expired")
        elif args.command == "report":
            print(json.dumps(
                store.report(unsuppressed_synthetic=args.unsuppressed_synthetic),
                indent=2, sort_keys=True,
            ))
        elif args.command == "export-interim":
            number = store.export_interim(args.output)
            print(f"SYNTHETIC: exported {number} safe rows; CSV is not approval authority.")
        return 0
    except (ValueError, PermissionError, KeyError, OverflowError, OSError, sqlite3.Error):
        # Never echo exception arguments: they may contain caller input or paths.
        print(
            "Register operation refused or unavailable; no cloud permission. "
            "Check safe reason codes, role, binding, evidence and controlled storage.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
