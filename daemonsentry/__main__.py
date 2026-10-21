"""Command-line interface: ``python -m daemonsentry <command>``."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .backends import Backend, JSONFileBackend, SQLiteBackend
from .core import DEFAULT_HOST, Sentry, format_table
from .models import VERDICT_CONFLICTED, VERDICT_DEAD, VERDICT_LATE, FleetVerdict


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the daemonsentry CLI."""
    parser = argparse.ArgumentParser(
        prog="daemonsentry",
        description="Cadence-relative liveness monitoring for daemon fleets.",
    )
    parser.add_argument("--self-test", action="store_true",
                        help="run the offline self-test and exit")
    parser.add_argument("--db", default=None, metavar="PATH",
                        help="heartbeat store path (default: daemonsentry.db / .json)")
    parser.add_argument("--backend", choices=("sqlite", "json"), default="sqlite",
                        help="storage backend (default: sqlite)")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("register", help="register a daemon and its expected cadence")
    p.add_argument("name")
    p.add_argument("--cadence", type=float, required=True,
                   help="expected heartbeat interval in seconds")
    p.add_argument("--host", default=DEFAULT_HOST)

    p = sub.add_parser("beat", help="record a heartbeat (also clears sticky errors)")
    p.add_argument("name")
    p.add_argument("--host", default=DEFAULT_HOST)

    p = sub.add_parser("error", help="record a failure with its timestamp")
    p.add_argument("name")
    p.add_argument("--message", required=True)
    p.add_argument("--host", default=DEFAULT_HOST)

    p = sub.add_parser("claim", help="record a daemon's self-reported status (audited, not trusted)")
    p.add_argument("name")
    p.add_argument("--status", required=True)
    p.add_argument("--host", default=DEFAULT_HOST)

    p = sub.add_parser("census", help="compute fleet health from heartbeat evidence")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print full evidence and per-host detail")
    p.add_argument("--pending", action="append", default=[], metavar="NAME=SECONDS|none",
                   help="mark NAME as a work-driven daemon with its oldest pending input "
                        "age in seconds (or 'none' when its input is empty); repeatable")

    p = sub.add_parser("retire", help="mark a decommissioned daemon as retired (kept as history, not judged)")
    p.add_argument("name")
    p.add_argument("--host", default=None, help="retire only this host's row (default: every host)")

    p = sub.add_parser("unretire", help="undo a retirement")
    p.add_argument("name")
    p.add_argument("--host", default=None)

    sub.add_parser("selftest", help="run the offline self-test")
    return parser


def _open_backend(args: argparse.Namespace) -> Backend:
    if args.backend == "json":
        return JSONFileBackend(args.db or "daemonsentry.json")
    return SQLiteBackend(args.db or "daemonsentry.db")


def _census_exit_code(results: list[FleetVerdict]) -> int:
    verdicts = {r.verdict for r in results}
    if verdicts & {VERDICT_DEAD, VERDICT_CONFLICTED}:
        return 2
    if VERDICT_LATE in verdicts:
        return 1
    return 0   # healthy, pending (a new daemon inside its grace), idle (a work-driven daemon with no work) and retired exit clean


def _parse_pending(items: list[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for item in items:
        name, sep, age = item.partition("=")
        if not name or not sep:
            raise ValueError(f"--pending expects NAME=SECONDS or NAME=none, got {item!r}")
        out[name] = None if age.strip().lower() == "none" else float(age)
    return out


def _run_census(sentry: Sentry, args: argparse.Namespace) -> int:
    try:
        pending_work = _parse_pending(args.pending) or None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    results = sentry.census(pending_work=pending_work)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(format_table(results))
        if args.verbose:
            for r in results:
                print(f"\n{r.name}:")
                for line in r.evidence:
                    print(f"  - {line}")
                for h in r.hosts:
                    print(f"  host {h.host}: {h.verdict} ({'; '.join(h.evidence)})")
    return _census_exit_code(results)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_test or args.command == "selftest":
        from .selftest import run_selftest
        return run_selftest()
    if args.command is None:
        parser.print_help()
        return 2

    sentry = Sentry(_open_backend(args))
    try:
        if args.command == "register":
            sentry.register(args.name, cadence_s=args.cadence, host=args.host)
            print(f"registered {args.name} on {args.host} (cadence {args.cadence:.0f}s)")
        elif args.command == "beat":
            sentry.beat(args.name, host=args.host)
            print(f"heartbeat recorded for {args.name} on {args.host}")
        elif args.command == "error":
            sentry.report_error(args.name, args.message, host=args.host)
            print(f"error recorded for {args.name} on {args.host}")
        elif args.command == "claim":
            sentry.claim(args.name, args.status, host=args.host)
            print(f"claimed status '{args.status}' recorded for {args.name} on {args.host}")
        elif args.command == "retire":
            n = sentry.retire(args.name, host=args.host)
            print(f"retired {n} row(s) for {args.name}")
        elif args.command == "unretire":
            n = sentry.unretire(args.name, host=args.host)
            print(f"unretired {n} row(s) for {args.name}")
        else:
            return _run_census(sentry, args)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    finally:
        sentry.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
