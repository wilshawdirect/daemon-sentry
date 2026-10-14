"""Core sentry logic: registration, heartbeats, and the fleet census."""
from __future__ import annotations

import time

from .backends import Backend
from .models import (
    VERDICT_CONFLICTED,
    VERDICT_DEAD,
    VERDICT_HEALTHY,
    VERDICT_LATE,
    DaemonRow,
    FleetVerdict,
    HostVerdict,
)

DEFAULT_HOST = "default"

_HEALTHY_CLAIMS = frozenset({"healthy", "ok", "green", "up", "running", "alive"})
_DEAD_CLAIMS = frozenset({"dead", "down", "red", "error", "failed", "stopped"})


def format_age(seconds: float | None) -> str:
    """Render a duration in seconds as a compact human string ('never' for None)."""
    if seconds is None:
        return "never"
    for unit, size in (("d", 86400.0), ("h", 3600.0), ("m", 60.0)):
        if seconds >= size:
            return f"{seconds / size:.1f}{unit}"
    return f"{seconds:.0f}s"


def format_table(results: list[FleetVerdict]) -> str:
    """Render census results as a plain-text table (one line per daemon name)."""
    if not results:
        return "no daemons registered"
    header = f"{'NAME':<18} {'VERDICT':<11} {'AGE':>8} {'CADENCE':>8} {'HOSTS':>5}  NOTES"
    lines = [header, "-" * len(header)]
    for r in results:
        note = r.evidence[0] if r.evidence else ""
        lines.append(
            f"{r.name:<18} {r.verdict.upper():<11} {format_age(r.age_s):>8} "
            f"{format_age(r.cadence_s):>8} {len(r.hosts):>5}  {note}"
        )
    return "\n".join(lines)


