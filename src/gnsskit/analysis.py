"""Receiver performance analysis of a parsed NMEA log.

Definitions used throughout (they appear in the report too):

* **Fix epoch**: GGA fix quality 1-5 (GPS, DGPS, PPS, RTK fixed, RTK float). Quality 0
  (none), 6 (estimated / dead reckoning), 7 (manual) and 8 (simulator) are not
  fixes. An epoch without GGA uses RMC: status A and a mode that is not N/E/M/S.
* **Epoch interval**: median step between consecutive timed epochs.
* **Expected epochs**: epochs present plus those missing from data gaps (a step
  longer than 1.5 x the interval). Missing epochs count as "no fix".
* **Fix availability**: fix epochs / expected epochs x 100.
* **TTFF**: time from the first timed epoch in the log to the first fix epoch. The
  log start stands in for receiver power-on, so this is only meaningful for logs
  recorded from a cold or warm start.
* **Fix lost / regained**: transitions between fix and no-fix epochs. A log that
  starts without a fix is a cold start, not a loss.
* **Satellites used**: GGA's count (some receivers cap it at 12) and, separately,
  the unique PRNs listed in GSA, split by constellation.
* **HDOP statistics**: GGA HDOP over fix epochs.
* **CEP50 / CEP95**: 50th / 95th percentile of horizontal distance from the
  reference point (the mean fix position, or a given point). Positions are
  converted to local east/north metres with WGS84 radii of curvature at the
  reference latitude; height is ignored (error < 0.02 % at 1000 m altitude).
* **Percentiles**: linear interpolation between closest ranks (numpy's default,
  `statistics.quantiles(method="inclusive")`).
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from gnsskit.log import Epoch, LogReadResult
from gnsskit.nmea import constellation_name

__all__ = [
    "Accuracy",
    "Analysis",
    "Event",
    "EventKind",
    "Stats",
    "analyse",
    "enu_offset_m",
    "is_fix",
    "percentile",
]

FIX_QUALITIES = frozenset({1, 2, 3, 4, 5})
_RMC_NO_FIX_MODES = frozenset({"N", "E", "M", "S"})
_GAP_FACTOR = 1.5

# NMEA 4.10+ GSA system ID -> constellation
_GSA_SYSTEMS = {1: "GPS", 2: "GLONASS", 3: "Galileo", 4: "BeiDou", 5: "QZSS", 6: "NavIC"}

# WGS84
_A = 6_378_137.0
_E2 = 6.694_379_990_14e-3


# ------------------------------------------------------------------------- helpers


def percentile(values: Sequence[float], p: float) -> float:
    """p-th percentile (0-100) with linear interpolation between closest ranks."""
    if not values:
        raise ValueError("percentile of empty data")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(rank), math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def enu_offset_m(lat: float, lon: float, *, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """East and north offset in metres of (lat, lon) from the reference point."""
    phi = math.radians(ref_lat)
    w = math.sqrt(1 - _E2 * math.sin(phi) ** 2)
    meridional = _A * (1 - _E2) / w**3  # M: north-south radius of curvature
    prime_vertical = _A / w  # N: east-west radius of curvature
    north = math.radians(lat - ref_lat) * meridional
    east = math.radians(lon - ref_lon) * prime_vertical * math.cos(phi)
    return east, north


def _latlon_from_enu(east: float, north: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    phi = math.radians(ref_lat)
    w = math.sqrt(1 - _E2 * math.sin(phi) ** 2)
    lat = ref_lat + math.degrees(north / (_A * (1 - _E2) / w**3))
    lon = ref_lon + math.degrees(east / (_A / w * math.cos(phi)))
    return lat, lon


def is_fix(epoch: Epoch) -> bool:
    if epoch.gga is not None:
        return epoch.gga.quality in FIX_QUALITIES
    if epoch.rmc is not None:
        return epoch.rmc.valid and (epoch.rmc.mode or "A") not in _RMC_NO_FIX_MODES
    return False


def _position(epoch: Epoch) -> tuple[float, float] | None:
    for sentence in (epoch.gga, epoch.rmc):
        if sentence is not None and sentence.lat is not None and sentence.lon is not None:
            return sentence.lat, sentence.lon
    return None


def _gsa_used(epoch: Epoch) -> dict[str, set[int]] | None:
    if not epoch.gsa:
        return None
    used: dict[str, set[int]] = {}
    for gsa in epoch.gsa:
        if gsa.system_id is not None:
            name = _GSA_SYSTEMS.get(gsa.system_id, f"System {gsa.system_id}")
        else:
            name = constellation_name(gsa.talker)
        used.setdefault(name, set()).update(gsa.prns)
    return used


# ------------------------------------------------------------------------- results


@dataclass(frozen=True)
class Stats:
    n: int
    min: float
    mean: float
    median: float
    p95: float
    max: float

    @classmethod
    def of(cls, values: Sequence[float]) -> Stats | None:
        if not values:
            return None
        return cls(
            n=len(values),
            min=min(values),
            mean=statistics.fmean(values),
            median=percentile(values, 50),
            p95=percentile(values, 95),
            max=max(values),
        )


class EventKind(StrEnum):
    FIX_LOST = "fix_lost"
    FIX_REGAINED = "fix_regained"
    DATA_GAP = "data_gap"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    time: dt.time | None
    utc: dt.datetime | None
    seconds: float | None
    line_no: int
    # FIX_LOST / FIX_REGAINED: outage length (None while still ongoing at end of log).
    # DATA_GAP: time with no data, beyond the normal epoch interval.
    duration_s: float | None = None


@dataclass(frozen=True)
class Accuracy:
    n: int
    reference_kind: str  # "mean" or "given"
    reference_lat: float
    reference_lon: float
    cep50_m: float
    cep95_m: float
    max_m: float


@dataclass(frozen=True)
class Analysis:
    epochs_present: int
    epochs_untimed: int
    epochs_expected: int
    epoch_interval_s: float | None
    duration_s: float | None
    start_utc: dt.datetime | None
    end_utc: dt.datetime | None
    fix_epochs: int
    availability_pct: float | None
    ttff_s: float | None
    ttff_note: str | None
    events: tuple[Event, ...]
    fix_lost_count: int
    longest_outage_s: float | None
    sats_used_gga: Stats | None
    sats_used_gsa: Stats | None
    sats_used_by_constellation: dict[str, Stats]
    # (seconds, GGA satellites, GSA satellites) per timed epoch; None = not reported
    sats_used_series: tuple[tuple[float, int | None, int | None], ...]
    hdop: Stats | None
    accuracy: Accuracy | None


# ------------------------------------------------------------------------ analysis


def _event(kind: EventKind, epoch: Epoch, duration: float | None = None) -> Event:
    return Event(kind, epoch.time, epoch.utc, epoch.seconds, epoch.first_line, duration)


def _interval(timed: list[Epoch]) -> float | None:
    steps = [b.seconds - a.seconds for a, b in zip(timed, timed[1:])]  # type: ignore[operator]
    positive = [s for s in steps if s > 0]
    return statistics.median(positive) if positive else None


def _events(timed: list[Epoch], interval: float | None) -> tuple[list[Event], int]:
    """Return (events, missing epoch count)."""
    events: list[Event] = []
    missing = 0
    state: bool | None = None
    lost_index: int | None = None
    prev: Epoch | None = None
    for epoch in timed:
        if prev is not None and interval is not None:
            step = epoch.seconds - prev.seconds  # type: ignore[operator]
            if step > _GAP_FACTOR * interval:
                missing += round(step / interval) - 1
                events.append(_event(EventKind.DATA_GAP, prev, step - interval))
        fix = is_fix(epoch)
        if state is True and not fix:
            lost_index = len(events)
            events.append(_event(EventKind.FIX_LOST, epoch))
        elif state is False and fix and lost_index is not None:
            lost = events[lost_index]
            duration = epoch.seconds - lost.seconds  # type: ignore[operator]
            events[lost_index] = replace(lost, duration_s=duration)
            events.append(_event(EventKind.FIX_REGAINED, epoch, duration))
            lost_index = None
        state = fix
        prev = epoch
    return events, missing


def _ttff(epochs: list[Epoch], timed: list[Epoch]) -> tuple[float | None, str | None]:
    first_fix = next((i for i, e in enumerate(epochs) if is_fix(e) and e.seconds is not None), None)
    if first_fix is None:
        return None, "no fix in log"
    # A fix always has a time (the parser rejects a fix without one), so `timed` is non-empty.
    untimed_before = sum(1 for e in epochs[:first_fix] if e.seconds is None)
    start = timed[0]
    if start is epochs[first_fix] and untimed_before == 0:
        return None, "log starts with a fix; TTFF not measurable"
    ttff = epochs[first_fix].seconds - start.seconds  # type: ignore[operator]
    note = None
    if untimed_before:
        note = (
            f"measured from the first timed epoch; {untimed_before} epochs without time "
            "came before it and are not included"
        )
    return ttff, note


def _accuracy(fixes: list[Epoch], reference: tuple[float, float] | None) -> Accuracy | None:
    positions = [p for p in (_position(e) for e in fixes) if p is not None]
    if not positions:
        return None
    if reference is not None:
        ref_lat, ref_lon = reference
        kind = "given"
    else:
        lat0, lon0 = positions[0]
        offsets = [enu_offset_m(lat, lon, ref_lat=lat0, ref_lon=lon0) for lat, lon in positions]
        mean_e = statistics.fmean(e for e, _ in offsets)
        mean_n = statistics.fmean(n for _, n in offsets)
        ref_lat, ref_lon = _latlon_from_enu(mean_e, mean_n, lat0, lon0)
        kind = "mean"
    distances = [
        math.hypot(*enu_offset_m(lat, lon, ref_lat=ref_lat, ref_lon=ref_lon))
        for lat, lon in positions
    ]
    return Accuracy(
        n=len(distances),
        reference_kind=kind,
        reference_lat=ref_lat,
        reference_lon=ref_lon,
        cep50_m=percentile(distances, 50),
        cep95_m=percentile(distances, 95),
        max_m=max(distances),
    )


def analyse(log: LogReadResult, reference: tuple[float, float] | None = None) -> Analysis:
    """Analyse a read log. `reference` is an optional (lat, lon) for CEP in degrees."""
    epochs = log.epochs
    timed = [e for e in epochs if e.seconds is not None]
    untimed = len(epochs) - len(timed)
    interval = _interval(timed)
    events, missing = _events(timed, interval)
    fixes = [e for e in epochs if is_fix(e)]
    expected = len(epochs) + missing
    ttff, ttff_note = _ttff(epochs, timed)

    outages = [e.duration_s for e in events if e.kind is EventKind.FIX_REGAINED]

    gsa_by_epoch = [(e, _gsa_used(e)) for e in fixes]
    gsa_known = [used for _, used in gsa_by_epoch if used is not None]
    constellations = sorted({name for used in gsa_known for name in used})
    by_constellation = {
        name: Stats.of([len(used.get(name, ())) for used in gsa_known])
        for name in constellations
    }

    series = []
    for e in timed:
        used = _gsa_used(e)
        series.append(
            (
                e.seconds,
                e.gga.num_sats if e.gga is not None else None,
                sum(len(p) for p in used.values()) if used is not None else None,
            )
        )

    return Analysis(
        epochs_present=len(epochs),
        epochs_untimed=untimed,
        epochs_expected=expected,
        epoch_interval_s=interval,
        duration_s=(timed[-1].seconds - timed[0].seconds) if timed else None,  # type: ignore[operator]
        start_utc=timed[0].utc if timed else None,
        end_utc=timed[-1].utc if timed else None,
        fix_epochs=len(fixes),
        availability_pct=(100 * len(fixes) / expected) if expected else None,
        ttff_s=ttff,
        ttff_note=ttff_note,
        events=tuple(events),
        fix_lost_count=sum(1 for e in events if e.kind is EventKind.FIX_LOST),
        longest_outage_s=max(outages) if outages else None,  # type: ignore[type-var]
        sats_used_gga=Stats.of(
            [e.gga.num_sats for e in fixes if e.gga is not None and e.gga.num_sats is not None]
        ),
        sats_used_gsa=Stats.of([sum(len(p) for p in used.values()) for used in gsa_known]),
        sats_used_by_constellation={k: v for k, v in by_constellation.items() if v is not None},
        sats_used_series=tuple(series),  # type: ignore[arg-type]
        hdop=Stats.of(
            [e.gga.hdop for e in fixes if e.gga is not None and e.gga.hdop is not None]
        ),
        accuracy=_accuracy(fixes, reference),
    )
