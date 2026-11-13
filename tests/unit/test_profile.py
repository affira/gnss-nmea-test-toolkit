"""Unit tests for test profiles and PASS/FAIL evaluation (gnsskit.profile)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gnsskit.analysis import analyse
from gnsskit.log import read_lines, read_log
from gnsskit.nmea import nmea_checksum
from gnsskit.profile import (
    DEFAULT_PROFILE,
    THRESHOLD_KEYS,
    Outcome,
    ProfileError,
    Verdict,
    evaluate,
    integrity_error_count,
    load_profile,
    with_overrides,
)

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"
DATA = ROOT / "data"


def write(tmp_path, text: str) -> Path:
    path = tmp_path / "p.yaml"
    path.write_text(text)
    return path


VALID = """\
name: t
description: d
illustrative: true
thresholds:
  min_availability_pct: 95
  max_ttff_s: 60
  max_hdop_p95: 2.0
  max_cep95_m: 5.0
  max_integrity_errors: 0
"""


# ---------------------------------------------------------------- example profiles


class TestExampleProfiles:
    def test_standard_profile_has_the_agreed_defaults(self):
        p = load_profile(PROFILES / "standard-gnss.yaml")
        assert p.name == "standard-gnss"
        assert p.thresholds == {
            "min_availability_pct": 95.0,
            "max_ttff_s": 60.0,
            "max_hdop_p95": 2.0,
            "max_cep95_m": 5.0,
            "max_integrity_errors": 0,
        }

    def test_built_in_default_equals_standard_profile_file(self):
        assert DEFAULT_PROFILE.thresholds == load_profile(PROFILES / "standard-gnss.yaml").thresholds

    def test_high_precision_is_stricter_on_every_threshold(self):
        std = load_profile(PROFILES / "standard-gnss.yaml").thresholds
        hp = load_profile(PROFILES / "high-precision.yaml").thresholds
        assert hp["min_availability_pct"] > std["min_availability_pct"]
        for key in ("max_ttff_s", "max_hdop_p95", "max_cep95_m"):
            assert hp[key] < std[key]
        assert hp["max_integrity_errors"] <= std["max_integrity_errors"]

    @pytest.mark.parametrize("name", ["standard-gnss.yaml", "high-precision.yaml"])
    def test_example_profiles_are_marked_illustrative(self, name):
        text = (PROFILES / name).read_text()
        assert text.startswith("# ILLUSTRATIVE EXAMPLE PROFILE.")
        assert "not taken from any product datasheet" in text
        assert load_profile(PROFILES / name).illustrative is True


# ---------------------------------------------------------------------- loading


class TestLoading:
    def test_valid_profile_loads(self, tmp_path):
        p = load_profile(write(tmp_path, VALID))
        assert (p.name, p.description, p.illustrative) == ("t", "d", True)
        assert p.source == "p.yaml"

    def test_null_threshold_disables_the_check(self, tmp_path):
        p = load_profile(write(tmp_path, VALID.replace("max_ttff_s: 60", "max_ttff_s: null")))
        assert p.thresholds["max_ttff_s"] is None

    @pytest.mark.parametrize(
        "text, message",
        [
            ("just a string", "mapping"),
            ("name: [unclosed", "YAML"),
            (VALID.replace("max_ttff_s: 60", "max_ttf_s: 60"), "max_ttf_s"),
            (VALID.replace("  max_ttff_s: 60\n", ""), "missing.*max_ttff_s"),
            (VALID.replace("max_ttff_s: 60", "max_ttff_s: soon"), "max_ttff_s.*number"),
            (VALID.replace("max_ttff_s: 60", "max_ttff_s: -1"), "max_ttff_s.*negative"),
            (VALID.replace("max_ttff_s: 60", "max_ttff_s: true"), "max_ttff_s.*number"),
            (VALID.replace("min_availability_pct: 95", "min_availability_pct: 101"), "100"),
            (VALID.replace("max_integrity_errors: 0", "max_integrity_errors: 1.5"), "whole number"),
            (VALID.replace("name: t\n", ""), "name"),
            (VALID + "colour: blue\n", "colour"),
            (VALID.replace("illustrative: true", "illustrative: maybe"), "illustrative"),
        ],
    )
    def test_invalid_profiles_are_rejected_with_a_clear_message(self, tmp_path, text, message):
        with pytest.raises(ProfileError, match=message):
            load_profile(write(tmp_path, text))

    def test_missing_file_is_a_profile_error(self, tmp_path):
        with pytest.raises(ProfileError, match="cannot read"):
            load_profile(tmp_path / "nope.yaml")

    def test_yaml_tags_are_not_executed(self, tmp_path):
        evil = VALID.replace("name: t", "name: !!python/object/apply:os.system ['echo hi']")
        with pytest.raises(ProfileError, match="YAML"):
            load_profile(write(tmp_path, evil))


class TestOverrides:
    def test_override_changes_value_and_is_recorded(self):
        p = with_overrides(DEFAULT_PROFILE, {"max_ttff_s": 30.0, "max_cep95_m": None})
        assert p.thresholds["max_ttff_s"] == 30.0
        assert p.thresholds["max_cep95_m"] is None
        assert p.overridden == {"max_ttff_s", "max_cep95_m"}
        assert DEFAULT_PROFILE.thresholds["max_ttff_s"] == 60.0  # original unchanged

    def test_unknown_override_key_is_rejected(self):
        with pytest.raises(ProfileError):
            with_overrides(DEFAULT_PROFILE, {"max_speed": 1.0})

    def test_overrides_are_validated_like_the_file(self):
        with pytest.raises(ProfileError, match="negative"):
            with_overrides(DEFAULT_PROFILE, {"max_hdop_p95": -2.0})

    def test_threshold_keys_are_the_five_criteria(self):
        assert THRESHOLD_KEYS == (
            "min_availability_pct", "max_ttff_s", "max_hdop_p95", "max_cep95_m",
            "max_integrity_errors",
        )  # fmt: skip


# ------------------------------------------------------------------- evaluation


def _evaluate(path: Path, **overrides):
    log = read_log(path)
    return evaluate(log, analyse(log), with_overrides(DEFAULT_PROFILE, overrides))


def by_key(results):
    return {r.key: r for r in results.criteria}


class TestEvaluate:
    def test_static_synthetic_log_passes_when_ttff_is_not_checked(self):
        result = _evaluate(DATA / "synthetic" / "SYNTHETIC-static-10min.log", max_ttff_s=None)
        assert result.verdict is Verdict.PASS
        assert by_key(result)["max_ttff_s"].outcome is Outcome.NOT_CHECKED

    def test_log_starting_with_fix_is_incomplete_because_ttff_is_not_measurable(self):
        result = _evaluate(DATA / "synthetic" / "SYNTHETIC-static-10min.log")
        assert result.verdict is Verdict.INCOMPLETE
        ttff = by_key(result)["max_ttff_s"]
        assert ttff.outcome is Outcome.NOT_MEASURABLE
        assert "starts with a fix" in ttff.note

    def test_cold_start_fails_on_availability_only(self):
        result = _evaluate(DATA / "synthetic" / "SYNTHETIC-cold-start.log")
        outcomes = {k: r.outcome for k, r in by_key(result).items()}
        assert outcomes == {
            "min_availability_pct": Outcome.FAIL,  # 75 % < 95 %
            "max_ttff_s": Outcome.PASS,  # 30 s <= 60 s
            "max_hdop_p95": Outcome.PASS,
            "max_cep95_m": Outcome.PASS,
            "max_integrity_errors": Outcome.PASS,
        }
        assert result.verdict is Verdict.FAIL

    def test_fail_beats_not_measurable(self):
        result = _evaluate(DATA / "real" / "unicore-um621.log")  # 80 %, TTFF not measurable
        assert result.verdict is Verdict.FAIL

    def test_values_exactly_at_threshold_pass(self):
        # Cold start: availability exactly 75 % and TTFF exactly 30 s.
        result = _evaluate(
            DATA / "synthetic" / "SYNTHETIC-cold-start.log",
            min_availability_pct=75.0, max_ttff_s=30.0,
        )  # fmt: skip
        assert by_key(result)["min_availability_pct"].outcome is Outcome.PASS
        assert by_key(result)["max_ttff_s"].outcome is Outcome.PASS

    def test_cep_and_hdop_not_measurable_without_fixes(self):
        body = "GPGGA,120000,,,,,0,00,,,M,,M,,"
        log = read_lines([f"${body}*{nmea_checksum(body)}"])
        assert len(log.epochs) == 1 and log.issues == []  # one valid no-fix epoch
        result = evaluate(log, analyse(log), DEFAULT_PROFILE)
        assert by_key(result)["max_cep95_m"].outcome is Outcome.NOT_MEASURABLE
        assert by_key(result)["max_hdop_p95"].outcome is Outcome.NOT_MEASURABLE

    def test_measured_value_and_threshold_are_recorded(self):
        result = _evaluate(DATA / "synthetic" / "SYNTHETIC-cold-start.log")
        availability = by_key(result)["min_availability_pct"]
        assert availability.measured == pytest.approx(75.0)
        assert availability.threshold == 95.0
        assert availability.comparison == ">="


class TestIntegrityCount:
    def test_unrecognised_sentence_types_are_not_integrity_errors(self):
        log = read_log(DATA / "real" / "unicore-um621.log")  # 220 unknown_type only
        assert integrity_error_count(log) == 0

    def test_other_problems_are_counted(self):
        unknown = f"$GPXYZ,1*{nmea_checksum('GPXYZ,1')}"  # valid sentence, unknown type
        lines = ["junk", "$GPGGA,1", unknown]
        assert integrity_error_count(read_lines(lines)) == 2
