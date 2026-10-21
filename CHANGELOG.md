# Changelog

## Unreleased

- A daemon that has never beaten is `pending` (not `dead`) until `new_daemon_grace` cadences (default 2) have
  passed since it was registered; after that it is `dead` as before. `pending` exits clean.

## 0.1.0 - 2026-07-31

Initial release, extracted and generalized from a private production multi-agent automation system.

- Full offline self-test (`python -m daemonsentry --self-test`)
- MIT licensed

See the Roadmap section of the README for planned improvements - this is a maintained tool, not a snapshot.
