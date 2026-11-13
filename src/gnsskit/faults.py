"""Controlled fault injection into a clean NMEA log.

`inject(lines, plan)` returns new lines plus a JSON-serialisable manifest that says
exactly what was injected and what a correct analyser must report. Tests compare
the analyser's output with the manifest. The same seed always gives the same output.

Faults, in the order they are applied:

1. midnight_shift_s: shift all times so the first epoch is this many seconds before
   00:00:00 UTC (dates in RMC and ZDA move with it). Tests UTC day rollover.
2. outages (start_s, duration_s): epochs in the window become "no fix" the way a
   receiver reports it (GGA quality 0, RMC status V, GSA fix type 1, no SNR in GSV).
3. gaps (start_s, duration_s): every line of the epochs in the window is removed.
4. dropped_gsv_parts: drop one part of N different multi-part GSV messages.
5. bad_checksums, truncated, blank_positions: corrupt N valid sentences each.
   Never both the GGA and RMC of one epoch, so every epoch survives.
6. unknown_sentences, garbage_lines: insert N lines at random positions.

Windows are seconds from the first epoch. Only GGA, RMC, GSA and GSV are changed by
an outage; other sentences (VTG, GLL, proprietary) keep their original content.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import re
import string
from dataclasses import dataclass, field
from typing import Any

from gnsskit import __version__
from gnsskit.analysis import is_fix
from gnsskit.log import read_lines
from gnsskit.nmea import GGA, GSV, RMC, ParseError, nmea_checksum, parse_sentence

__all__ = ["FaultPlan", "InjectionResult", "inject"]

LABEL = "# FAULT-INJECTED"
_TIME_FIELD = {"GGA": 0, "RMC": 0, "GLL": 4, "GST": 0, "GBS": 0, "ZDA": 0, "GNS": 0}
_TIME = re.compile(r"(\d{2})(\d{2})(\d{2})(\.\d+)?")


@dataclass(frozen=True)
class FaultPlan:
    seed: int = 0
    bad_checksums: int = 0
    truncated: int = 0
    blank_positions: int = 0
    unknown_sentences: int = 0
    garbage_lines: int = 0
    dropped_gsv_parts: int = 0
    outages: tuple[tuple[float, float], ...] = ()
    gaps: tuple[tuple[float, float], ...] = ()
    midnight_shift_s: float | None = None


@dataclass(frozen=True)
class InjectionResult:
    lines: list[str]
    manifest: dict[str, Any]


@dataclass
class _Line:
    text: str
    origin: int | None  # 1-based line number in the input, None if inserted
    tags: list[str] = field(default_factory=list)
    deleted: bool = False


# --------------------------------------------------------------------- NMEA editing


def _split(text: str) -> tuple[str, list[str]]:
    body = text.strip()[1:].rpartition("*")[0]
    address, *fields = body.split(",")
    return address, fields


def _build(address: str, fields: list[str]) -> str:
    body = ",".join([address, *fields])
    return f"${body}*{nmea_checksum(body)}"


def _shift_time(value: str, delta: int) -> tuple[str, int] | None:
    """Shift hhmmss[.ss] by whole seconds; return (new value, day carry)."""
    match = _TIME.fullmatch(value)
    if not match:
        return None
    hh, mm, ss, frac = match.groups()
    total = int(hh) * 3600 + int(mm) * 60 + int(ss) + delta
    carry = math.floor(total / 86_400)
    total -= carry * 86_400
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}{m:02d}{s:02d}{frac or ''}", carry


def _shift_line(text: str, delta: int) -> str | None:
    if isinstance(parse_sentence(text), ParseError):
        return None
    address, f = _split(text)
    idx = _TIME_FIELD.get(address[2:]) if len(address) == 5 else None
    if idx is None or len(f) <= idx or f[idx] == "":
        return None
    shifted = _shift_time(f[idx], delta)
    if shifted is None:
        return None
    f[idx], carry = shifted
    if carry:
        if address[2:] == "RMC" and f[8]:
            d = dt.datetime.strptime(f[8], "%d%m%y").date() + dt.timedelta(days=carry)
            f[8] = d.strftime("%d%m%y")
        if address[2:] == "ZDA" and all(f[1:4]):
            d = dt.date(int(f[3]), int(f[2]), int(f[1])) + dt.timedelta(days=carry)
            f[1], f[2], f[3] = f"{d.day:02d}", f"{d.month:02d}", f"{d.year:04d}"
    return _build(address, f)


def _no_fix(text: str) -> str | None:
    """Rewrite a GGA/RMC/GSA/GSV sentence as a receiver with no fix would send it."""
    sentence = parse_sentence(text)
    if isinstance(sentence, ParseError):
        return None
    address, f = _split(text)
    kind = address[2:]
    if kind == "GGA":
        f[1:5] = ["", "", "", ""]
        f[5], f[6], f[7], f[8] = "0", "00", "", ""
        if len(f) > 10:
            f[10] = ""
    elif kind == "RMC":
        f[1] = "V"
        f[2:8] = ["", "", "", "", "", ""]
        if len(f) > 11:
            f[11] = "N"
        if len(f) > 12:
            f[12] = "V"
    elif kind == "GSA":
        f[1] = "1"
        f[2:17] = [""] * 15
    elif kind == "GSV":
        for i in range((len(f) - 3) // 4):
            f[3 + 4 * i + 3] = ""
    else:
        return None
    return _build(address, f)


# ------------------------------------------------------------------------ helpers


def _check_plan(plan: FaultPlan) -> None:
    for name in ("bad_checksums", "truncated", "blank_positions", "unknown_sentences",
                 "garbage_lines", "dropped_gsv_parts"):  # fmt: skip
        if getattr(plan, name) < 0:
            raise ValueError(f"{name} must not be negative")
    for name in ("outages", "gaps"):
        for start, duration in getattr(plan, name):
            if start < 0 or duration <= 0:
                raise ValueError(f"{name}: window ({start}, {duration}) must have start >= 0 "
                                 "and duration > 0")  # fmt: skip
    if plan.midnight_shift_s is not None and not 0 < plan.midnight_shift_s < 86_400:
        raise ValueError("midnight_shift_s must be between 0 and 86400")


class _Epochs:
    """Map each line index to its epoch, using the log reader's own grouping."""

    def __init__(self, texts: list[str]):
        log = read_lines(texts)
        self.epochs = log.epochs
        first = next((e.seconds for e in self.epochs if e.seconds is not None), None)
        self.rel = [
            (e.seconds - first) if e.seconds is not None and first is not None else None
            for e in self.epochs
        ]
        self.of_line: list[int | None] = [None] * len(texts)
        starts = [e.first_line - 1 for e in self.epochs] + [len(texts)]
        for k in range(len(self.epochs)):
            for i in range(starts[k], starts[k + 1]):
                self.of_line[i] = k

    def in_window(self, start: float, duration: float) -> list[int]:
        return [k for k, r in enumerate(self.rel) if r is not None and start <= r < start + duration]


