"""Tests for the synthetic log generator and the data provenance rules.

Synthetic logs must be (1) reproducible byte for byte from the script, (2) clearly
labelled, (3) listed in data/SOURCES.md, and (4) analysed to the values the
generator built in. Real logs must still match the SHA-256 recorded in SOURCES.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from pathlib import Path

import pytest

from gnsskit.analysis import analyse
from gnsskit.log import read_lines, read_log
from gnsskit.synthetic import SCENARIOS, TRUTH_LAT, TRUTH_LON, generate

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "data" / "synthetic"
SOURCES = (ROOT / "data" / "SOURCES.md").read_text()

# Circular Gaussian with sigma per axis: CEP50 = sqrt(2 ln 2) sigma, CEP95 = sqrt(2 ln 20) sigma.
CEP50_FACTOR = math.sqrt(2 * math.log(2))  # 1.1774
CEP95_FACTOR = math.sqrt(2 * math.log(20))  # 2.4477


# ------------------------------------------------------------------- provenance


@pytest.mark.parametrize("name", SCENARIOS)
def test_committed_file_matches_generator_output(name):
    path = SYNTHETIC / SCENARIOS[name].filename
    assert path.read_text() == "\n".join(generate(SCENARIOS[name])) + "\n"


@pytest.mark.parametrize("name", SCENARIOS)
def test_synthetic_file_is_labelled_in_name_header_and_sources(name):
    scenario = SCENARIOS[name]
    assert scenario.filename.startswith("SYNTHETIC-")
    first = generate(scenario)[0]
    assert first.startswith("# SYNTHETIC")
    assert "not a real recording" in first.lower()
    assert f"`{scenario.filename}`" in SOURCES


def test_no_unlisted_files_in_data_dirs():
    for path in list((ROOT / "data" / "real").glob("*.log")) + list(SYNTHETIC.glob("*")):
        assert f"`{path.name}`" in SOURCES, f"{path.name} is not listed in data/SOURCES.md"


def test_real_logs_match_recorded_sha256():
    rows = re.findall(r"\| `([^`]+\.log)` \|.*\| `([0-9a-f]{64})` \|", SOURCES)
    assert len(rows) == 3
    for name, digest in rows:
        assert hashlib.sha256((ROOT / "data" / "real" / name).read_bytes()).hexdigest() == digest


def test_generation_is_deterministic():
    scenario = SCENARIOS["static-10min"]
    assert generate(scenario) == generate(scenario)


# -------------------------------------------------------------- built-in truth


def test_cold_start_has_30_second_ttff_and_no_losses():
    scenario = SCENARIOS["cold-start"]
    result = analyse(read_log(SYNTHETIC / scenario.filename))
    assert scenario.no_fix_epochs == 30
    assert result.ttff_s == pytest.approx(30.0)
    assert result.ttff_note is None
    assert result.availability_pct == pytest.approx(100 * 90 / 120)
    assert result.events == ()


def test_static_log_cep_matches_gaussian_theory():
    # n = 600: the sampling standard deviation of both CEP estimates is about 3 %,
    # so a 10 % tolerance is more than 3 standard deviations.
    scenario = SCENARIOS["static-10min"]
    log = read_log(SYNTHETIC / scenario.filename)
    assert log.issues == []
    acc = analyse(log, reference=(TRUTH_LAT, TRUTH_LON)).accuracy
    assert acc.n == 600
    assert acc.cep50_m == pytest.approx(CEP50_FACTOR * scenario.sigma_m, rel=0.10)
    assert acc.cep95_m == pytest.approx(CEP95_FACTOR * scenario.sigma_m, rel=0.10)


def test_static_log_mean_position_is_close_to_truth():
    # Standard error of the mean per axis: sigma / sqrt(600) = 0.04 m.
    acc = analyse(read_log(SYNTHETIC / SCENARIOS["static-10min"].filename)).accuracy
    north_m = (acc.reference_lat - TRUTH_LAT) * 111_250
    east_m = (acc.reference_lon - TRUTH_LON) * 111_250 * math.cos(math.radians(TRUTH_LAT))
    assert math.hypot(north_m, east_m) < 0.2


def test_midnight_log_rolls_over_into_new_year():
    log = read_log(SYNTHETIC / SCENARIOS["midnight-rollover"].filename)
    assert log.issues == []
    assert log.rollovers == 1
    assert len(log.epochs) == 121
    assert log.epochs[0].utc == dt.datetime(2025, 12, 31, 23, 59, 0, tzinfo=dt.timezone.utc)
    assert log.epochs[60].utc == dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    assert {b.seconds - a.seconds for a, b in zip(log.epochs, log.epochs[1:])} == {1.0}


@pytest.mark.parametrize("name", SCENARIOS)
def test_satellites_used_match_the_generated_sky(name):
    scenario = SCENARIOS[name]
    result = analyse(read_lines(generate(scenario)))
    by = result.sats_used_by_constellation
    assert {k: v.max for k, v in by.items()} == {"GPS": 7, "GLONASS": 4, "Galileo": 4, "BeiDou": 4}
    assert result.sats_used_gga.max == 19
    assert result.sats_used_gsa.min == 19


def test_western_hemisphere_near_prime_meridian_is_formatted_correctly():
    gga = next(line for line in generate(SCENARIOS["static-10min"]) if "GGA" in line)
    fields = gga.split(",")
    assert re.fullmatch(r"000\d\d\.\d{5}", fields[4])  # dddmm.mmmmm
    assert fields[5] in ("E", "W")