class Sentry:
    """Cadence-relative liveness monitor over a pluggable heartbeat store.

    Health is recomputed from heartbeat timestamps on every census; stored
    status fields are treated as claims to audit, never as truth. Thresholds
    are multiples of each daemon's *own* cadence: heartbeat age up to
    ``late_factor`` x cadence is healthy, up to ``dead_factor`` x cadence is
    late, beyond that is dead.
    """

    def __init__(self, backend: Backend, late_factor: float = 1.5, dead_factor: float = 3.0) -> None:
        self.backend = backend
        self.late_factor = late_factor
        self.dead_factor = dead_factor

    def register(self, name: str, cadence_s: float, host: str = DEFAULT_HOST,
                 at: float | None = None) -> None:
        """Register (or re-register) a daemon with its expected heartbeat cadence in seconds."""
        if cadence_s <= 0:
            raise ValueError("cadence_s must be positive")
        at = time.time() if at is None else at
        row = self.backend.get(name, host)
        if row is None:
            row = DaemonRow(name=name, host=host, cadence_s=cadence_s, registered_at=at)
        else:
            row.cadence_s = cadence_s
        self.backend.put(row)

    def beat(self, name: str, host: str = DEFAULT_HOST, at: float | None = None) -> None:
        """Record a successful heartbeat for (name, host)."""
        at = time.time() if at is None else at
        row = self._require(name, host)
        row.last_beat_at = at
        row.last_ok_at = at
        self.backend.put(row)

    def report_error(self, name: str, message: str, host: str = DEFAULT_HOST,
                     at: float | None = None) -> None:
        """Record a failure WITH its timestamp, so the census can judge it stale later."""
        at = time.time() if at is None else at
        row = self._require(name, host)
        row.last_error = message
        row.last_error_at = at
        self.backend.put(row)

    def claim(self, name: str, status: str, host: str = DEFAULT_HOST,
              at: float | None = None) -> None:
        """Record the status a daemon claims for itself. Audited by census(), never trusted."""
        at = time.time() if at is None else at
        row = self._require(name, host)
        row.claimed_status = status
        row.claimed_at = at
        self.backend.put(row)

    def census(self, now: float | None = None) -> list[FleetVerdict]:
        """Compute a fleet-wide health census from stored heartbeat evidence.

        Rows sharing a name are grouped across hosts; the fleet verdict is
        keyed to the freshest heartbeat (MAX across hosts), and per-host
        detail is preserved so a stale replica is still visible.
        """
        now = time.time() if now is None else now
        by_name: dict[str, list[DaemonRow]] = {}
        for row in self.backend.all_rows():
            by_name.setdefault(row.name, []).append(row)

        results: list[FleetVerdict] = []
        for name in sorted(by_name):
            rows = by_name[name]
            verdicts = {row.host: self._classify_host(row, now) for row in rows}
            freshest = max(
                rows,
                key=lambda r: r.last_beat_at if r.last_beat_at is not None else float("-inf"),
            )
            top = verdicts[freshest.host]
            evidence = list(top.evidence)
            if len(rows) > 1:
                evidence.append(
                    f"{len(rows)} hosts share this name; fleet verdict keyed to freshest "
                    f"({freshest.host}) - a name-only single-row lookup is a coin flip"
                )
                for hv in verdicts.values():
                    if hv.host != freshest.host and hv.verdict != top.verdict:
                        evidence.append(f"host {hv.host} disagrees: {hv.verdict} ({hv.evidence[0]})")
            results.append(FleetVerdict(
                name=name,
                verdict=top.verdict,
                cadence_s=freshest.cadence_s,
                freshest_host=freshest.host if freshest.last_beat_at is not None else None,
                age_s=top.age_s,
                ratio=top.ratio,
                evidence=evidence,
                hosts=[verdicts[row.host] for row in sorted(rows, key=lambda r: r.host)],
            ))
        return results

    def close(self) -> None:
        """Release the underlying backend's resources."""
        self.backend.close()

    def _require(self, name: str, host: str) -> DaemonRow:
        row = self.backend.get(name, host)
        if row is None:
            raise KeyError(
                f"daemon {name!r} on host {host!r} is not registered - call register() first"
            )
        return row

    def _classify_host(self, row: DaemonRow, now: float) -> HostVerdict:
        evidence: list[str] = []
        age: float | None
        ratio: float | None
        if row.last_beat_at is None:
            computed, age, ratio = VERDICT_DEAD, None, None
            evidence.append(
                f"no heartbeat ever recorded (registered {format_age(now - row.registered_at)} ago)"
            )
        else:
            age = now - row.last_beat_at
            ratio = age / row.cadence_s
            if ratio <= self.late_factor:
                computed = VERDICT_HEALTHY
            elif ratio <= self.dead_factor:
                computed = VERDICT_LATE
            else:
                computed = VERDICT_DEAD
            evidence.append(
                f"last heartbeat {format_age(age)} ago = {ratio:.1f}x its "
                f"{format_age(row.cadence_s)} cadence"
            )

        error_active = row.last_error_at is not None and (
            row.last_ok_at is None or row.last_error_at > row.last_ok_at
        )
        if row.last_error_at is not None:
            if error_active:
                evidence.append(
                    f"active error ({format_age(now - row.last_error_at)} ago, no success "
                    f"since): {row.last_error}"
                )
            else:
                evidence.append(
                    f"stale error ignored - success {format_age(now - row.last_ok_at)} ago "
                    f"supersedes error from {format_age(now - row.last_error_at)} ago"
                )

        verdict = computed
        claimed = (row.claimed_status or "").strip().lower()
        if claimed in _HEALTHY_CLAIMS and computed == VERDICT_DEAD:
            verdict = VERDICT_CONFLICTED
            evidence.insert(0, (
                f"stored status says {row.claimed_status!r} but heartbeats say "
                f"{computed} (green-but-dead)"
            ))
        elif claimed in _DEAD_CLAIMS and computed == VERDICT_HEALTHY:
            verdict = VERDICT_CONFLICTED
            evidence.insert(0, (
                f"stored status says {row.claimed_status!r} but heartbeats say "
                f"{computed} (red-but-fine)"
            ))

        return HostVerdict(
            host=row.host,
            verdict=verdict,
            age_s=age,
            ratio=ratio,
            claimed_status=row.claimed_status,
            error_active=error_active,
            evidence=evidence,
        )
