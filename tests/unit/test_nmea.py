"""Unit tests for the NMEA 0183 sentence parser (gnsskit.nmea).

Real sentences are copied verbatim from the gpsd logs in data/real/ (BSD-2-Clause,
see data/SOURCES.md). Hand-built sentences use `cs()`, which computes the checksum
independently of the code under test.
"""

from __future__ import annotations

import datetime as dt
import random
from functools import reduce
from pathlib import Path

import pytest

from gnsskit.nmea import (
    GGA,
    GSA,
    GSV,
    RMC,
    ErrorKind,
    OtherSentence,
    ParseError,
    SatelliteInView,
    constellation_name,
    nmea_checksum,
    parse_sentence,
)

DATA = Path(__file__).resolve().parents[2] / "data" / "real"


def cs(body: str) -> str:
    """Wrap a sentence body (no '$', no '*') with a correct checksum."""
    checksum = reduce(lambda acc, ch: acc ^ ord(ch), body, 0)
    return f"${body}*{checksum:02X}"


def expect_error(line: str, kind: ErrorKind) -> ParseError:
    result = parse_sentence(line)
    assert isinstance(result, ParseError), f"expected {kind}, got {result!r}"
    assert result.kind is kind, f"expected {kind}, got {result.kind}: {result.message}"
    assert result.line == line
    return result


# --------------------------------------------------------------------------- checksum


class TestChecksum:
    @pytest.mark.parametrize(
        "line",
        [
            "$GNGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,*71",
            "$GNRMC,003956.00,A,4552.65403,S,17030.00668,E,0.025,,120419,,,A,V*07",
            "$GPGSV,4,4,16,29,46,133,43,44,45,171,45,46,45,191,43,51,43,156,43,1*60",
            "$GNGSA,A,3,,,,,,,,,,,,,4.89,2.92,3.92,3*07",
        ],
    )
    def test_checksum_matches_real_receiver_output(self, line):
        body, checksum = line[1:].split("*")
        assert nmea_checksum(body) == checksum

    def test_checksum_is_two_uppercase_hex_digits(self):
        assert nmea_checksum("") == "00"
        assert nmea_checksum("A") == "41"

    def test_lowercase_checksum_digits_are_accepted(self):
        # Real line ends in *0B; receivers may legally emit lowercase hex.
        line = "$GNGSA,A,3,,,,,,,,,,,,,11.97,10.16,6.34,2*0b"
        assert isinstance(parse_sentence(line), GSA)

    def test_wrong_checksum_is_reported_with_expected_and_found_values(self):
        err = expect_error(
            "$GNGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,*72",
            ErrorKind.BAD_CHECKSUM,
        )
        assert "71" in err.message and "72" in err.message

    def test_non_hex_checksum_is_bad_checksum(self):
        expect_error("$GNGSA,A,3,,,,,,,,,,,,,4.89,2.92,3.92,3*ZZ", ErrorKind.BAD_CHECKSUM)

    def test_extra_characters_after_checksum_is_bad_checksum(self):
        expect_error("$GNGSA,A,3,,,,,,,,,,,,,4.89,2.92,3.92,3*07X", ErrorKind.BAD_CHECKSUM)

    def test_surrounding_whitespace_and_crlf_are_ignored(self):
        line = "  $GNGSA,A,3,,,,,,,,,,,,,4.89,2.92,3.92,3*07\r\n"
        assert isinstance(parse_sentence(line), GSA)


# ------------------------------------------------------------------ framing / bad input


class TestFraming:
    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            "GNGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,*71",
            '{"class":"VERSION","release":"3.23.2"}',
            "# comment line",
        ],
    )
    def test_lines_that_are_not_nmea_are_non_nmea(self, line):
        expect_error(line, ErrorKind.NON_NMEA)

    def test_non_ascii_character_is_non_nmea(self):
        expect_error("$GNGSA,A,3,,,,�,,,,,,,,4.89,2.92,3.92,3*07", ErrorKind.NON_NMEA)

    def test_control_character_is_non_nmea(self):
        expect_error("$GNGSA,A,3,,,,\x00,,,,,,,,4.89,2.92,3.92,3*07", ErrorKind.NON_NMEA)

    @pytest.mark.parametrize(
        "line",
        [
            "$GNGGA,000653.00,4404.12994,N,121",  # cut mid-field
            "$GNGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,",  # no '*'
            "$GNGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,*",  # no digits
            "$GNGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,*7",  # 1 digit
            "$",
        ],
    )
    def test_truncated_lines_are_truncated(self, line):
        expect_error(line, ErrorKind.TRUNCATED)

    def test_line_with_second_sentence_start_is_truncated(self):
        # A receiver buffer overrun: a cut-off sentence runs into the next one.
        line = "$GNGGA,000653.00,4404.1$GNGSA,A,3,,,,,,,,,,,,,4.89,2.92,3.92,3*07"
        expect_error(line, ErrorKind.TRUNCATED)


