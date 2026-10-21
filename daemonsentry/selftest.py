"""Offline self-test: a synthetic fleet exhibiting the failure modes.

Run with ``python -m daemonsentry --self-test``. No network, no external
services — the fleet is built in memory and in temp files, at a fixed
reference instant so the output is deterministic.
"""
from __future__ import annotations

import os
import tempfile

from .backends import JSONFileBackend, SQLiteBackend
from .core import Sentry, format_table

_NOW = 1_780_000_000.0  # fixed reference instant for deterministic verdicts
_DAY = 86400.0


def run_selftest() -> int:
    """Build the synthetic fleet, run a census, assert every lesson holds.

    Returns a process exit code: 0 if all checks pass, 1 otherwise.
    """
    total = 0
    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        nonlocal total
        total += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    print("daemon-sentry self-test (offline, synthetic fleet)")
    print("=" * 70)

    s = Sentry(SQLiteBackend(":memory:"))

    # Lesson 1 — liveness is cadence-relative
    s.register("weekly-digest", cadence_s=7 * _DAY, at=_NOW - 30 * _DAY)
    s.beat("weekly-digest", at=_NOW - 5 * _DAY)
    s.register("queue-poller", cadence_s=120, at=_NOW - 30 * _DAY)
    s.beat("queue-poller", at=_NOW - 1200)
    s.register("metrics-rollup", cadence_s=600, at=_NOW - 30 * _DAY)
    s.beat("metrics-rollup", at=_NOW - 1200)

    # Lesson 2 — a stored status field is a claim, not evidence
    s.register("report-writer", cadence_s=300, at=_NOW - 30 * _DAY)
    s.beat("report-writer", at=_NOW - 2 * 3600)
    s.claim("report-writer", "healthy", at=_NOW - 2 * 3600)
    s.register("backup-runner", cadence_s=300, at=_NOW - 30 * _DAY)
    s.beat("backup-runner", at=_NOW - 60)
    s.claim("backup-runner", "down", at=_NOW - 5 * _DAY)

    # Lesson 3 — errors must be paired with timestamps and the last success
    s.register("mail-fetcher", cadence_s=60, at=_NOW - 30 * _DAY)
    s.report_error("mail-fetcher", "connection timeout to imap.example.com", at=_NOW - 3600)
    s.beat("mail-fetcher", at=_NOW - 30)
    s.register("disk-cleaner", cadence_s=60, at=_NOW - 30 * _DAY)
    s.beat("disk-cleaner", at=_NOW - 30)
    s.report_error("disk-cleaner", "permission denied: /var/cache", at=_NOW - 10)

    # Lesson 4 — duplicate identity across hosts
    s.register("sync-agent", cadence_s=300, host="host-a", at=_NOW - 30 * _DAY)
    s.register("sync-agent", cadence_s=300, host="host-b", at=_NOW - 30 * _DAY)
    s.beat("sync-agent", host="host-a", at=_NOW - _DAY)
    s.beat("sync-agent", host="host-b", at=_NOW - 60)

    # Lesson 5 — a daemon registered a moment ago has not missed anything yet
    s.register("fresh-worker", cadence_s=300, at=_NOW - 120)
    s.register("never-started", cadence_s=300, at=_NOW - 2 * 3600)
    s.register("fresh-claims-green", cadence_s=300, at=_NOW - 60)
    s.claim("fresh-claims-green", "healthy", at=_NOW - 30)

    results = {r.name: r for r in s.census(now=_NOW)}
    s.close()

    print()
    print(format_table(sorted(results.values(), key=lambda r: r.name)))
    print()

    check("lesson 1: weekly job beaten 5d ago is HEALTHY (0.7x its own cadence)",
          results["weekly-digest"].verdict == "healthy")
    check("lesson 1: 2-minute job beaten 20m ago is DEAD (10.0x its own cadence)",
          results["queue-poller"].verdict == "dead")
    check("lesson 1: 10-minute job beaten 20m ago is LATE (2.0x its own cadence)",
          results["metrics-rollup"].verdict == "late")
    check("lesson 2: stored 'healthy' vs dead heartbeats -> CONFLICTED (green-but-dead)",
          results["report-writer"].verdict == "conflicted")
    check("lesson 2: stored 'down' vs fresh heartbeats -> CONFLICTED (red-but-fine)",
          results["backup-runner"].verdict == "conflicted")
    mail = results["mail-fetcher"]
    check("lesson 3: stale error superseded by success is NOT flagged",
          mail.verdict == "healthy" and not mail.hosts[0].error_active)
    check("lesson 3: error newer than the last success IS flagged active",
          results["disk-cleaner"].hosts[0].error_active)
    sync = results["sync-agent"]
    check("lesson 4: fleet verdict uses MAX(heartbeat) across hosts -> HEALTHY",
          sync.verdict == "healthy" and sync.freshest_host == "host-b")
    host_a = next(h for h in sync.hosts if h.host == "host-a")
    check("lesson 4: per-host detail still exposes host-a as DEAD",
          host_a.verdict == "dead")

    check("lesson 5: registered 2 min ago with a 5-minute cadence is PENDING, not DEAD",
          results["fresh-worker"].verdict == "pending")
    check("lesson 5: never beaten and 2 hours past registration is DEAD",
          results["never-started"].verdict == "dead")
    check("lesson 5: a green claim on a pending daemon is not a conflict",
          results["fresh-claims-green"].verdict == "pending")

    # Backend round-trips (atomic writes + reopen from disk)
    with tempfile.TemporaryDirectory() as tmp:
        jpath = os.path.join(tmp, "fleet.json")
        js = Sentry(JSONFileBackend(jpath))
        js.register("json-daemon", cadence_s=60, at=_NOW - 600)
        js.beat("json-daemon", at=_NOW - 30)
        js.close()
        reread = Sentry(JSONFileBackend(jpath)).census(now=_NOW)
        check("json backend: atomic write + reopen round-trip",
              len(reread) == 1 and reread[0].verdict == "healthy")

        dpath = os.path.join(tmp, "fleet.db")
        ds = Sentry(SQLiteBackend(dpath))
        ds.register("sqlite-daemon", cadence_s=60, at=_NOW - 600)
        ds.beat("sqlite-daemon", at=_NOW - 30)
        ds.close()
        ds2 = Sentry(SQLiteBackend(dpath))
        reread2 = ds2.census(now=_NOW)
        ds2.close()
        check("sqlite backend: on-disk round-trip",
              len(reread2) == 1 and reread2[0].verdict == "healthy")

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} of {total} checks failed")
        return 1
    print(f"SELF-TEST PASSED: {total}/{total} checks green")
    return 0
