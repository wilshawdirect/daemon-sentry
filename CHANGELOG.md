# Changelog

## Unreleased

- `retire()` / `unretire()` (CLI `retire`, `unretire`): a decommissioned or moved daemon reads `retired` (clean exit), never speaks for
  the fleet while another host's row is active, and a fresh heartbeat ends the retirement. Existing SQLite stores gain the column in place.
- Work-driven daemons: `census(pending_work={name: oldest_pending_age_s | None})` (CLI `--pending NAME=SECONDS|none`) turns a late or
  dead daemon with an empty input queue into `idle`; waiting work it ignores keeps the verdict; `idle` exits clean.
- A daemon that has never beaten is `pending` (not `dead`) until `new_daemon_grace` cadences (default 2) have
  passed since it was registered; after that it is `dead` as before. `pending` exits clean.

## 0.1.0 - 2026-07-31

Initial release, extracted and generalized from a private production multi-agent automation system.

- Full offline self-test (`python -m daemonsentry --self-test`)
- MIT licensed

See the Roadmap section of the README for planned improvements - this is a maintained tool, not a snapshot.
