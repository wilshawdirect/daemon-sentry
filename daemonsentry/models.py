"""Data structures for daemon-sentry."""
from __future__ import annotations

from dataclasses import dataclass, field

VERDICT_HEALTHY = "healthy"
VERDICT_LATE = "late"
VERDICT_DEAD = "dead"
VERDICT_CONFLICTED = "conflicted"
VERDICT_PENDING = "pending"
VERDICT_IDLE = "idle"
VERDICT_RETIRED = "retired"


@dataclass
class DaemonRow:
    """One daemon registration on one host — the raw stored record.

    All timestamps are Unix epoch seconds. ``claimed_status`` is whatever the
    daemon last said about itself; the census audits it, it never trusts it.
    """

    name: str
    host: str
    cadence_s: float
    registered_at: float
    last_beat_at: float | None = None
    last_ok_at: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    claimed_status: str | None = None
    claimed_at: float | None = None
    retired_at: float | None = None


@dataclass
class HostVerdict:
    """Computed health for one (name, host) row, with the evidence behind it."""

    host: str
    verdict: str
    age_s: float | None
    ratio: float | None
    claimed_status: str | None
    error_active: bool
    evidence: list[str] = field(default_factory=list)


@dataclass
class FleetVerdict:
    """Fleet-level health for one daemon name aggregated across all hosts."""

    name: str
    verdict: str
    cadence_s: float
    freshest_host: str | None
    age_s: float | None
    ratio: float | None
    evidence: list[str] = field(default_factory=list)
    hosts: list[HostVerdict] = field(default_factory=list)
