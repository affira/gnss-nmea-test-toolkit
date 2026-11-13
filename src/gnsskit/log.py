"""Read an NMEA log into time-ordered epochs and an integrity report.

Pass 1 groups sentences into epochs. A GGA or RMC carrying a new UTC time starts a
new epoch; GSA and GSV sentences (which have no time) join the current epoch. This
works whichever order the receiver uses (u-blox sends GGA first, Unicore sends RMC
first).

Pass 2 assigns each epoch a day index and a date. RMC dates are used when they are
consistent; otherwise a UTC day rollover is inferred when the time of day goes
backwards by more than 12 hours. A smaller backward step is reported as
TIME_BACKWARDS, not treated as a rollover.

Nothing here raises on bad input: every problem becomes an `Issue` with its line number.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from gnsskit.nmea import (
    GGA,
    GSA,
    GSV,
    RMC,
    ErrorKind,
    OtherSentence,
    ParseError,
    SatelliteInView,
    parse_sentence,
)

__all__ = [
    "Epoch",
    "GsvGroup",
    "Issue",
    "LogIssueKind",
    "LogReadResult",
    "read_lines",
    "read_log",
]

_SECONDS_PER_DAY = 86_400
_ROLLOVER_THRESHOLD_S = 12 * 3600
_MAX_ISSUE_LINE = 120


class LogIssueKind(StrEnum):
    TIME_BACKWARDS = "time_backwards"  # epoch time earlier than the previous one
    DATE_JUMP = "date_jump"  # RMC date inconsistent with the timeline; ignored
    DUPLICATE_SENTENCE = "duplicate_sentence"  # second GGA/RMC within one epoch
    ORPHAN_SENTENCE = "orphan_sentence"  # GSA/GSV before any epoch has started
    INCOMPLETE_GSV = "incomplete_gsv"  # multi-part GSV with missing/extra parts


@dataclass(frozen=True)
class Issue:
    line_no: int
    kind: ErrorKind | LogIssueKind
    message: str
    line: str = ""


@dataclass(frozen=True)
class GsvGroup:
    """All parts of one GSV message (one talker + signal) within an epoch."""

    talker: str
    signal_id: int | None
    satellites: tuple[SatelliteInView, ...]
    complete: bool


@dataclass
class Epoch:
    first_line: int
    time: dt.time | None
    gga: GGA | None = None
    rmc: RMC | None = None
    gsa: tuple[GSA, ...] = ()
    gsv_groups: tuple[GsvGroup, ...] = ()
    day: int = 0  # days since the first epoch (UTC)
    date: dt.date | None = None
    rmc_line: int | None = None

    @property
    def seconds(self) -> float | None:
        """Seconds on a continuous timeline: day * 86400 + time of day."""
        if self.time is None:
            return None
        t = self.time
        return (
            self.day * _SECONDS_PER_DAY
            + t.hour * 3600
            + t.minute * 60
            + t.second
            + t.microsecond / 1e6
        )

    @property
    def utc(self) -> dt.datetime | None:
        if self.time is None or self.date is None:
            return None
        return dt.datetime.combine(self.date, self.time, tzinfo=dt.timezone.utc)


@dataclass
class LogReadResult:
    epochs: list[Epoch]
    issues: list[Issue]
    line_count: int = 0
    blank_lines: int = 0
    comment_lines: int = 0
    sentence_count: int = 0  # valid sentences, including OtherSentence
    other_count: int = 0  # valid sentences of types we do not interpret
    rollovers: int = 0

    def issue_counts(self) -> dict[ErrorKind | LogIssueKind, int]:
        return dict(Counter(issue.kind for issue in self.issues))


# ------------------------------------------------------------------- pass 1: grouping


@dataclass
class _Builder:
    epoch: Epoch
    gsa: list[GSA] = field(default_factory=list)
    gsv: list[tuple[int, GSV]] = field(default_factory=list)


def _assemble_gsv(parts: list[tuple[int, GSV]], issues: list[Issue]) -> tuple[GsvGroup, ...]:
    grouped: dict[tuple[str, int | None], list[tuple[int, GSV]]] = {}
    for line_no, gsv in parts:
        grouped.setdefault((gsv.talker, gsv.signal_id), []).append((line_no, gsv))

    groups = []
    for (talker, signal_id), items in grouped.items():
        sentences = [gsv for _, gsv in items]
        totals = {gsv.total_msgs for gsv in sentences}
        numbers = [gsv.msg_num for gsv in sentences]
        ordered = sorted(sentences, key=lambda gsv: gsv.msg_num)
        satellites = tuple(sat for gsv in ordered for sat in gsv.satellites)
        complete = (
            len(totals) == 1
            and sorted(numbers) == list(range(1, totals.pop() + 1))
            and len(satellites) == sentences[0].sats_in_view
        )
        if not complete:
            issues.append(
                Issue(
                    line_no=items[0][0],
                    kind=LogIssueKind.INCOMPLETE_GSV,
                    message=(
                        f"{talker}GSV signal {signal_id}: parts {sorted(numbers)} of "
                        f"{sorted({g.total_msgs for g in sentences})}, "
                        f"{len(satellites)} satellites listed vs "
                        f"{sentences[0].sats_in_view} in view"
                    ),
                )
            )
        groups.append(GsvGroup(talker, signal_id, satellites, complete))
    return tuple(groups)


def _finish(builder: _Builder, issues: list[Issue]) -> Epoch:
    epoch = builder.epoch
    epoch.gsa = tuple(builder.gsa)
    epoch.gsv_groups = _assemble_gsv(builder.gsv, issues)
    return epoch


# ----------------------------------------------------------------- pass 2: timeline


def _time_seconds(t: dt.time) -> float:
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6


def _assign_days(epochs: list[Epoch], issues: list[Issue]) -> int:
    """Set epoch.day and epoch.date; return the number of day rollovers."""
    base_date: dt.date | None = None  # date of day index 0
    prev: Epoch | None = None  # previous epoch with a time
    day = 0
    rollovers = 0

    for epoch in epochs:
        if epoch.time is None:
            epoch.day = day
            continue
        rmc_date = epoch.rmc.date if epoch.rmc is not None else None

        if prev is None:
            day = 0
            backward = False
        else:
            assert prev.time is not None
            step = _time_seconds(epoch.time) - _time_seconds(prev.time)
            backward = step < 0
            by_time = prev.day + 1 if step < -_ROLLOVER_THRESHOLD_S else prev.day
            day = by_time
            if rmc_date is not None and base_date is not None:
                by_date = (rmc_date - base_date).days
                if by_date == prev.day or (by_date == prev.day + 1 and backward):
                    day = by_date
                else:
                    issues.append(
                        Issue(
                            line_no=epoch.rmc_line or epoch.first_line,
                            kind=LogIssueKind.DATE_JUMP,
                            message=(
                                f"RMC date {rmc_date} does not fit the timeline "
                                f"(expected day {by_time} after {base_date}); ignored"
                            ),
                        )
                    )
            if day > prev.day:  # by construction day is at most prev.day + 1
                rollovers += 1
            elif backward:
                issues.append(
                    Issue(
                        line_no=epoch.first_line,
                        kind=LogIssueKind.TIME_BACKWARDS,
                        message=f"time {epoch.time} is earlier than previous epoch {prev.time}",
                    )
                )

        if base_date is None and rmc_date is not None:
            base_date = rmc_date - dt.timedelta(days=day)
        epoch.day = day
        prev = epoch

    if base_date is not None:
        for epoch in epochs:
            epoch.date = base_date + dt.timedelta(days=epoch.day)
    return rollovers


# ------------------------------------------------------------------------- reading


def read_lines(lines: Iterable[str]) -> LogReadResult:
    epochs: list[Epoch] = []
    issues: list[Issue] = []
    result = LogReadResult(epochs=epochs, issues=issues)
    current: _Builder | None = None

    def start(line_no: int, time: dt.time | None) -> _Builder:
        nonlocal current
        if current is not None:
            epochs.append(_finish(current, issues))
        current = _Builder(Epoch(first_line=line_no, time=time))
        return current

    def issue(line_no: int, kind: ErrorKind | LogIssueKind, message: str, line: str) -> None:
        issues.append(Issue(line_no, kind, message, line.strip()[:_MAX_ISSUE_LINE]))

    for line_no, line in enumerate(lines, start=1):
        result.line_count += 1
        text = line.strip()
        if text == "":
            result.blank_lines += 1
            continue
        if text.startswith("#"):
            result.comment_lines += 1
            continue

        sentence = parse_sentence(line)
        if isinstance(sentence, ParseError):
            issue(line_no, sentence.kind, sentence.message, line)
            continue
        result.sentence_count += 1
        if isinstance(sentence, OtherSentence):
            result.other_count += 1
            continue

        if isinstance(sentence, (GGA, RMC)):
            slot = "gga" if isinstance(sentence, GGA) else "rmc"
            time = sentence.time
            if time is not None:
                if current is None or current.epoch.time != time:
                    current = start(line_no, time)
            elif current is None or current.epoch.time is not None or getattr(
                current.epoch, slot
            ):
                current = start(line_no, None)
            if getattr(current.epoch, slot) is not None:
                issue(
                    line_no,
                    LogIssueKind.DUPLICATE_SENTENCE,
                    f"second {slot.upper()} in epoch {time}; kept the first",
                    line,
                )
                continue
            setattr(current.epoch, slot, sentence)
            if slot == "rmc":
                current.epoch.rmc_line = line_no
            continue

        # GSA / GSV: no time of their own; they belong to the current epoch.
        if current is None:
            issue(
                line_no,
                LogIssueKind.ORPHAN_SENTENCE,
                "sentence appears before any GGA/RMC; cannot assign it to an epoch",
                line,
            )
            continue
        if isinstance(sentence, GSA):
            current.gsa.append(sentence)
        else:
            current.gsv.append((line_no, sentence))

    if current is not None:
        epochs.append(_finish(current, issues))
    result.rollovers = _assign_days(epochs, issues)
    issues.sort(key=lambda i: i.line_no)
    return result


def read_log(path: str | Path) -> LogReadResult:
    """Read a log file. Undecodable bytes become U+FFFD and are reported as NON_NMEA."""
    with open(path, encoding="utf-8", errors="replace", newline="") as handle:
        content = handle.read()
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return read_lines(lines)