class TestAddress:
    @pytest.mark.parametrize(
        "line",
        [
            # Verbatim from unicore-um621.log: invalid addresses per the log's own header.
            "$SNRSTAT,1,1,0,0*5D",
            cs("GYOACC,1,2,3"),
            cs("GP,1,2"),
            cs("gpgga,1"),
            cs("GPGG1,1"),
        ],
    )
    def test_invalid_address_is_unknown_type(self, line):
        expect_error(line, ErrorKind.UNKNOWN_TYPE)

    def test_unknown_standard_formatter_is_unknown_type(self):
        expect_error(cs("GPXYZ,1,2,3"), ErrorKind.UNKNOWN_TYPE)

    @pytest.mark.parametrize(
        "line, talker, formatter",
        [
            ("$GNTXT,01,01,02,0,00,10000,00,01,17,01,0001,0000,0.000*57", "GN", "TXT"),
            (cs("GNVTG,,T,,M,0.025,N,0.046,K,A"), "GN", "VTG"),
            (cs("GNZDA,003956.00,12,04,2019,00,00"), "GN", "ZDA"),
        ],
    )
    def test_known_but_unsupported_standard_sentence_is_other(self, line, talker, formatter):
        result = parse_sentence(line)
        assert result == OtherSentence(talker=talker, formatter=formatter, proprietary=False)

    def test_proprietary_sentence_is_other(self):
        line = (
            "$PDTINFO,UM621-02,G1B1L1E1,V1.2,R6.0.0.0Build2810,"
            "2310414000033,PC12B4231700429*73"
        )
        result = parse_sentence(line)
        assert result == OtherSentence(talker=None, formatter="PDTINFO", proprietary=True)


class TestConstellation:
    @pytest.mark.parametrize(
        "talker, name",
        [
            ("GP", "GPS"),
            ("GL", "GLONASS"),
            ("GA", "Galileo"),
            ("GB", "BeiDou"),
            ("BD", "BeiDou"),
            ("GQ", "QZSS"),
            ("GI", "NavIC"),
            ("GN", "Multi-GNSS"),
            ("XX", "Unknown"),
        ],
    )
    def test_talker_maps_to_constellation(self, talker, name):
        assert constellation_name(talker) == name

    def test_unknown_talker_is_still_parsed(self):
        result = parse_sentence(cs("XXGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99"))
        assert isinstance(result, GSA)
        assert result.talker == "XX"


# ------------------------------------------------------------------------------- GGA


