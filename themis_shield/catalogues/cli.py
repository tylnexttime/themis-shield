"""Operator commands for the v0.3 catalogues: ``themis kmr``, ``themis fpc`` and ``themis tripwire``.

Values to fingerprint are read through a hidden prompt, never from the command line (shell history and process
lists keep argv). Patterns and phrases are read from a prompt too. Identities are opaque labels; nothing here
authenticates anyone.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys

from .false_positives import DEFAULT_EXPIRY_DAYS, STRENGTHS, FalsePositiveCatalogue
from .known_miss import ENTRY_KINDS, SOURCES, STATUSES, KnownMissRegister
from .store import CatalogueError


def _config(path: str | None) -> dict:
    from ..integration import CONFIG_ENV, load_config
    candidate = path or os.environ.get(CONFIG_ENV)
    if not candidate:
        return {}
    return load_config(candidate)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="gate configuration (default: THEMIS_CONFIG)")
    parser.add_argument("--db", type=Path, default=None, help="catalogue file (default: from the configuration)")


def _identity(args) -> str:
    return getattr(args, "identity", None) or getpass.getuser()


def _refused(error: BaseException) -> int:
    # Catalogue errors carry fixed text (never a value); anything else is reported by class only.
    if isinstance(error, CatalogueError):
        print(f"Refused: {error}", file=sys.stderr)
    elif isinstance(error, KeyError):
        print("Refused: no such entry.", file=sys.stderr)
    else:
        print(f"Refused or unavailable ({type(error).__name__}).", file=sys.stderr)
    return 2


# -- themis kmr ---------------------------------------------------------------------------------------------------


def kmr_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="themis kmr",
        description="Known-miss register. Never stores the values it protects.",
    )
    _common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="propose an entry (patterns and values are prompted, never argv)")
    add.add_argument("--kind", choices=ENTRY_KINDS, required=True)
    add.add_argument("--entity-type", required=True, help="a gate label, e.g. PERSON, INTERNAL_ID")
    add.add_argument("--source", choices=SOURCES, default="operator")
    add.add_argument("--job", default=None, help="the job that showed the miss (JOB-YYYY-NNNN)")
    add.add_argument("--regression-case", default=None)
    add.add_argument("--identity", default=None)
    listing = commands.add_parser("list")
    listing.add_argument("--status", choices=STATUSES)
    listing.add_argument("--show-patterns", action="store_true")
    for name in ("verify", "reject"):
        command = commands.add_parser(name)
        command.add_argument("entry_id")
        command.add_argument("--identity", default=None)
        command.add_argument("--regression-case", default=None)
    promote = commands.add_parser("promote", help="verified -> in_sweep, or in_sweep -> built_in")
    promote.add_argument("entry_id")
    promote.add_argument("--to", choices=("in_sweep", "built_in"), required=True)
    promote.add_argument("--release", default=None, help="release tag that carries the fix (built_in)")
    promote.add_argument("--regression-case", default=None)
    promote.add_argument("--identity", default=None)
    stub = commands.add_parser("export-regression-stub", help="write a value-free regression case stub")
    stub.add_argument("entry_id")
    stub.add_argument("--out", type=Path, required=True, help="directory for the stub")
    stub.add_argument("--identity", default=None)
    refresh = commands.add_parser("refresh", help="attest the register is current (resets its age)")
    refresh.add_argument("--identity", default=None)
    rotate = commands.add_parser("rotate-key", help="replace the fingerprint key; invalidates fingerprints")
    rotate.add_argument("--confirm-invalidates", type=int, required=True)
    rotate.add_argument("--identity", default=None)
    return parser


def _kmr_store(args, protector):
    from .gate import settings
    config = _config(args.config)
    path = args.db or settings(config)["known_miss"]["path"]
    if protector is None and (args.command in {"add", "rotate-key"}):
        from ..protectors import build_protector
        protector = build_protector(config.get("vault") or {})
    return KnownMissRegister(path, protector=protector, create=True)


def kmr_main(argv=None, *, protector=None, prompt=getpass.getpass, read=input) -> int:
    args = kmr_parser().parse_args(argv)
    try:
        store = _kmr_store(args, protector)
        who = _identity(args)
        if args.command == "add":
            if args.kind == "fingerprint":
                value = prompt("Value to fingerprint (hidden; hashed, never stored): ")
                if prompt("Again, to confirm (hidden): ") != value:
                    raise CatalogueError("the two entries differ")
                entry = store.add_fingerprint(
                    value, entity_type=args.entity_type, source=args.source, proposed_by=who,
                    job_id=args.job, regression_case_id=args.regression_case,
                )
                del value
            else:
                pattern = read(f"{args.kind.capitalize()} pattern (a shape, never a value): ")
                entry = store.add_pattern(
                    args.kind, pattern, entity_type=args.entity_type, source=args.source, proposed_by=who,
                    job_id=args.job, regression_case_id=args.regression_case,
                )
            print(f"{entry}: proposed. A different person verifies it: themis kmr verify {entry}")
        elif args.command == "list":
            print("ID\tKind\tEntity\tSource\tStatus\tJob\tRegression case" + ("\tPattern" if args.show_patterns else ""))
            for row in store.entries(args.status):
                cells = [row["entry_id"], row["entry_kind"], row["entity_type"], row["source"], row["status"],
                         row["job_id"] or "-", row["regression_case_id"] or "-"]
                if args.show_patterns:
                    cells.append(row["pattern"] or "(fingerprint)")
                print("\t".join(cells))
        elif args.command in {"verify", "reject"}:
            status = store.transition(
                args.entry_id, "verified" if args.command == "verify" else "rejected",
                actor_identity=who, regression_case_id=args.regression_case,
            )
            print(f"{args.entry_id}: {status}")
        elif args.command == "promote":
            status = store.transition(
                args.entry_id, args.to, actor_identity=who, regression_case_id=args.regression_case,
                release_tag=args.release,
            )
            print(f"{args.entry_id}: {status}")
        elif args.command == "export-regression-stub":
            stub = store.regression_stub(args.entry_id, actor_identity=who)
            args.out.mkdir(parents=True, exist_ok=True)
            target = args.out / f"{stub['case_id']}.json"
            with target.open("x", encoding="utf-8") as stream:
                json.dump(stub, stream, indent=2, sort_keys=True)
                stream.write("\n")
            print(f"{args.entry_id}: regression stub {stub['case_id']} written; write the synthetic text yourself.")
        elif args.command == "refresh":
            print(f"Known-miss register refreshed; version {store.refresh(actor_identity=who)}.")
        elif args.command == "rotate-key":
            count = store.rotate_key(actor_identity=who, confirm_invalidates=args.confirm_invalidates)
            print(f"Fingerprint key rotated; {count} fingerprint entries invalidated (add them again).")
        return 0
    except (CatalogueError, KeyError, ValueError, OSError, sqlite3.Error) as error:
        return _refused(error)


# -- themis fpc ---------------------------------------------------------------------------------------------------


def fpc_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="themis fpc",
        description="Reviewed false-positive catalogue. No in-document markers: entries live only here.",
    )
    _common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser("propose", help="propose an entry (phrase and context are prompted)")
    propose.add_argument("--label", required=True)
    propose.add_argument("--strength", choices=STRENGTHS, default="no_hold")
    propose.add_argument("--profile", default=None, help="scope (default: [false_positives] scope)")
    propose.add_argument("--no-phrase", action="store_true", help="context pattern only")
    propose.add_argument("--with-context", action="store_true", help="also prompt for a context pattern")
    propose.add_argument("--expires-days", type=int, default=DEFAULT_EXPIRY_DAYS)
    propose.add_argument("--job", default=None, help="the run that showed the false positive")
    propose.add_argument("--identity", default=None)
    commands.add_parser("list")
    for name in ("accept", "expire"):
        command = commands.add_parser(name)
        command.add_argument("entry_id")
        command.add_argument("--identity", default=None)
    return parser


def fpc_main(argv=None, *, read=input) -> int:
    args = fpc_parser().parse_args(argv)
    try:
        from .gate import settings
        config = _config(args.config)
        values = settings(config)["false_positives"]
        store = FalsePositiveCatalogue(args.db or values["path"], create=True)
        who = _identity(args)
        if args.command == "propose":
            phrase = None if args.no_phrase else read("Phrase (exactly as it appears; not personal data): ")
            context = (
                read("Context pattern (regular expression near the finding): ")
                if args.with_context or args.no_phrase else None
            )
            entry = store.propose(
                profile=args.profile or values["scope"], label=args.label, strength=args.strength,
                phrase=phrase, context=context, expires_days=args.expires_days, proposed_by=who,
                source_job_id=args.job,
            )
            print(f"{entry}: proposed. A different person accepts it: themis fpc accept {entry}")
        elif args.command == "list":
            print("ID\tProfile\tLabel\tStrength\tStatus\tExpires (UTC)\tPhrase\tContext")
            for row in store.entries():
                print("\t".join(str(row[name] if row[name] is not None else "-") for name in (
                    "entry_id", "profile", "label", "strength", "status", "expires_utc", "phrase", "context",
                )))
        elif args.command == "accept":
            store.accept(args.entry_id, actor_identity=who)
            print(f"{args.entry_id}: accepted")
        elif args.command == "expire":
            store.expire(args.entry_id, actor_identity=who)
            print(f"{args.entry_id}: expired")
        return 0
    except (CatalogueError, KeyError, ValueError, OSError, sqlite3.Error) as error:
        return _refused(error)


# -- themis tripwire ----------------------------------------------------------------------------------------------


def tripwire_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="themis tripwire",
        description="Cloud trip-wire results: review flags and track flag precision.",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--register-db", type=Path, default=None, help="job register (default: from config)")
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("review", help="an Operator's verdict on a job's pending flag")
    review.add_argument("job_id")
    verdict = review.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--confirm", action="store_true", help="a real residual: the job is refused")
    verdict.add_argument("--reject", action="store_true", help="a false flag: the job may be re-sent")
    review.add_argument("--identity", default=None)
    review.add_argument("--as-role", choices=("operator", "security-admin"), default="operator")
    listing = commands.add_parser("list")
    listing.add_argument("--job", default=None)
    commands.add_parser("stats")
    return parser


def tripwire_main(argv=None, *, register=None) -> int:
    args = tripwire_parser().parse_args(argv)
    try:
        if register is None:
            from ..register.sqlite_store import SqliteRegister
            config = _config(args.config)
            path = args.register_db or (config.get("register") or {}).get("path") or None
            register = SqliteRegister(Path(path).expanduser() if path else None)
        if args.command == "review":
            status = register.review_tripwire(
                args.job_id, "confirmed" if args.confirm else "rejected",
                reviewer_identity=args.identity or getpass.getuser(), reviewer_role=args.as_role,
            )
            print(f"{args.job_id}: {status.value}")
            if args.confirm:
                print(
                    "Confirmed. Propose the miss to the known-miss register (a rule, or a fingerprint for a "
                    f"high-entropy string): themis kmr add --source cloud_tripwire --job {args.job_id} "
                    "--kind <rule|context|fingerprint> --entity-type <LABEL>"
                )
        elif args.command == "list":
            print("Record\tJob\tOutcome\tVerdict\tFlags (spans and placeholder ids only)")
            for row in register.tripwire_records(args.job):
                flags = "; ".join(
                    (f"{item['start']}-{item['end']}" if item["start"] is not None else "")
                    + (f" {item['placeholder']}" if item["placeholder"] else "") + f" {item['category']}"
                    for item in row["flags"]
                ) or "-"
                print(f"{row['record_id']}\t{row['job_id']}\t{row['outcome']}\t{row['verdict'] or '-'}\t{flags}")
        elif args.command == "stats":
            print(json.dumps(register.tripwire_stats(), indent=2, sort_keys=True))
        return 0
    except (ValueError, PermissionError, KeyError, OSError, sqlite3.Error) as error:
        return _refused(error)
