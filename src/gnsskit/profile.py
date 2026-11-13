"""Test profiles (pass/fail thresholds) and evaluation of an analysed log against them.

A profile is a small YAML file:

    name: standard-gnss
    description: ...
    illustrative: true          # true = example values, not from any specification
    thresholds:
      min_availability_pct: 95.0
      max_ttff_s: 60.0
      max_hdop_p95: 2.0
      max_cep95_m: 5.0
      max_integrity_errors: 0

All five thresholds must be present; `null` disables that check. Values exactly at
a threshold pass.

Outcome of each criterion: PASS, FAIL, NOT_MEASURABLE (the log cannot show it, e.g.
TTFF when the log starts with a fix) or NOT_CHECKED (threshold is null).
Overall verdict: FAIL if any criterion fails, otherwise INCOMPLETE if any is not
measurable, otherwise PASS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from gnsskit.analysis import Analysis
from gnsskit.log import LogReadResult
from gnsskit.nmea import ErrorKind

__all__ = [
    "DEFAULT_PROFILE",
    "THRESHOLD_KEYS",
    "CriterionResult",
    "Evaluation",
    "Outcome",
    "Profile",
    "ProfileError",
    "Verdict",
    "evaluate",
    "integrity_error_count",
    "load_profile",
    "with_overrides",
]

THRESHOLD_KEYS = (
    "min_availability_pct",
    "max_ttff_s",
    "max_hdop_p95",
    "max_cep95_m",
    "max_integrity_errors",
)
_TOP_KEYS = {"name", "description", "illustrative", "thresholds"}

# Problems that are not integrity errors: valid sentences of a type we do not know.
NOT_INTEGRITY_ERRORS = frozenset({ErrorKind.UNKNOWN_TYPE})


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    illustrative: bool
    thresholds: dict[str, float | int | None]
    source: str  # file name, or "built-in"
    overridden: frozenset[str] = field(default_factory=frozenset)


DEFAULT_PROFILE = Profile(
    name="standard-gnss",
    description="General-purpose GNSS receiver acceptance check (illustrative)",
    illustrative=True,
    thresholds={
        "min_availability_pct": 95.0,
        "max_ttff_s": 60.0,
        "max_hdop_p95": 2.0,
        "max_cep95_m": 5.0,
        "max_integrity_errors": 0,
    },
    source="built-in",
)


# ------------------------------------------------------------------------- loading


def _check_threshold(key: str, value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProfileError(f"{key} must be a number or null, got {value!r}")
    if value < 0:
        raise ProfileError(f"{key} must not be negative, got {value}")
    if key == "min_availability_pct" and value > 100:
        raise ProfileError(f"{key} must be at most 100, got {value}")
    if key == "max_integrity_errors":
        if value != int(value):
            raise ProfileError(f"{key} must be a whole number, got {value}")
        return int(value)
    return float(value)


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"cannot read profile {path}: {exc.strerror}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path.name}: invalid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ProfileError(f"{path.name}: a profile must be a YAML mapping")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ProfileError(f"{path.name}: unknown keys {sorted(unknown)}")
    for key in ("name", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ProfileError(f"{path.name}: '{key}' must be a non-empty string")
    if not isinstance(data.get("illustrative"), bool):
        raise ProfileError(f"{path.name}: 'illustrative' must be true or false")
    raw = data.get("thresholds")
    if not isinstance(raw, dict):
        raise ProfileError(f"{path.name}: 'thresholds' must be a mapping")
    unknown = set(raw) - set(THRESHOLD_KEYS)
    if unknown:
        raise ProfileError(f"{path.name}: unknown thresholds {sorted(unknown)}")
    missing = [k for k in THRESHOLD_KEYS if k not in raw]
    if missing:
        raise ProfileError(f"{path.name}: missing thresholds {missing} (use null to disable)")
    return Profile(
        name=data["name"],
        description=data["description"],
        illustrative=data["illustrative"],
        thresholds={k: _check_threshold(k, raw[k]) for k in THRESHOLD_KEYS},
        source=path.name,
    )


def with_overrides(profile: Profile, overrides: dict[str, float | int | None]) -> Profile:
    unknown = set(overrides) - set(THRESHOLD_KEYS)
    if unknown:
        raise ProfileError(f"unknown thresholds {sorted(unknown)}")
    thresholds = dict(profile.thresholds)
    for key, value in overrides.items():
        thresholds[key] = _check_threshold(key, value)
    return replace(profile, thresholds=thresholds, overridden=profile.overridden | set(overrides))


# ---------------------------------------------------------------------- evaluation


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_MEASURABLE = "NOT MEASURABLE"
    NOT_CHECKED = "NOT CHECKED"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class CriterionResult:
    key: str
    label: str
    comparison: str  # ">=" or "<="
    unit: str
    threshold: float | int | None
    measured: float | int | None
    outcome: Outcome
    overridden: bool
    note: str | None = None


@dataclass(frozen=True)
class Evaluation:
    profile: Profile
    criteria: tuple[CriterionResult, ...]
    verdict: Verdict


def integrity_error_count(log: LogReadResult) -> int:
    return sum(1 for issue in log.issues if issue.kind not in NOT_INTEGRITY_ERRORS)


def evaluate(log: LogReadResult, analysis: Analysis, profile: Profile) -> Evaluation:
    acc = analysis.accuracy
    measurements: list[tuple[str, str, str, str, float | int | None, str | None]] = [
        ("min_availability_pct", "Fix availability", ">=", "%", analysis.availability_pct,
         None if analysis.availability_pct is not None else "log has no epochs"),
        ("max_ttff_s", "Time to first fix", "<=", "s", analysis.ttff_s, analysis.ttff_note),
        ("max_hdop_p95", "HDOP, 95th percentile", "<=", "",
         analysis.hdop.p95 if analysis.hdop else None,
         None if analysis.hdop else "no fix epochs with HDOP"),
        ("max_cep95_m", "CEP95", "<=", "m", acc.cep95_m if acc else None,
         None if acc else "no fix positions"),
        ("max_integrity_errors", "Integrity errors", "<=", "", integrity_error_count(log),
         "unrecognised sentence types are reported but not counted"),
    ]  # fmt: skip
    results = []
    for key, label, comparison, unit, measured, note in measurements:
        threshold = profile.thresholds[key]
        if threshold is None:
            outcome = Outcome.NOT_CHECKED
        elif measured is None:
            outcome = Outcome.NOT_MEASURABLE
        elif comparison == ">=":
            outcome = Outcome.PASS if measured >= threshold else Outcome.FAIL
        else:
            outcome = Outcome.PASS if measured <= threshold else Outcome.FAIL
        results.append(
            CriterionResult(key, label, comparison, unit, threshold, measured, outcome,
                            key in profile.overridden, note)  # fmt: skip
        )
    outcomes = {r.outcome for r in results}
    if Outcome.FAIL in outcomes:
        verdict = Verdict.FAIL
    elif Outcome.NOT_MEASURABLE in outcomes:
        verdict = Verdict.INCOMPLETE
    else:
        verdict = Verdict.PASS
    return Evaluation(profile=profile, criteria=tuple(results), verdict=verdict)