def _not_enough(name: str, wanted: int, available: int) -> ValueError:
    return ValueError(f"{name}: asked for {wanted} but only {available} eligible lines")


# --------------------------------------------------------------------------- inject


def inject(lines: list[str], plan: FaultPlan, source: str = "<lines>") -> InjectionResult:
    _check_plan(plan)
    rng = random.Random(plan.seed)
    work = [_Line(text, i + 1) for i, text in enumerate(lines)]
    faults: list[dict[str, Any]] = []

    def add_fault(entry: dict[str, Any]) -> str:
        entry["id"] = f"{entry['type']}#{len(faults)}"
        faults.append(entry)
        return entry["id"]

    # 1. midnight shift ------------------------------------------------------------
    if plan.midnight_shift_s is not None:
        epochs = _Epochs([w.text for w in work])
        timed = [e for e in epochs.epochs if e.seconds is not None]
        if not timed:
            raise ValueError("midnight_shift_s: the log has no timed epochs")
        first_tod = timed[0].seconds
        delta = round(86_400 - plan.midnight_shift_s - first_tod)  # type: ignore[operator]
        fid = add_fault({"type": "midnight_shift", "seconds_before_midnight": plan.midnight_shift_s,
                         "delta_s": delta})  # fmt: skip
        for w in work:
            new = _shift_line(w.text, delta)
            if new is not None:
                w.text = new
                w.tags.append(fid)
        span = timed[-1].seconds - timed[0].seconds  # type: ignore[operator]
        faults[-1]["expect_rollovers"] = 1 if span >= plan.midnight_shift_s else 0

    epochs = _Epochs([w.text for w in work])

    # 2. outages -------------------------------------------------------------------
    for start, duration in plan.outages:
        window = epochs.in_window(start, duration)
        if not window:
            raise ValueError(f"outages: window ({start}, {duration}) contains no epochs")
        before = window[0] - 1
        after = window[-1] + 1
        fid = add_fault({
            "type": "outage", "start_s": start, "duration_s": duration, "epochs": len(window),
            "expect_fix_lost": before >= 0 and is_fix(epochs.epochs[before]),
            "expect_fix_regained": after < len(epochs.epochs) and is_fix(epochs.epochs[after]),
        })  # fmt: skip
        chosen = set(window)
        for i, w in enumerate(work):
            if epochs.of_line[i] in chosen:
                new = _no_fix(w.text)
                if new is not None:
                    w.text = new
                    w.tags.append(fid)

    # 3. gaps ----------------------------------------------------------------------
    for start, duration in plan.gaps:
        window = epochs.in_window(start, duration)
        if not window:
            raise ValueError(f"gaps: window ({start}, {duration}) contains no epochs")
        if window[0] == 0 or window[-1] == len(epochs.epochs) - 1:
            raise ValueError(f"gaps: window ({start}, {duration}) must have epochs before "
                             "and after it, or the gap cannot be detected")  # fmt: skip
        fid = add_fault({"type": "gap", "start_s": start, "duration_s": duration,
                         "epochs_removed": len(window), "expect_data_gaps": 1})  # fmt: skip
        chosen = set(window)
        for i, w in enumerate(work):
            if epochs.of_line[i] in chosen and not w.text.lstrip().startswith("#"):
                w.deleted = True
                w.tags.append(fid)

    # 4. dropped GSV parts ---------------------------------------------------------
    if plan.dropped_gsv_parts:
        groups: dict[tuple, list[int]] = {}
        for i, w in enumerate(work):
            sentence = parse_sentence(w.text) if not w.deleted else None
            if isinstance(sentence, GSV) and sentence.total_msgs >= 2 and epochs.of_line[i] is not None:
                key = (epochs.of_line[i], sentence.talker, sentence.signal_id)
                groups.setdefault(key, []).append(i)
        keys = sorted(groups, key=lambda k: (k[0], k[1], -1 if k[2] is None else k[2]))
        if plan.dropped_gsv_parts > len(keys):
            raise _not_enough("dropped_gsv_parts", plan.dropped_gsv_parts, len(keys))
        fid = add_fault({"type": "dropped_gsv_parts", "count": plan.dropped_gsv_parts,
                         "expect_issue": {"kind": "incomplete_gsv", "match": "at_least",
                                          "count": plan.dropped_gsv_parts}})  # fmt: skip
        for key in rng.sample(keys, plan.dropped_gsv_parts):
            victim = work[rng.choice(groups[key])]
            victim.deleted = True
            victim.tags.append(fid)

    # 5. corrupt valid sentences ---------------------------------------------------
    killed: dict[int, set[str]] = {}
    slots: dict[int, set[str]] = {}
    for i, w in enumerate(work):
        sentence = parse_sentence(w.text) if not w.deleted else None
        if isinstance(sentence, (GGA, RMC)) and epochs.of_line[i] is not None:
            slots.setdefault(epochs.of_line[i], set()).add(type(sentence).__name__)  # type: ignore[arg-type]

    def eligible(i: int, w: _Line, *, only_fixed_gga: bool = False) -> bool:
        if w.deleted:
            return False
        sentence = parse_sentence(w.text)
        if isinstance(sentence, ParseError):  # also excludes lines already corrupted
            return False
        if only_fixed_gga and not (isinstance(sentence, GGA) and sentence.quality > 0):
            return False
        if isinstance(sentence, (GGA, RMC)):
            k = epochs.of_line[i]
            if k is None:
                return False
            this = type(sentence).__name__
            other = "RMC" if this == "GGA" else "GGA"
            # The other time-bearing sentence must exist and stay intact.
            if other not in slots.get(k, set()) or other in killed.get(k, set()):
                return False
        return True

    def corrupt(name: str, count: int, change, issue_kind: str, **flags) -> None:
        if not count:
            return
        candidates = [i for i, w in enumerate(work) if eligible(i, w, **flags)]
        rng.shuffle(candidates)
        fid = add_fault({"type": name, "count": count,
                         "expect_issue": {"kind": issue_kind, "match": "exactly", "count": count}})  # fmt: skip
        done = 0
        for i in candidates:
            if done == count:
                break
            if not eligible(i, work[i], **flags):  # an earlier pick may have ruled it out
                continue
            sentence = parse_sentence(work[i].text)
            if isinstance(sentence, (GGA, RMC)):
                killed.setdefault(epochs.of_line[i], set()).add(type(sentence).__name__)  # type: ignore[arg-type]
            work[i].text = change(work[i].text)
            work[i].tags.append(fid)
            done += 1
        if done < count:
            raise _not_enough(name, count, done)

    def bad_checksum(text: str) -> str:
        body, _, checksum = text.rpartition("*")
        wrong = (int(checksum, 16) + rng.randrange(1, 256)) % 256
        return f"{body}*{wrong:02X}"

    def truncate(text: str) -> str:
        return text[: rng.randint(1, text.index("*"))]

    def blank_position(text: str) -> str:
        address, f = _split(text)
        f[1:5] = ["", "", "", ""]
        return _build(address, f)

    corrupt("bad_checksums", plan.bad_checksums, bad_checksum, "bad_checksum")
    corrupt("truncated", plan.truncated, truncate, "truncated")
    corrupt("blank_positions", plan.blank_positions, blank_position, "empty_field",
            only_fixed_gga=True)  # fmt: skip

    # 6. inserted lines ------------------------------------------------------------
    def insert(name: str, count: int, make, issue_kind: str) -> None:
        if not count:
            return
        fid = add_fault({"type": name, "count": count,
                         "expect_issue": {"kind": issue_kind, "match": "exactly", "count": count}})  # fmt: skip
        for _ in range(count):
            work.insert(rng.randrange(len(work) + 1), _Line(make(), None, [fid]))

    def unknown() -> str:
        return _build("GPXYZ", [str(rng.randrange(1000)), "INJECTED"])

    def garbage() -> str:
        alphabet = string.ascii_letters + string.digits + " ,.*$!"
        return rng.choice(string.ascii_letters) + "".join(
            rng.choice(alphabet) for _ in range(rng.randrange(10, 60))
        )

    insert("unknown_sentences", plan.unknown_sentences, unknown, "unknown_type")
    insert("garbage_lines", plan.garbage_lines, garbage, "non_nmea")

    # output ----------------------------------------------------------------------
    header = (
        f"{LABEL} from {source} by gnsskit {__version__} inject, seed {plan.seed}. "
        "Not an original recording: faults were added on purpose; see the manifest."
    )
    out = [header] + [w.text for w in work if not w.deleted]
    output_no = {}
    n = 1
    for w in work:
        if not w.deleted:
            n += 1
            output_no[id(w)] = n
    for entry in faults:
        mine = [w for w in work if entry["id"] in w.tags]
        if any(w.deleted for w in mine):
            entry["removed_input_lines"] = sorted(w.origin for w in mine if w.origin)
        kept = sorted(output_no[id(w)] for w in mine if not w.deleted)
        if kept:
            entry["lines"] = kept
    manifest = {
        "tool": f"gnsskit {__version__} inject",
        "source": source,
        "seed": plan.seed,
        "input_lines": len(lines),
        "output_lines": len(out),
        "faults": faults,
    }
    return InjectionResult(lines=out, manifest=manifest)