class TestGGA:
    def test_parses_real_fix(self):
        result = parse_sentence(
            "$GNGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,*71"
        )
        assert isinstance(result, GGA)
        assert result.talker == "GN"
        assert result.time == dt.time(0, 6, 53)
        assert result.lat == pytest.approx(44 + 4.12994 / 60, abs=1e-9)
        assert result.lon == pytest.approx(-(121 + 18.84613 / 60), abs=1e-9)
        assert result.quality == 1
        assert result.num_sats == 29
        assert result.hdop == pytest.approx(0.69)
        assert result.altitude_m == pytest.approx(1109.2)
        assert result.geoid_sep_m is None

    def test_parses_southern_eastern_hemisphere_and_geoid_separation(self):
        result = parse_sentence(
            "$GNGGA,003956.00,4552.65403,S,17030.00668,E,1,12,0.64,14.2,M,1.8,M,,*56"
        )
        assert isinstance(result, GGA)
        assert result.lat == pytest.approx(-(45 + 52.65403 / 60), abs=1e-9)
        assert result.lon == pytest.approx(170 + 30.00668 / 60, abs=1e-9)
        assert result.geoid_sep_m == pytest.approx(1.8)

    def test_parses_dgps_quality_and_station_field(self):
        result = parse_sentence(
            "$GNGGA,223745.00,3806.62964,N,12237.61382,W,2,12,0.54,83.1,M,-29.5,M,,0000*44"
        )
        assert isinstance(result, GGA)
        assert result.quality == 2
        assert result.geoid_sep_m == pytest.approx(-29.5)

    def test_parses_real_no_fix_epoch_with_stale_position(self):
        # unicore-um621.log: the receiver keeps reporting its last position with quality 0.
        result = parse_sentence(
            "$GNGGA,000706.00,4404.12938,N,12118.84590,W,0,00,99.99,1108.8,M,,M,,*40"
        )
        assert isinstance(result, GGA)
        assert result.quality == 0
        assert result.num_sats == 0
        assert result.hdop == pytest.approx(99.99)

    def test_no_fix_with_all_empty_fields_is_valid(self):
        result = parse_sentence(cs("GPGGA,,,,,,0,,,,,,,,"))
        assert isinstance(result, GGA)
        assert result.time is None and result.lat is None and result.lon is None
        assert result.quality == 0
        assert result.num_sats is None and result.hdop is None

    def test_fractional_seconds_are_kept(self):
        result = parse_sentence(cs("GPGGA,235959.25,,,,,0,,,,,,,,"))
        assert result.time == dt.time(23, 59, 59, 250000)

    def test_too_few_fields_is_field_count(self):
        expect_error(cs("GPGGA,000653.00,4404.12994,N,12118.84613,W,1"), ErrorKind.FIELD_COUNT)

    def test_empty_quality_is_empty_field(self):
        err = expect_error(
            cs("GPGGA,000653.00,4404.12994,N,12118.84613,W,,29,0.69,1109.2,M,,M,,"),
            ErrorKind.EMPTY_FIELD,
        )
        assert err.field == "quality"

    @pytest.mark.parametrize(
        "body, field",
        [
            ("GPGGA,,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "time"),
            ("GPGGA,000653.00,,,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "lat"),
            ("GPGGA,000653.00,4404.12994,N,,,1,29,0.69,1109.2,M,,M,,", "lon"),
        ],
    )
    def test_fix_with_missing_time_or_position_is_empty_field(self, body, field):
        err = expect_error(cs(body), ErrorKind.EMPTY_FIELD)
        assert err.field == field

    def test_latitude_without_hemisphere_is_empty_field(self):
        err = expect_error(
            cs("GPGGA,000653.00,4404.12994,,12118.84613,W,1,29,0.69,1109.2,M,,M,,"),
            ErrorKind.EMPTY_FIELD,
        )
        assert err.field == "lat_hemisphere"

    @pytest.mark.parametrize(
        "body, field",
        [
            ("GPGGA,250653.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "time"),
            ("GPGGA,006053.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "time"),
            ("GPGGA,0006ab.00,4404.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "time"),
            ("GPGGA,000653.00,4460.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "lat"),
            ("GPGGA,000653.00,4460.00000,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "lat"),
            ("GPGGA,000653.00,9104.12994,N,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "lat"),
            ("GPGGA,000653.00,4404.12994,X,12118.84613,W,1,29,0.69,1109.2,M,,M,,", "lat_hemisphere"),
            ("GPGGA,000653.00,4404.12994,N,18118.84613,W,1,29,0.69,1109.2,M,,M,,", "lon"),
            ("GPGGA,000653.00,4404.12994,N,12118.84613,N,1,29,0.69,1109.2,M,,M,,", "lon_hemisphere"),
            ("GPGGA,000653.00,4404.12994,N,12118.84613,W,9,29,0.69,1109.2,M,,M,,", "quality"),
            ("GPGGA,000653.00,4404.12994,N,12118.84613,W,1,2x,0.69,1109.2,M,,M,,", "num_sats"),
            ("GPGGA,000653.00,4404.12994,N,12118.84613,W,1,29,nan,1109.2,M,,M,,", "hdop"),
            ("GPGGA,000653.00,4404.12994,N,12118.84613,W,1,29,0.69,inf,M,,M,,", "altitude_m"),
        ],
    )
    def test_invalid_values_are_malformed_field(self, body, field):
        err = expect_error(cs(body), ErrorKind.MALFORMED_FIELD)
        assert err.field == field


# ------------------------------------------------------------------------------- RMC


class TestRMC:
    def test_parses_real_valid_rmc(self):
        result = parse_sentence(
            "$GNRMC,003956.00,A,4552.65403,S,17030.00668,E,0.025,,120419,,,A,V*07"
        )
        assert isinstance(result, RMC)
        assert result.talker == "GN"
        assert result.time == dt.time(0, 39, 56)
        assert result.status == "A"
        assert result.valid is True
        assert result.lat == pytest.approx(-(45 + 52.65403 / 60), abs=1e-9)
        assert result.lon == pytest.approx(170 + 30.00668 / 60, abs=1e-9)
        assert result.speed_knots == pytest.approx(0.025)
        assert result.course_deg is None
        assert result.date == dt.date(2019, 4, 12)
        assert result.mode == "A"

    def test_parses_real_void_rmc(self):
        result = parse_sentence(
            "$GNRMC,000706.00,V,4404.12938,N,12118.84590,W,,,090624,,,N,V*36"
        )
        assert isinstance(result, RMC)
        assert result.valid is False
        assert result.date == dt.date(2024, 6, 9)
        assert result.speed_knots is None
        assert result.mode == "N"

    def test_parses_nmea_2_0_rmc_without_mode_field(self):
        result = parse_sentence(cs("GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W"))
        assert isinstance(result, RMC)
        assert result.date == dt.date(1994, 3, 23)
        assert result.course_deg == pytest.approx(84.4)
        assert result.mode is None

    def test_too_few_fields_is_field_count(self):
        expect_error(cs("GPRMC,123519,A,4807.038,N"), ErrorKind.FIELD_COUNT)

    def test_empty_status_is_empty_field(self):
        err = expect_error(cs("GPRMC,123519,,4807.038,N,01131.000,E,,,230394,,"), ErrorKind.EMPTY_FIELD)
        assert err.field == "status"

    @pytest.mark.parametrize(
        "body, field",
        [
            ("GPRMC,,A,4807.038,N,01131.000,E,,,230394,,", "time"),
            ("GPRMC,123519,A,4807.038,N,01131.000,E,,,,,", "date"),
            ("GPRMC,123519,A,,,01131.000,E,,,230394,,", "lat"),
        ],
    )
    def test_valid_status_with_missing_required_field_is_empty_field(self, body, field):
        err = expect_error(cs(body), ErrorKind.EMPTY_FIELD)
        assert err.field == field

    def test_void_status_with_empty_fields_is_valid(self):
        result = parse_sentence(cs("GPRMC,,V,,,,,,,,,,N"))
        assert isinstance(result, RMC)
        assert result.valid is False and result.date is None and result.lat is None

    @pytest.mark.parametrize(
        "body, field",
        [
            ("GPRMC,123519,X,4807.038,N,01131.000,E,,,230394,,", "status"),
            ("GPRMC,123519,A,4807.038,N,01131.000,E,,,320394,,", "date"),
            ("GPRMC,123519,A,4807.038,N,01131.000,E,,,300294,,", "date"),
            ("GPRMC,123519,A,4807.038,N,01131.000,E,,,23039,,", "date"),
            ("GPRMC,123519,A,4807.038,N,01131.000,E,fast,,230394,,", "speed_knots"),
        ],
    )
    def test_invalid_values_are_malformed_field(self, body, field):
        err = expect_error(cs(body), ErrorKind.MALFORMED_FIELD)
        assert err.field == field


# ------------------------------------------------------------------------------- GSA


class TestGSA:
    def test_parses_real_gsa_with_system_id(self):
        result = parse_sentence("$GNGSA,A,3,13,16,21,15,10,29,27,20,,,,,1.05,0.64,0.83,1*03")
        assert isinstance(result, GSA)
        assert result.talker == "GN"
        assert result.mode == "A"
        assert result.fix_type == 3
        assert result.prns == (13, 16, 21, 15, 10, 29, 27, 20)
        assert result.pdop == pytest.approx(1.05)
        assert result.hdop == pytest.approx(0.64)
        assert result.vdop == pytest.approx(0.83)
        assert result.system_id == 1

    def test_parses_real_gsa_with_no_satellites_listed(self):
        result = parse_sentence("$GNGSA,A,3,,,,,,,,,,,,,4.89,2.92,3.92,3*07")
        assert isinstance(result, GSA)
        assert result.prns == ()
        assert result.system_id == 3

    def test_parses_gsa_without_system_id(self):
        result = parse_sentence(cs("GPGSA,M,1,,,,,,,,,,,,,,,"))
        assert isinstance(result, GSA)
        assert result.mode == "M"
        assert result.fix_type == 1
        assert result.pdop is None and result.system_id is None

    def test_too_few_fields_is_field_count(self):
        expect_error(cs("GPGSA,A,3,01,02"), ErrorKind.FIELD_COUNT)

    def test_empty_fix_type_is_empty_field(self):
        err = expect_error(cs("GPGSA,A,,,,,,,,,,,,,,,,"), ErrorKind.EMPTY_FIELD)
        assert err.field == "fix_type"

    @pytest.mark.parametrize(
        "body, field",
        [
            ("GPGSA,A,4,,,,,,,,,,,,,1.0,1.0,1.0", "fix_type"),
            ("GPGSA,Q,3,,,,,,,,,,,,,1.0,1.0,1.0", "mode"),
            ("GPGSA,A,3,0x,,,,,,,,,,,,1.0,1.0,1.0", "prn"),
            ("GPGSA,A,3,,,,,,,,,,,,,1.0,-1.0,1.0", "hdop"),
        ],
    )
    def test_invalid_values_are_malformed_field(self, body, field):
        err = expect_error(cs(body), ErrorKind.MALFORMED_FIELD)
        assert err.field == field


# ------------------------------------------------------------------------------- GSV


class TestGSV:
    def test_parses_real_gsv_with_four_satellites_and_signal_id(self):
        result = parse_sentence(
            "$GPGSV,4,4,16,29,46,133,43,44,45,171,45,46,45,191,43,51,43,156,43,1*60"
        )
        assert isinstance(result, GSV)
        assert result.talker == "GP"
        assert (result.total_msgs, result.msg_num, result.sats_in_view) == (4, 4, 16)
        assert result.satellites == (
            SatelliteInView(prn=29, elevation_deg=46, azimuth_deg=133, snr_dbhz=43),
            SatelliteInView(prn=44, elevation_deg=45, azimuth_deg=171, snr_dbhz=45),
            SatelliteInView(prn=46, elevation_deg=45, azimuth_deg=191, snr_dbhz=43),
            SatelliteInView(prn=51, elevation_deg=43, azimuth_deg=156, snr_dbhz=43),
        )
        assert result.signal_id == 1

    def test_satellite_not_tracked_has_no_snr(self):
        result = parse_sentence(
            "$GPGSV,3,3,12,26,04,276,,27,15,218,31,29,24,004,13,30,00,159,,1*69"
        )
        assert isinstance(result, GSV)
        assert result.satellites[0].snr_dbhz is None
        assert result.satellites[3] == SatelliteInView(30, 0, 159, None)

    def test_parses_last_part_with_fewer_than_four_satellites(self):
        result = parse_sentence(cs("GLGSV,3,3,10,85,30,300,40,86,10,100,"))
        assert isinstance(result, GSV)
        assert len(result.satellites) == 2
        assert result.signal_id is None

    def test_empty_padding_blocks_are_skipped(self):
        # Some receivers pad the last GSV part to four blocks with empty fields.
        result = parse_sentence(cs("GPGSV,3,3,09,05,06,110,19,,,,,,,,,,,,"))
        assert isinstance(result, GSV)
        assert result.satellites == (SatelliteInView(5, 6, 110, 19),)

    def test_parses_gsv_with_no_satellites_in_view(self):
        result = parse_sentence(cs("GPGSV,1,1,00"))
        assert isinstance(result, GSV)
        assert result.satellites == ()

    def test_wrong_number_of_satellite_fields_is_field_count(self):
        expect_error(cs("GPGSV,1,1,01,05,06,110"), ErrorKind.FIELD_COUNT)

    def test_more_than_four_satellites_is_field_count(self):
        sats = ",".join(["05,06,110,19"] * 5)
        expect_error(cs(f"GPGSV,1,1,05,{sats}"), ErrorKind.FIELD_COUNT)

    def test_empty_message_counter_is_empty_field(self):
        err = expect_error(cs("GPGSV,,1,00"), ErrorKind.EMPTY_FIELD)
        assert err.field == "total_msgs"

    @pytest.mark.parametrize(
        "body, field",
        [
            ("GPGSV,2,3,08,05,06,110,19", "msg_num"),
            ("GPGSV,1,1,01,05,91,110,19", "elevation_deg"),
            ("GPGSV,1,1,01,05,06,360,19", "azimuth_deg"),
            ("GPGSV,1,1,01,05,06,110,100", "snr_dbhz"),
            ("GPGSV,1,1,01,,06,110,19", "prn"),
        ],
    )
    def test_invalid_values_are_malformed_field(self, body, field):
        err = expect_error(cs(body), ErrorKind.MALFORMED_FIELD)
        assert err.field == field


# ------------------------------------------------------------------- whole real logs


@pytest.mark.parametrize(
    "log", ["unicore-um621.log", "ublox-zed-f9p-nmea.log", "ublox-neo-m9n-nmea.log"]
)
def test_every_real_sentence_parses_without_checksum_or_framing_errors(log):
    """Real receiver output must not produce checksum, truncation or field errors.

    Only UNKNOWN_TYPE is allowed: unicore-um621.log contains $SNRSTAT and $GYOACC,
    which its header documents as invalid talker IDs.
    """
    kinds: dict[ErrorKind, int] = {}
    parsed = 0
    for line in (DATA / log).read_text().splitlines():
        if not line.startswith("$"):
            continue  # '#' comments are handled by the log reader, not the parser
        result = parse_sentence(line)
        if isinstance(result, ParseError):
            kinds[result.kind] = kinds.get(result.kind, 0) + 1
        else:
            parsed += 1
    assert set(kinds) <= {ErrorKind.UNKNOWN_TYPE}, kinds
    assert parsed > 0


def test_unicore_log_unknown_types_are_exactly_snrstat_and_gyoacc():
    unknown = set()
    for line in (DATA / "unicore-um621.log").read_text().splitlines():
        if line.startswith("$"):
            result = parse_sentence(line)
            if isinstance(result, ParseError):
                unknown.add(line[1:].split(",")[0])
    assert unknown == {"SNRSTAT", "GYOACC"}


# ------------------------------------------------------------------- robustness / fuzz


def _real_lines() -> list[str]:
    lines = []
    for log in DATA.glob("*.log"):
        lines += [l for l in log.read_text().splitlines() if l.startswith("$")]
    return lines


def _mutate(rng: random.Random, line: str) -> str:
    choice = rng.randrange(6)
    pos = rng.randrange(len(line) + 1)
    if choice == 0:  # truncate
        return line[:pos]
    if choice == 1:  # flip one character to any code point in a wide range
        if not line:
            return line
        i = rng.randrange(len(line))
        return line[:i] + chr(rng.randrange(0, 0x3000)) + line[i + 1 :]
    if choice == 2:  # insert random printable ASCII
        return line[:pos] + chr(rng.randrange(32, 127)) + line[pos:]
    if choice == 3:  # delete a field
        parts = line.split(",")
        if len(parts) > 1:
            del parts[rng.randrange(1, len(parts))]
        return ",".join(parts)
    if choice == 4:  # blank a field
        parts = line.split(",")
        parts[rng.randrange(len(parts))] = ""
        return ",".join(parts)
    # random garbage
    return "".join(chr(rng.randrange(0, 256)) for _ in range(rng.randrange(0, 90)))


def _rechecksum(line: str) -> str:
    """Give a mutated line a valid checksum so mutations reach the field parsers."""
    if not line.startswith("$"):
        return line
    body = line[1:].split("*")[0]
    return cs(body)


@pytest.mark.parametrize("seed", range(5))
def test_parser_never_raises_on_mutated_real_input(seed):
    rng = random.Random(seed)
    lines = _real_lines()
    for _ in range(2000):
        mutated = _mutate(rng, rng.choice(lines))
        for candidate in (mutated, _rechecksum(mutated)):
            result = parse_sentence(candidate)
            assert isinstance(result, (GGA, RMC, GSA, GSV, OtherSentence, ParseError))
            if isinstance(result, ParseError):
                assert isinstance(result.kind, ErrorKind)
                assert result.message
