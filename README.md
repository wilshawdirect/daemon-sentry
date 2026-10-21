# daemon-sentry

[![CI](https://github.com/Wilshawdirect/daemon-sentry/actions/workflows/ci.yml/badge.svg)](https://github.com/Wilshawdirect/daemon-sentry/actions/workflows/ci.yml)

Cadence-relative liveness monitoring for daemon fleets. It recomputes every
daemon's health from raw heartbeat timestamps — never from what the daemon
last said about itself — and judges each daemon against its *own* expected
cadence, not a global staleness bar.

## Why this exists

This tool is a generalized extract from a production multi-agent automation
system that ran a few dozen scheduled daemons across multiple hosts. The
dashboards there were green while daemons were dead, and red while daemons
were fine — repeatedly. Each of those incidents became a rule, and the rules
are the product:

1. **Liveness is cadence-relative.** A daemon is judged by
   `heartbeat age / its own expected cadence`, never by a global staleness
   threshold. A weekly job whose last heartbeat is 5 days old is healthy; a
   2-minute job at 20 minutes is dead. Any monitor with one fleet-wide
   "stale after N minutes" bar gets both of those wrong at once.
2. **Green-but-dead.** A stored `status = "healthy"` field is a claim, not
   evidence — it says the daemon was healthy *when it last managed to write*.
   The census recomputes truth from heartbeat timestamps and flags any
   disagreement between the stored claim and the computed verdict as
   `conflicted` (this also catches the inverse, red-but-fine).
3. **Sticky errors.** A `last_error` field that is written on failure but
   never cleared on success produces permanent false-reds. Every error here
   is paired with its timestamp and compared against the most recent success:
   an error that was superseded by a later successful heartbeat is reported
   as stale and does not affect the verdict.
4. **Duplicate identity.** Fleets keyed by name-only across multiple hosts
   produce coin-flip results — a name-only lookup returns whichever host's
   row the storage happens to serve first. The census groups by name, keys
   the fleet verdict to `MAX(heartbeat)` across hosts, and preserves per-host
   detail so a dead replica is still visible.

## Quickstart

Requires Python 3.11+. No dependencies.

```bash
git clone https://github.com/Wilshawdirect/daemon-sentry
cd daemon-sentry

# prove it works, offline
python -m daemonsentry --self-test

# monitor a real fleet
python -m daemonsentry --db fleet.db register queue-poller --cadence 120 --host worker-1
python -m daemonsentry --db fleet.db beat queue-poller --host worker-1
python -m daemonsentry --db fleet.db census
```

`census` exits `0` when everything is healthy, `1` if anything is late, and
`2` if anything is dead or conflicted — so it drops straight into cron, CI,
or an alerting wrapper.

## Library usage

```python
from daemonsentry import Sentry, SQLiteBackend

sentry = Sentry(SQLiteBackend("fleet.db"))
sentry.register("invoice-mailer", cadence_s=3600, host="worker-1")

# from the daemon (or a wrapper around it):
sentry.beat("invoice-mailer", host="worker-1")
sentry.report_error("invoice-mailer", "SMTP 421 from mail.example.com", host="worker-1")

# from the monitor:
for fleet in sentry.census():
    print(fleet.name, fleet.verdict, fleet.evidence)
```

Each census entry carries the verdict (`healthy` / `late` / `dead` /
`conflicted`), the heartbeat age, the age-to-cadence ratio, human-readable
evidence lines, and per-host detail.

## Verdicts

| Verdict      | Meaning                                                              |
| ------------ | -------------------------------------------------------------------- |
| `healthy`    | heartbeat age ≤ 1.5x the daemon's own cadence                        |
| `late`       | heartbeat age ≤ 3x cadence — worth watching, not yet an incident     |
| `dead`       | heartbeat age > 3x cadence, or no heartbeat after the grace          |
| `conflicted` | the stored status claim disagrees with the computed heartbeat truth  |
| `pending`    | never beaten, registered under 2 cadences ago (new daemon)           |
| `idle`       | a work-driven daemon, late or silent, whose input is empty           |
| `retired`    | decommissioned on purpose; kept as history, never judged             |

Both factors are constructor arguments (`Sentry(backend, late_factor=1.5,
dead_factor=3.0, new_daemon_grace=2.0)`) if your fleet needs a different tolerance.
A daemon that has never beaten is `pending` for `new_daemon_grace` cadences after
registration (it has not missed anything yet) and `dead` after that, so one that
never starts cannot stay invisible.

## Work-driven daemons

Some daemons beat once per item they process, so an empty input queue makes them look dead while they are only idle.
Tell the census which ones those are and how old their oldest pending item is:

```python
fleet = sentry.census(pending_work={"invoice-parser": None,        # nothing queued: idle, not dead
                                    "image-resizer": 1800.0})      # oldest item waiting 30 min
```

or `python -m daemonsentry census --pending invoice-parser=none --pending image-resizer=1800`. A late or dead daemon with no
pending input (or an oldest item younger than its own cadence) becomes `idle`; one that is ignoring waiting work stays
`late`/`dead`, and one left out of the mapping is judged on heartbeats alone (an unreadable queue proves nothing).

## Retiring a daemon

A daemon you switched off, or moved to another host, would otherwise fail every census for ever (or leave a dead ghost row
next to the live one). `sentry.retire("invoice-mailer")` (CLI: `retire NAME [--host H]`) marks the row `retired`: it stays in the
store as history, reads `retired`, exits clean, and never speaks for the fleet while another host's row is active. A fresh
heartbeat or `unretire()` ends the retirement. Stores written before this existed are migrated in place.

## Architecture

```
daemonsentry/
  models.py    dataclasses: DaemonRow (stored), HostVerdict / FleetVerdict (computed)
  backends.py  storage: Backend ABC + SQLiteBackend + JSONFileBackend (atomic writes)
  core.py      Sentry: register / beat / report_error / claim / retire / census
  selftest.py  offline synthetic fleet exercising all four failure modes
  __main__.py  CLI
```

Backends are deliberately dumb `(name, host) -> row` stores; every verdict is
computed in `core.py` at census time. That split is the point: adding a new
backend never re-implements health rules, and no backend can "cache" a
verdict that later goes stale.

## Limitations

- **Not an alerter.** It computes verdicts and exit codes; paging, retries,
  and escalation are yours to wire on top.
- **Single-writer JSON backend.** The JSON file is written atomically
  (temp file + `os.replace`) but has no cross-process locking; use the
  SQLite backend if multiple processes record heartbeats.
- **One clock.** Timestamps are recorded with the recording process's clock.
  If daemons on different machines write to a shared store, clock skew
  between them is not corrected.
- **Point-in-time.** `census()` is a snapshot, not a history — it keeps only
  the latest heartbeat/error/claim per (name, host), so there is no trend or
  flap detection.

## License

MIT — see [LICENSE](LICENSE). Copyright 2026 ADZ.

## Roadmap

This is a maintained tool, not a snapshot - improvements land as the system it came from evolves:

- Alerting hooks (webhook + Telegram) on verdict transitions
- Minimal read-only web dashboard for the census
- Prometheus metrics export
- Heartbeat retention pruning for long-running fleets

See [CHANGELOG.md](CHANGELOG.md) for release history.
