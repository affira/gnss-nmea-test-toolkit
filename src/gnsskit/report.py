"""Markdown test report for an analysed NMEA log.

`build_report` is a pure function of its inputs (the generation time is passed in),
so the same log, profile and time always give the same report.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from gnsskit import __version__
from gnsskit.analysis import Analysis, Event, EventKind, Stats
from gnsskit.log import LogReadResult
from gnsskit.profile import NOT_INTEGRITY_ERRORS, CriterionResult, Evaluation, Outcome, Verdict

__all__ = ["build_report"]

_MAX_EXAMPLES = 10
_SMALL_SAMPLE = 100

_THRESHOLD_FORMAT = {
    "min_availability_pct": "≥ {:.1f} %",
    "max_ttff_s": "≤ {:.1f} s",
    "max_hdop_p95": "≤ {:.2f}",
    "max_cep95_m": "≤ {:.2f} m",
    "max_integrity_errors": "≤ {}",
}
_MEASURED_FORMAT = {
    "min_availability_pct": "{:.1f} %",
    "max_ttff_s": "{:.1f} s",
    "max_hdop_p95": "{:.2f}",
    "max_cep95_m": "{:.3f} m",
    "max_integrity_errors": "{}",
}
_EVENT_LABEL = {
    EventKind.FIX_LOST: "Fix lost",
    EventKind.FIX_REGAINED: "Fix regained",
    EventKind.DATA_GAP: "Data gap",
}

DEFINITIONS = [
    ("Epoch", "one measurement instant; at 1 Hz, the set of sentences for one second."),
    ("Fix", "a valid position: GGA fix quality 1-5 (GPS, DGPS, PPS, RTK fixed, RTK float). "
            "Quality 6 (estimated / dead reckoning) is not a fix."),
    ("Fix availability", "fix epochs divided by expected epochs. Expected epochs include those "
                         "missing from data gaps, which count as no fix."),
    ("TTFF", "time to first fix: from the first epoch in the log to the first fix epoch. The log "
             "start stands in for receiver power-on."),
    ("Fix lost / regained", "a change from fix to no fix, and back. A log that starts without a "
                            "fix is a cold start, not a loss."),
    ("Data gap", "a step between epochs longer than 1.5 times the normal interval."),
    ("HDOP", "horizontal dilution of precision: how much satellite geometry magnifies "
             "horizontal error. Lower is better."),
    ("Satellites used", "GGA's count, and the unique satellites listed in GSA, by constellation."),
    ("CEP50 / CEP95", "circular error probable: the radius containing 50 % / 95 % of horizontal "
                      "positions around the reference point."),
    ("Integrity errors", "lines that fail checksum, framing or field checks, plus log-level "
                         "problems (time going backwards, duplicates, incomplete GSV). "
                         "Valid sentences of an unrecognised type are reported but not counted."),
    ("Percentiles", "linear interpolation between closest ranks."),
]  # fmt: skip


# ------------------------------------------------------------------------- helpers


def _esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _num(value: float | int | None, fmt: str = "{:.1f}") -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return fmt.format(value)


def _when(utc: dt.datetime | None, time: dt.time | None) -> str:
    if utc is not None:
        text = utc.strftime("%Y-%m-%d %H:%M:%S")
        return text + (f".{utc.microsecond // 10_000:02d}" if utc.microsecond else "")
    if time is not None:
        return time.isoformat() + " (date unknown)"
    return "—"


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _stats_row(label: str, stats: Stats | None, fmt: str) -> list[str]:
    if stats is None:
        return [label, "0", "—", "—", "—", "—", "—"]
    return [label, str(stats.n), _num(stats.min, fmt), _num(stats.mean, fmt),
            _num(stats.median, fmt), _num(stats.p95, fmt), _num(stats.max, fmt)]  # fmt: skip


# ------------------------------------------------------------------------ sections


def _criterion_row(c: CriterionResult, profile_name: str) -> list[str]:
    if c.threshold is None:
        threshold = "disabled"
    else:
        threshold = _THRESHOLD_FORMAT[c.key].format(c.threshold)
    measured = (
        _MEASURED_FORMAT[c.key].format(c.measured) if c.measured is not None else "not measurable"
    )
    source = "command-line override" if c.overridden else f"profile {profile_name}"
    if c.outcome in (Outcome.PASS, Outcome.FAIL):
        result = f"**{c.outcome}**"
    elif c.outcome is Outcome.NOT_MEASURABLE:
        result = f"NOT MEASURABLE: {_esc(c.note or 'no data')}"
    else:
        result = "NOT CHECKED"
    return [c.label, measured, threshold, source, result]


def _verdict(evaluation: Evaluation) -> list[str]:
    p = evaluation.profile
    failed = [c.label for c in evaluation.criteria if c.outcome is Outcome.FAIL]
    unmeasured = [c.label for c in evaluation.criteria if c.outcome is Outcome.NOT_MEASURABLE]
    out = [f"## Verdict: {evaluation.verdict}", ""]
    if evaluation.verdict is Verdict.FAIL:
        out.append(f"{_count(len(failed), 'criterion', 'criteria')} failed: {'; '.join(failed)}.")
    elif evaluation.verdict is Verdict.INCOMPLETE:
        out.append(
            f"No criterion failed, but {len(unmeasured)} could not be measured from this log: "
            f"{'; '.join(unmeasured)}."
        )
    else:
        out.append("All checked criteria passed.")
    origin = (
        "built in, same values as `profiles/standard-gnss.yaml`" if p.source == "built-in"
        else f"from `{p.source}`"
    )  # fmt: skip
    out += ["", f"Test profile: **{p.name}** ({origin}). {_esc(p.description)}."]
    if p.illustrative:
        out.append(
            "_Illustrative example values: not taken from any product datasheet, standard "
            "or company specification._"
        )
    if p.overridden:
        out.append(f"Thresholds overridden on the command line: {', '.join(sorted(p.overridden))}.")
    out.append("")
    out += _table(
        ["Criterion", "Measured", "Threshold", "Threshold source", "Result"],
        [_criterion_row(c, p.name) for c in evaluation.criteria],
    )
    return out


def _summary(log: LogReadResult, a: Analysis) -> list[str]:
    first = next((e for e in log.epochs if e.seconds is not None), None)
    last = next((e for e in reversed(log.epochs) if e.seconds is not None), None)
    rows = [
        ["Start (UTC)", _when(a.start_utc, first.time if first else None)],
        ["End (UTC)", _when(a.end_utc, last.time if last else None)],
        ["Duration", _num(a.duration_s, "{:.1f} s")],
        ["Epoch interval", _num(a.epoch_interval_s, "{:.2f} s")],
        ["Epochs present / expected", f"{a.epochs_present} / {a.epochs_expected}"],
        ["Epochs without a time", str(a.epochs_untimed)],
        ["Lines (blank, comment)", f"{log.line_count} ({log.blank_lines}, {log.comment_lines})"],
        ["Valid sentences (types not analysed)", f"{log.sentence_count} ({log.other_count})"],
        ["UTC day rollovers", str(log.rollovers)],
    ]
    return ["## Log summary", ""] + _table(["Item", "Value"], rows)


def _integrity(log: LogReadResult) -> list[str]:
    out = ["## Integrity", ""]
    counts = log.issue_counts()
    if not counts:
        return out + ["No problems found."]
    rows = []
    for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        counted = "no (unrecognised sentence type)" if kind in NOT_INTEGRITY_ERRORS else "yes"
        rows.append([str(kind), str(n), counted])
    out += _table(["Problem", "Count", "Counted as integrity error"], rows)
    shown = log.issues[:_MAX_EXAMPLES]
    out += ["", f"First {len(shown)} of {len(log.issues)} problems:", ""]
    out += _table(
        ["Line", "Problem", "Detail"],
        [[str(i.line_no), str(i.kind), _esc(i.message)] for i in shown],
    )
    return out


def _event_row(e: Event) -> list[str]:
    if e.duration_s is None:
        duration = "ongoing at end of log" if e.kind is EventKind.FIX_LOST else "—"
    else:
        duration = f"{e.duration_s:.1f} s"
    return [_EVENT_LABEL[e.kind], _when(e.utc, e.time), str(e.line_no), duration]


def _fix(a: Analysis) -> list[str]:
    ttff = _num(a.ttff_s, "{:.1f} s") if a.ttff_s is not None else f"not measurable ({a.ttff_note})"
    rows = [
        ["Fix epochs", f"{a.fix_epochs} of {a.epochs_expected} expected"],
        ["Fix availability", _num(a.availability_pct, "{:.1f} %")],
        ["Time to first fix", ttff],
        ["Fix-lost events", str(a.fix_lost_count)],
        ["Longest completed outage", _num(a.longest_outage_s, "{:.1f} s")],
    ]
    out = ["## Fix and outages", ""] + _table(["Item", "Value"], rows)
    if a.ttff_s is not None and a.ttff_note:
        out += ["", f"TTFF note: {a.ttff_note}."]
    out += ["", "### Events", ""]
    if not a.events:
        return out + ["No fix-lost, fix-regained or data-gap events."]
    return out + _table(["Event", "Time (UTC)", "Line", "Duration"], [_event_row(e) for e in a.events])


def _satellites(a: Analysis) -> list[str]:
    out = ["## Satellites used", ""]
    rows = [_stats_row("GGA (all systems)", a.sats_used_gga, "{:.1f}"),
            _stats_row("GSA (all systems)", a.sats_used_gsa, "{:.1f}")]  # fmt: skip
    rows += [_stats_row(f"GSA: {name}", s, "{:.1f}") for name, s in a.sats_used_by_constellation.items()]
    out += _table(["Source", "n", "min", "mean", "median", "p95", "max"], rows)
    gga, gsa = a.sats_used_gga, a.sats_used_gsa
    if gga and gsa and gga.max <= 12 and gsa.max > gga.max:
        out += ["", f"Note: GGA reports at most {gga.max} satellites here while GSA lists up to "
                    f"{gsa.max}. Some receivers cap the GGA count, so the GSA figures are the "
                    "better measure."]  # fmt: skip

    out += ["", "### Satellites used over time", ""]
    use_gsa = any(gsa_n is not None for _, _, gsa_n in a.sats_used_series)
    points = [(s, gsa_n if use_gsa else gga_n) for s, gga_n, gsa_n in a.sats_used_series]
    points = [(s, n) for s, n in points if n is not None]
    if not points:
        return out + ["No satellite data."]
    start = points[0][0]
    span = points[-1][0] - start
    window = 10 if span <= 120 else 60
    buckets: dict[int, list[int]] = {}
    for s, n in points:
        buckets.setdefault(int((s - start) // window), []).append(n)
    source = "GSA" if use_gsa else "GGA"
    out.append(f"All epochs, {window} s windows from the first epoch, {source} counts:")
    out.append("")
    out += _table(
        ["Window", "min", "mean", "max"],
        [[f"{k * window}–{(k + 1) * window} s", str(min(v)), f"{sum(v) / len(v):.1f}", str(max(v))]
         for k, v in sorted(buckets.items())],
    )  # fmt: skip
    return out


def _hdop(a: Analysis) -> list[str]:
    out = ["## HDOP (fix epochs)", ""]
    if a.hdop is None:
        return out + ["No HDOP values in fix epochs."]
    return out + _table(["n", "min", "mean", "median", "p95", "max"],
                        [_stats_row("", a.hdop, "{:.2f}")[1:]])  # fmt: skip


def _accuracy(a: Analysis) -> list[str]:
    out = ["## Position accuracy", ""]
    acc = a.accuracy
    if acc is None:
        return out + ["No fix positions, so CEP cannot be computed."]
    point = f"{acc.reference_lat:.7f}, {acc.reference_lon:.7f}"
    reference = (
        f"mean of the fix positions ({point})" if acc.reference_kind == "mean"
        else f"given reference point {point}"
    )  # fmt: skip
    out += _table(["Item", "Value"], [
        ["Reference", reference],
        ["Positions (n)", str(acc.n)],
        ["CEP50", f"{acc.cep50_m:.3f} m"],
        ["CEP95", f"{acc.cep95_m:.3f} m"],
        ["Largest error", f"{acc.max_m:.3f} m"],
    ])  # fmt: skip
    out += ["", "- CEP assumes the receiver was static during the log."]
    if acc.reference_kind == "mean":
        out.append("- Against the mean position this measures precision (spread), not accuracy "
                   "against a surveyed point.")  # fmt: skip
    if acc.n < _SMALL_SAMPLE:
        out.append(f"- Small sample (n = {acc.n} < {_SMALL_SAMPLE}): CEP95 depends on the few "
                   "largest errors and is only indicative.")  # fmt: skip
    return out


# --------------------------------------------------------------------------- build


def build_report(
    *,
    log_name: str,
    sha256: str,
    header_lines: Sequence[str],
    log: LogReadResult,
    analysis: Analysis,
    evaluation: Evaluation,
    generated_at: dt.datetime,
) -> str:
    out = [f"# GNSS log test report: {log_name}", ""]
    if any(line.startswith("# SYNTHETIC") for line in header_lines):
        out += ["> **SYNTHETIC LOG**: generated data, not a real recording.", ""]
    if any(line.startswith("# FAULT-INJECTED") for line in header_lines):
        out += ["> **FAULT-INJECTED LOG**: faults were added on purpose; see its manifest.", ""]
    out += _table(["Item", "Value"], [
        ["Log file", f"`{_esc(log_name)}`"],
        ["SHA-256", f"`{sha256}`"],
        ["Generated", f"{generated_at:%Y-%m-%d %H:%M:%S} UTC by gnsskit {__version__}"],
    ])  # fmt: skip
    for section in (
        _verdict(evaluation),
        _summary(log, analysis),
        _integrity(log),
        _fix(analysis),
        _satellites(analysis),
        _hdop(analysis),
        _accuracy(analysis),
    ):
        out += [""] + section
    out += ["", "## Definitions", ""]
    out += [f"- **{term}**: {text}" for term, text in DEFINITIONS]
    out += ["", "---", f"_All values are computed from the log file above by gnsskit {__version__}._"]
    return "\n".join(out) + "\n"
