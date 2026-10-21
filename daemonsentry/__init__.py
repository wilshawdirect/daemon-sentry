"""daemon-sentry: cadence-relative liveness monitoring for daemon fleets."""
from .backends import Backend, JSONFileBackend, SQLiteBackend
from .core import DEFAULT_HOST, Sentry, format_age, format_table
from .models import (
    VERDICT_CONFLICTED,
    VERDICT_DEAD,
    VERDICT_HEALTHY,
    VERDICT_IDLE,
    VERDICT_LATE,
    VERDICT_PENDING,
    VERDICT_RETIRED,
    DaemonRow,
    FleetVerdict,
    HostVerdict,
)

__version__ = "1.0.0"

__all__ = [
    "Backend",
    "DEFAULT_HOST",
    "DaemonRow",
    "FleetVerdict",
    "HostVerdict",
    "JSONFileBackend",
    "SQLiteBackend",
    "Sentry",
    "VERDICT_CONFLICTED",
    "VERDICT_DEAD",
    "VERDICT_HEALTHY",
    "VERDICT_IDLE",
    "VERDICT_LATE",
    "VERDICT_PENDING",
    "VERDICT_RETIRED",
    "format_age",
    "format_table",
]
