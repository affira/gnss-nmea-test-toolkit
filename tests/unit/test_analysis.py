"""Unit tests for the log analyser (gnsskit.analysis).

Expected values are worked out by hand in each test, or (for real logs) taken from
grep/awk output and checked with Python's `statistics` module, independently of the
code under test.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from functools import reduce
from pathlib import Path

import pytest

from gnsskit.analysis import (
    EventKind,
    analyse,
    enu_offset_m,
    percentile,
)
from gnsskit.log import LogReadResult, read_lines, read_log

DATA = Path(__file__).resolve().parents[2] / "data" / "real"


def cs(body: str) -> str:
    checksum = reduce(lambda acc, ch: acc ^ ord(ch), body, 0)
    return f"${body}*{checksum:02X}"


def hhmmss(seconds: float) -> str:
    """Seconds of day -> hhmmss[.cc], rounded to centiseconds first."""
    whole, centi = divmod(round(seconds * 100), 100)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    text = f"{h:02d}{m:02d}{s:02d}"
    return text + (f".{centi:02d}" if centi else "")


def gga(t: float, quality: int = 1, *, sats: int = 8, hdop: float = 1.0,
        lat: str = "4404.12994", lon: str = "12118.84613") -> str:  # fmt: skip
    return cs(f"GPGGA,{hhmmss(t)},{lat},N,{lon},W,{quality},{sats:02d},{hdop},100.0,M,,M,,")


def read(lines: list[str]) -> LogReadResult:
    return read_lines(lines)


def fix_pattern(pattern: str, start: float = 43200.0) -> list[str]:
    """'F' = fix epoch, '.' = no-fix epoch, ' ' = missing second (no data). 1 Hz."""
    return [
        gga(start + i, 1 if ch == "F" else 0)
        for i, ch in enumerate(pattern)
        if ch != " "
    ]


# ------------------------------------------------------------------------ helpers


class TestPercentile:
    def test_linear_interpolation_between_ranks(self):
        values = [1.0, 2.0, 3.0, 4.0]
        assert percentile(values, 50) == pytest.approx(2.5)
        assert percentile(values, 95) == pytest.approx(3.85)  # rank 2.85 -> 3 + 0.85
        assert percentile(values, 0) == 1.0
        assert percentile(values, 100) == 4.0

    def test_order_of_input_does_not_matter(self):
        assert percentile([4.0, 1.0, 3.0, 2.0], 50) == pytest.approx(2.5)

    def test_single_value(self):
        assert percentile([7.0], 95) == 7.0

    def test_matches_statistics_inclusive_method(self):
        values = [0.3, 1.7, 2.2, 5.0, 5.1, 8.8, 9.0]
        expected = statistics.quantiles(values, n=100, method="inclusive")
        for p in (5, 25, 50, 75, 95):
            assert percentile(values, p) == pytest.approx(expected[p - 1])

    def test_empty_raises_value_error(self):
        with pytest.raises(ValueError):
            percentile([], 50)


class TestEnuOffset:
    # Published WGS84 values: 1 degree of longitude at the equator is a*pi/180
    # = 111 319.49 m; 1 degree of latitude at the equator is 110 574.3 m.
    def test_one_degree_east_at_equator(self):
        east, north = enu_offset_m(0.0, 1.0, ref_lat=0.0, ref_lon=0.0)
        assert east == pytest.approx(111_319.49, abs=0.5)
        assert north == pytest.approx(0.0, abs=1e-9)

    def test_small_north_offset_at_equator(self):
        east, north = enu_offset_m(0.001, 0.0, ref_lat=0.0, ref_lon=0.0)
        assert north == pytest.approx(110.5743, abs=0.001)
        assert east == pytest.approx(0.0, abs=1e-9)

    def test_east_distance_shrinks_with_cos_latitude(self):
        east_eq, _ = enu_offset_m(0.0, 0.001, ref_lat=0.0, ref_lon=0.0)
        east_60, _ = enu_offset_m(60.0, 0.001, ref_lat=60.0, ref_lon=0.0)
        # Ratio is cos(60) times N(60)/N(0) = 1/sqrt(1 - e^2 * 0.75) = 1.00252 (WGS84).
        assert east_60 / east_eq == pytest.approx(0.5 * 1.00252, rel=1e-5)

    def test_south_and_west_are_negative(self):
        east, north = enu_offset_m(-0.001, -0.001, ref_lat=0.0, ref_lon=0.0)
        assert east < 0 and north < 0


# ------------------------------------------------------------------ fix definition


class TestFixDefinition:
    @pytest.mark.parametrize("quality, is_fix", [(0, False), (1, True), (2, True), (3, True),
                                                 (4, True), (5, True), (6, False), (7, False),
                                                 (8, False)])  # fmt: skip
    def test_gga_quality_codes(self, quality, is_fix):
        result = analyse(read([gga(43200, quality)]))
        assert result.fix_epochs == (1 if is_fix else 0)

    @pytest.mark.parametrize(
        "status, mode, is_fix",
        [("A", "A", True), ("A", "D", True), ("A", "", True), ("V", "N", False),
         ("A", "E", False), ("A", "N", False)],
    )  # fmt: skip
    def test_rmc_only_epoch_uses_status_and_mode(self, status, mode, is_fix):
        line = cs(f"GPRMC,120000,{status},4404.12994,N,12118.84613,W,0.0,,090624,,,{mode}")
        result = analyse(read([line]))
        assert result.fix_epochs == (1 if is_fix else 0)


# ------------------------------------------------------------- availability & TTFF


class TestAvailability:
    def test_availability_is_fix_epochs_over_expected_epochs(self):
        result = analyse(read(fix_pattern("FFF...FFFF")))
        assert result.epochs_expected == 10
        assert result.fix_epochs == 7
        assert result.availability_pct == pytest.approx(70.0)

    def test_missing_seconds_count_as_expected_but_not_fixed(self):
        result = analyse(read(fix_pattern("FFF  FFFFF")))
        assert result.epoch_interval_s == 1.0
        assert result.epochs_present == 8
        assert result.epochs_expected == 10
        assert result.availability_pct == pytest.approx(80.0)

    def test_interval_is_median_step_so_5hz_logs_work(self):
        lines = [gga(43200 + i * 0.2) for i in range(10)]
        result = analyse(read(lines))
        assert result.epoch_interval_s == pytest.approx(0.2)
        assert result.epochs_expected == 10
        assert result.duration_s == pytest.approx(1.8)

    def test_empty_log_does_not_crash(self):
        result = analyse(read([]))
        assert result.epochs_present == 0
        assert result.availability_pct is None
        assert result.ttff_s is None
        assert result.hdop is None and result.accuracy is None
        assert result.events == ()


class TestTtff:
    def test_ttff_from_first_epoch_to_first_fix(self):
        result = analyse(read(fix_pattern(".....FFF")))
        assert result.ttff_s == pytest.approx(5.0)
        assert result.ttff_note is None

    def test_log_starting_with_fix_has_no_measurable_ttff(self):
        result = analyse(read(fix_pattern("FFF")))
        assert result.ttff_s is None
        assert "starts with a fix" in result.ttff_note

    def test_log_without_fix_has_no_ttff(self):
        result = analyse(read(fix_pattern("....")))
        assert result.ttff_s is None
        assert "no fix" in result.ttff_note

    def test_untimed_epochs_before_first_fix_are_noted(self):
        untimed = cs("GPGGA,,,,,,0,,,,,,,,")
        result = analyse(read([untimed, untimed] + fix_pattern("..F")))
        assert result.ttff_s == pytest.approx(2.0)
        assert "2 epochs without time" in result.ttff_note

    def test_ttff_across_midnight(self):
        lines = [gga(86398, 0), gga(86399, 0), gga(0, 0), gga(1, 1)]
        assert analyse(read(lines)).ttff_s == pytest.approx(3.0)


# ------------------------------------------------------------------------- events


class TestEvents:
    def test_single_outage_gives_one_lost_and_one_regained(self):
        result = analyse(read(fix_pattern("FF...FF", start=43200)))
        lost, regained = result.events
        assert lost.kind is EventKind.FIX_LOST
        assert lost.time == dt.time(12, 0, 2)
        assert regained.kind is EventKind.FIX_REGAINED
        assert regained.time == dt.time(12, 0, 5)
        assert regained.duration_s == pytest.approx(3.0)
        assert lost.duration_s == pytest.approx(3.0)
        assert result.fix_lost_count == 1
        assert result.longest_outage_s == pytest.approx(3.0)

    def test_two_outages(self):
        result = analyse(read(fix_pattern("F.FF..F")))
        assert [e.kind for e in result.events] == [
            EventKind.FIX_LOST, EventKind.FIX_REGAINED, EventKind.FIX_LOST, EventKind.FIX_REGAINED,
        ]  # fmt: skip
        assert [e.duration_s for e in result.events if e.kind is EventKind.FIX_REGAINED] == [1.0, 2.0]
        assert result.longest_outage_s == pytest.approx(2.0)

    def test_outage_still_ongoing_at_end_of_log(self):
        result = analyse(read(fix_pattern("FF...")))
        (lost,) = result.events
        assert lost.kind is EventKind.FIX_LOST
        assert lost.duration_s is None
        assert result.longest_outage_s is None

    def test_cold_start_is_not_a_fix_loss(self):
        result = analyse(read(fix_pattern("...FFF")))
        assert result.events == ()
        assert result.fix_lost_count == 0

    def test_missing_seconds_while_fixed_is_a_data_gap_not_a_fix_loss(self):
        result = analyse(read(fix_pattern("FF   FF")))
        (gap,) = result.events
        assert gap.kind is EventKind.DATA_GAP
        assert gap.time == dt.time(12, 0, 1)  # last epoch before the gap
        assert gap.duration_s == pytest.approx(3.0)  # three missing seconds
        assert result.fix_lost_count == 0

    def test_fix_lost_during_a_data_gap(self):
        result = analyse(read(fix_pattern("FF  ..F")))
        assert [e.kind for e in result.events] == [
            EventKind.DATA_GAP, EventKind.FIX_LOST, EventKind.FIX_REGAINED,
        ]  # fmt: skip

    def test_events_carry_log_line_numbers(self):
        result = analyse(read(["# header"] + fix_pattern("F.F")))
        assert [e.line_no for e in result.events] == [3, 4]


# --------------------------------------------------------------- satellites & DOP


class TestSatellitesAndHdop:
    def test_hdop_statistics_use_fix_epochs_only(self):
        lines = [gga(43200, 1, hdop=1.0), gga(43201, 1, hdop=2.0), gga(43202, 0, hdop=99.99),
                 gga(43203, 1, hdop=3.0), gga(43204, 1, hdop=4.0)]  # fmt: skip
        hdop = analyse(read(lines)).hdop
        assert hdop.n == 4
        assert (hdop.min, hdop.max) == (1.0, 4.0)
        assert hdop.mean == pytest.approx(2.5)
        assert hdop.median == pytest.approx(2.5)
        assert hdop.p95 == pytest.approx(3.85)

    def test_gga_satellite_count_statistics_and_series(self):
        lines = [gga(43200, 1, sats=6), gga(43201, 0, sats=0), gga(43202, 1, sats=10)]
        result = analyse(read(lines))
        assert result.sats_used_gga.n == 2
        assert result.sats_used_gga.mean == pytest.approx(8.0)
        assert result.sats_used_series == ((43200.0, 6, None), (43201.0, 0, None), (43202.0, 10, None))

    def test_gsa_satellites_by_constellation_using_system_id(self):
        lines = [
            gga(43200),
            cs("GNGSA,A,3,01,02,03,,,,,,,,,,1.5,0.9,1.2,1"),
            cs("GNGSA,A,3,65,66,,,,,,,,,,,1.5,0.9,1.2,2"),
            cs("GNGSA,A,3,,,,,,,,,,,,,1.5,0.9,1.2,3"),
            gga(43201),
            cs("GNGSA,A,3,01,02,03,04,05,,,,,,,,1.5,0.9,1.2,1"),
            cs("GNGSA,A,3,201,202,203,,,,,,,,,,1.5,0.9,1.2,4"),
        ]
        result = analyse(read(lines))
        by = result.sats_used_by_constellation
        assert by["GPS"].mean == pytest.approx(4.0)  # 3 then 5
        assert by["GLONASS"].mean == pytest.approx(1.0)  # 2 then 0
        assert by["Galileo"].max == 0
        assert by["BeiDou"].mean == pytest.approx(1.5)  # 0 then 3
        assert result.sats_used_gsa.mean == pytest.approx(6.5)  # 5 then 8
        assert [s[2] for s in result.sats_used_series] == [5, 8]

    def test_gsa_without_system_id_uses_talker(self):
        lines = [gga(43200), cs("GPGSA,A,3,01,02,,,,,,,,,,,1.5,0.9,1.2"),
                 cs("GLGSA,A,3,65,,,,,,,,,,,,1.5,0.9,1.2")]  # fmt: skip
        by = analyse(read(lines)).sats_used_by_constellation
        assert (by["GPS"].mean, by["GLONASS"].mean) == (2.0, 1.0)

    def test_unknown_gsa_system_id_is_kept_under_its_number(self):
        lines = [gga(43200), cs("GNGSA,A,3,01,,,,,,,,,,,,1.5,0.9,1.2,9")]
        assert set(analyse(read(lines)).sats_used_by_constellation) == {"System 9"}

    def test_same_satellite_listed_twice_is_counted_once(self):
        # Some receivers repeat the PRN in a second GSA (e.g. one per signal).
        lines = [gga(43200), cs("GNGSA,A,3,01,02,,,,,,,,,,,1.5,0.9,1.2,1"),
                 cs("GNGSA,A,3,01,02,,,,,,,,,,,1.5,0.9,1.2,1")]  # fmt: skip
        assert analyse(read(lines)).sats_used_by_constellation["GPS"].mean == 2.0


# ---------------------------------------------------------------------- accuracy


def equator_gga(t: float, east_m: float) -> str:
    """Fix on the equator, `east_m` metres east of 0 deg longitude."""
    lon_deg = east_m / 111_319.4908  # WGS84: a * pi / 180 metres per degree at the equator
    minutes = lon_deg * 60
    return cs(f"GPGGA,{hhmmss(t)},0000.00000,N,000{minutes:08.5f},E,1,08,1.0,0.0,M,,M,,")


class TestAccuracy:
    def test_cep_against_given_reference(self):
        lines = [equator_gga(43200 + i, east_m=float(d)) for i, d in enumerate(range(1, 11))]
        acc = analyse(read(lines), reference=(0.0, 0.0)).accuracy
        assert acc.reference_kind == "given"
        assert acc.n == 10
        # distances 1..10 m (to 5 decimal places of a minute, ~2 cm resolution)
        assert acc.cep50_m == pytest.approx(5.5, abs=0.05)
        assert acc.cep95_m == pytest.approx(9.55, abs=0.05)
        assert acc.max_m == pytest.approx(10.0, abs=0.05)

    def test_cep_against_mean_position(self):
        offsets = [-3.0, -1.0, 1.0, 3.0]
        lines = [equator_gga(43200 + i, east_m=d + 100) for i, d in enumerate(offsets)]
        acc = analyse(read(lines)).accuracy
        assert acc.reference_kind == "mean"
        assert acc.reference_lat == pytest.approx(0.0, abs=1e-9)
        assert acc.reference_lon * 111_319.4908 == pytest.approx(100.0, abs=0.05)
        assert acc.cep50_m == pytest.approx(2.0, abs=0.05)  # distances 1,1,3,3

    def test_no_fix_positions_are_excluded(self):
        lines = [equator_gga(43200, 1.0), gga(43201, 0)]
        assert analyse(read(lines), reference=(0.0, 0.0)).accuracy.n == 1

    def test_no_positions_means_no_accuracy(self):
        assert analyse(read(fix_pattern("...."))).accuracy is None


# ---------------------------------------------------------------------- real logs


def test_real_unicore_log_outage():
    result = analyse(read_log(DATA / "unicore-um621.log"))
    assert result.epochs_expected == 20
    assert result.fix_epochs == 16
    assert result.availability_pct == pytest.approx(80.0)
    assert result.ttff_s is None  # log starts with a fix
    lost, regained = result.events
    assert (lost.kind, lost.time) == (EventKind.FIX_LOST, dt.time(0, 7, 6))
    assert (regained.kind, regained.time) == (EventKind.FIX_REGAINED, dt.time(0, 7, 10))
    assert regained.duration_s == pytest.approx(4.0)
    assert lost.utc == dt.datetime(2024, 6, 9, 0, 7, 6, tzinfo=dt.timezone.utc)


def test_real_unicore_log_hdop_matches_independent_statistics():
    # HDOP of the 16 fix epochs, extracted with awk.
    values = [0.69, 0.63, 0.60, 0.62, 0.62, 0.60, 0.62, 0.64, 0.70, 0.83, 1.99, 2.92,
              10.16, 2.62, 0.74, 0.67]  # fmt: skip
    hdop = analyse(read_log(DATA / "unicore-um621.log")).hdop
    assert hdop.n == 16
    assert hdop.min == 0.60 and hdop.max == 10.16
    assert hdop.mean == pytest.approx(statistics.fmean(values))
    assert hdop.median == pytest.approx(statistics.median(values))
    assert hdop.p95 == pytest.approx(statistics.quantiles(values, n=100, method="inclusive")[94])


@pytest.mark.parametrize("log, n", [("ublox-zed-f9p-nmea.log", 29), ("ublox-neo-m9n-nmea.log", 61)])
def test_real_ublox_logs_full_availability_and_no_events(log, n):
    result = analyse(read_log(DATA / log))
    assert result.fix_epochs == n
    assert result.availability_pct == pytest.approx(100.0)
    assert result.events == ()


@pytest.mark.parametrize("log", ["ublox-zed-f9p-nmea.log", "ublox-neo-m9n-nmea.log"])
def test_real_ublox_gga_satellite_count_is_capped_below_gsa(log):
    # Both u-blox logs report 12 satellites in every GGA but list 18-24 in GSA (awk).
    result = analyse(read_log(DATA / log))
    assert result.sats_used_gga.max == 12
    assert result.sats_used_gsa.min >= 18
    assert set(result.sats_used_by_constellation) == {"GPS", "GLONASS", "Galileo", "BeiDou"}


def test_real_zed_f9p_static_log_cep():
    # Independent check (step 0): all fixes lie within 0.14 m of the mean.
    acc = analyse(read_log(DATA / "ublox-zed-f9p-nmea.log")).accuracy
    assert acc.n == 29
    assert 0 < acc.cep50_m <= acc.cep95_m <= acc.max_m <= 0.15
    assert acc.reference_lat == pytest.approx(-(45 + 52.654 / 60), abs=1e-5)
    assert math.isfinite(acc.cep95_m)
