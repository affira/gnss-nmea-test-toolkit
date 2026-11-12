"""NMEA 0183 sentence parser.

`parse_sentence(line)` never raises on bad input. It returns either a typed sentence
(GGA, RMC, GSA, GSV, OtherSentence) or a `ParseError` whose `kind` says what went
wrong, so callers can count and report problems instead of crashing.

Supported layouts include NMEA 4.10+ fields: the GSA system ID and the GSV signal ID.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import reduce

__all__ = [
    "GGA",
    "GSA",
    "GSV",
    "RMC",
    "ErrorKind",
    "OtherSentence",
    "ParseError",
    "SatelliteInView",
    "Sentence",
    "constellation_name",
    "nmea_checksum",
    "parse_sentence",
]


# ------------------------------------------------------------------------- results


class ErrorKind(StrEnum):
    NON_NMEA = "non_nmea"  # not an NMEA sentence at all (no '$', binary, JSON, ...)
    TRUNCATED = "truncated"  # checksum missing or incomplete, or line runs into another
    BAD_CHECKSUM = "bad_checksum"  # checksum present but wrong or malformed
    UNKNOWN_TYPE = "unknown_type"  # invalid address or unknown sentence formatter
    FIELD_COUNT = "field_count"  # too few / wrong number of fields for the type
    EMPTY_FIELD = "empty_field"  # a field required in this context is empty
    MALFORMED_FIELD = "malformed_field"  # a field value cannot be parsed or is out of range


@dataclass(frozen=True)
class ParseError:
    kind: ErrorKind
    message: str
    line: str
    field: str | None = None


@dataclass(frozen=True)
class GGA:
    """Global Positioning System Fix Data."""

    talker: str
    time: dt.time | None
    lat: float | None  # decimal degrees, south negative
    lon: float | None  # decimal degrees, west negative
    quality: int  # 0 = no fix, 1 = GPS, 2 = DGPS, 4 = RTK fixed, 5 = RTK float, 6 = estimated
    num_sats: int | None
    hdop: float | None
    altitude_m: float | None
    geoid_sep_m: float | None


@dataclass(frozen=True)
class RMC:
    """Recommended Minimum Specific GNSS Data."""

    talker: str
    time: dt.time | None
    status: str  # 'A' = valid, 'V' = void
    lat: float | None
    lon: float | None
    speed_knots: float | None
    course_deg: float | None
    date: dt.date | None
    mode: str | None  # NMEA 2.3+: A autonomous, D differential, N not valid, ...

    @property
    def valid(self) -> bool:
        return self.status == "A"


@dataclass(frozen=True)
class GSA:
    """GNSS DOP and Active Satellites."""

    talker: str
    mode: str  # 'A' automatic, 'M' manual
    fix_type: int  # 1 = no fix, 2 = 2D, 3 = 3D
    prns: tuple[int, ...]
    pdop: float | None
    hdop: float | None
    vdop: float | None
    system_id: int | None  # NMEA 4.10+


@dataclass(frozen=True)
class SatelliteInView:
    prn: int
    elevation_deg: int | None
    azimuth_deg: int | None
    snr_dbhz: int | None  # None = satellite not tracked


@dataclass(frozen=True)
class GSV:
    """GNSS Satellites in View (one part of a multi-part message)."""

    talker: str
    total_msgs: int
    msg_num: int
    sats_in_view: int
    satellites: tuple[SatelliteInView, ...]
    signal_id: int | None  # NMEA 4.10+


@dataclass(frozen=True)
class OtherSentence:
    """A valid sentence of a type this toolkit does not interpret (VTG, ZDA, proprietary...)."""

    talker: str | None  # None for proprietary sentences
    formatter: str  # e.g. 'VTG', or the full address 'PDTINFO' for proprietary
    proprietary: bool


Sentence = GGA | RMC | GSA | GSV | OtherSentence


# ---------------------------------------------------------------------- constants

_CONSTELLATIONS = {
    "GP": "GPS",
    "GL": "GLONASS",
    "GA": "Galileo",
    "GB": "BeiDou",
    "BD": "BeiDou",
    "GQ": "QZSS",
    "QZ": "QZSS",
    "GI": "NavIC",
    "GN": "Multi-GNSS",
}

# Standard NMEA formatters we recognise but do not interpret.
_KNOWN_OTHER_FORMATTERS = frozenset(
    "AAM ALM APB BOD BWC DBT DPT DTM GBS GLL GNS GRS GST HDG HDM HDT MSK MSS MWV "
    "RMB RTE THS TXT VHW VTG WPL XTE ZDA".split()
)

_STANDARD_ADDRESS = re.compile(r"[A-Z]{5}")
_PROPRIETARY_ADDRESS = re.compile(r"P[A-Z0-9]+")
_HEX2 = re.compile(r"[0-9A-Fa-f]{2}")
_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
_INTEGER = re.compile(r"\d+")
_TIME = re.compile(r"(\d{2})(\d{2})(\d{2})(?:\.(\d+))?")
_DATE = re.compile(r"(\d{2})(\d{2})(\d{2})")
_COORD = re.compile(r"(\d+)(\d{2}(?:\.\d*)?)")  # degrees then 2-digit minutes


def constellation_name(talker: str) -> str:
    return _CONSTELLATIONS.get(talker, "Unknown")


def nmea_checksum(body: str) -> str:
    """XOR of all characters between '$' and '*', as two uppercase hex digits."""
    return f"{reduce(lambda acc, ch: acc ^ ord(ch), body, 0):02X}"


# --------------------------------------------------------------------- field parsing


class _FieldError(Exception):
    def __init__(self, kind: ErrorKind, field: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.field = field
        self.message = message


def _malformed(field: str, value: str, why: str = "cannot be parsed") -> _FieldError:
    return _FieldError(ErrorKind.MALFORMED_FIELD, field, f"{field}={value!r} {why}")


def _empty(field: str, why: str) -> _FieldError:
    return _FieldError(ErrorKind.EMPTY_FIELD, field, f"{field} is empty {why}")


def _float(value: str, field: str, *, minimum: float | None = None) -> float | None:
    if value == "":
        return None
    if not _DECIMAL.fullmatch(value):
        raise _malformed(field, value)
    number = float(value)
    if minimum is not None and number < minimum:
        raise _malformed(field, value, f"is below {minimum}")
    return number


def _int(value: str, field: str, *, lo: int | None = None, hi: int | None = None) -> int | None:
    if value == "":
        return None
    if not _INTEGER.fullmatch(value):
        raise _malformed(field, value)
    number = int(value)
    if (lo is not None and number < lo) or (hi is not None and number > hi):
        raise _malformed(field, value, f"is outside {lo}..{hi}")
    return number


def _required_int(value: str, field: str, *, lo: int, hi: int, context: str) -> int:
    number = _int(value, field, lo=lo, hi=hi)
    if number is None:
        raise _empty(field, context)
    return number


def _time(value: str, field: str = "time") -> dt.time | None:
    if value == "":
        return None
    match = _TIME.fullmatch(value)
    if not match:
        raise _malformed(field, value)
    hh, mm, ss, frac = match.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    try:
        return dt.time(int(hh), int(mm), int(ss), micro)
    except ValueError:
        raise _malformed(field, value, "is not a valid time of day") from None


def _date(value: str, field: str = "date") -> dt.date | None:
    if value == "":
        return None
    match = _DATE.fullmatch(value)
    if not match:
        raise _malformed(field, value)
    dd, mm, yy = (int(g) for g in match.groups())
    year = 2000 + yy if yy < 80 else 1900 + yy
    try:
        return dt.date(year, mm, dd)
    except ValueError:
        raise _malformed(field, value, "is not a valid date") from None


def _coord(value: str, hemisphere: str, field: str, *, axis: str) -> float | None:
    """Parse ddmm.mmmm (lat) or dddmm.mmmm (lon) plus N/S/E/W into signed degrees."""
    positive, negative, max_deg = ("N", "S", 90) if axis == "lat" else ("E", "W", 180)
    hemi_field = f"{field}_hemisphere"
    if value == "":
        return None
    match = _COORD.fullmatch(value)
    if not match:
        raise _malformed(field, value)
    degrees, minutes = int(match.group(1)), float(match.group(2))
    if minutes >= 60:
        raise _malformed(field, value, "has minutes >= 60")
    result = degrees + minutes / 60
    if result > max_deg:
        raise _malformed(field, value, f"is beyond {max_deg} degrees")
    if hemisphere == "":
        raise _empty(hemi_field, f"while {field} is present")
    if hemisphere not in (positive, negative):
        raise _malformed(hemi_field, hemisphere, f"is not {positive} or {negative}")
    return -result if hemisphere == negative else result


def _require_fields(fields: list[str], minimum: int, name: str) -> None:
    if len(fields) < minimum:
        raise _FieldError(
            ErrorKind.FIELD_COUNT,
            name,
            f"{name} needs at least {minimum} fields, got {len(fields)}",
        )


# ------------------------------------------------------------------ sentence parsers


def _parse_gga(talker: str, f: list[str]) -> GGA:
    _require_fields(f, 14, "GGA")
    time = _time(f[0])
    lat = _coord(f[1], f[2], "lat", axis="lat")
    lon = _coord(f[3], f[4], "lon", axis="lon")
    quality = _required_int(f[5], "quality", lo=0, hi=8, context="(fix quality is mandatory)")
    if quality > 0:
        for field, value in (("time", time), ("lat", lat), ("lon", lon)):
            if value is None:
                raise _empty(field, f"while fix quality is {quality}")
    return GGA(
        talker=talker,
        time=time,
        lat=lat,
        lon=lon,
        quality=quality,
        num_sats=_int(f[6], "num_sats"),
        hdop=_float(f[7], "hdop", minimum=0),
        altitude_m=_float(f[8], "altitude_m"),
        geoid_sep_m=_float(f[10], "geoid_sep_m"),
    )


def _parse_rmc(talker: str, f: list[str]) -> RMC:
    _require_fields(f, 11, "RMC")
    status = f[1]
    if status == "":
        raise _empty("status", "(status is mandatory)")
    if status not in ("A", "V"):
        raise _malformed("status", status, "is not A or V")
    time = _time(f[0])
    lat = _coord(f[2], f[3], "lat", axis="lat")
    lon = _coord(f[4], f[5], "lon", axis="lon")
    date = _date(f[8])
    if status == "A":
        for field, value in (("time", time), ("date", date), ("lat", lat), ("lon", lon)):
            if value is None:
                raise _empty(field, "while status is A (valid)")
    return RMC(
        talker=talker,
        time=time,
        status=status,
        lat=lat,
        lon=lon,
        speed_knots=_float(f[6], "speed_knots", minimum=0),
        course_deg=_float(f[7], "course_deg"),
        date=date,
        mode=(f[11] or None) if len(f) > 11 else None,
    )


def _parse_gsa(talker: str, f: list[str]) -> GSA:
    _require_fields(f, 17, "GSA")
    mode = f[0]
    if mode not in ("A", "M"):
        raise _malformed("mode", mode, "is not A or M")
    fix_type = _required_int(f[1], "fix_type", lo=1, hi=3, context="(fix type is mandatory)")
    prns = tuple(p for p in (_int(v, "prn") for v in f[2:14]) if p is not None)
    return GSA(
        talker=talker,
        mode=mode,
        fix_type=fix_type,
        prns=prns,
        pdop=_float(f[14], "pdop", minimum=0),
        hdop=_float(f[15], "hdop", minimum=0),
        vdop=_float(f[16], "vdop", minimum=0),
        system_id=_int(f[17], "system_id") if len(f) > 17 else None,
    )


def _parse_gsv(talker: str, f: list[str]) -> GSV:
    _require_fields(f, 3, "GSV")
    sat_fields = len(f) - 3
    if sat_fields % 4 not in (0, 1) or sat_fields // 4 > 4:
        raise _FieldError(
            ErrorKind.FIELD_COUNT,
            "GSV",
            f"GSV has {sat_fields} satellite fields; expected 4 per satellite (max 4), "
            "optionally plus a signal ID",
        )
    total = _required_int(f[0], "total_msgs", lo=1, hi=99, context="(message count is mandatory)")
    num = _required_int(f[1], "msg_num", lo=1, hi=99, context="(message number is mandatory)")
    if num > total:
        raise _malformed("msg_num", f[1], f"is greater than total_msgs={total}")
    in_view = _required_int(f[2], "sats_in_view", lo=0, hi=999, context="(count is mandatory)")
    satellites = []
    for i in range(sat_fields // 4):
        prn_s, elev_s, az_s, snr_s = f[3 + 4 * i : 7 + 4 * i]
        if not (prn_s or elev_s or az_s or snr_s):
            continue  # empty padding block
        if prn_s == "":
            raise _malformed("prn", prn_s, "is empty but the satellite block has data")
        satellites.append(
            SatelliteInView(
                prn=_int(prn_s, "prn"),  # type: ignore[arg-type]  # non-empty checked above
                elevation_deg=_int(elev_s, "elevation_deg", lo=0, hi=90),
                azimuth_deg=_int(az_s, "azimuth_deg", lo=0, hi=359),
                snr_dbhz=_int(snr_s, "snr_dbhz", lo=0, hi=99),
            )
        )
    signal_id = _int(f[-1], "signal_id") if sat_fields % 4 == 1 else None
    return GSV(
        talker=talker,
        total_msgs=total,
        msg_num=num,
        sats_in_view=in_view,
        satellites=tuple(satellites),
        signal_id=signal_id,
    )


_PARSERS = {"GGA": _parse_gga, "RMC": _parse_rmc, "GSA": _parse_gsa, "GSV": _parse_gsv}


# ------------------------------------------------------------------------ entry point


def parse_sentence(line: str) -> Sentence | ParseError:
    """Parse one line of NMEA 0183. Never raises for bad input."""
    text = line.strip()

    def error(kind: ErrorKind, message: str, field: str | None = None) -> ParseError:
        return ParseError(kind=kind, message=message, line=line, field=field)

    if not text.startswith("$"):
        return error(ErrorKind.NON_NMEA, "line does not start with '$'")
    if not all(32 <= ord(ch) < 127 for ch in text):
        return error(ErrorKind.NON_NMEA, "line contains non-printable or non-ASCII characters")
    if "$" in text[1:]:
        return error(ErrorKind.TRUNCATED, "line contains a second '$' (sentence cut off)")
    if "*" not in text:
        return error(ErrorKind.TRUNCATED, "checksum delimiter '*' is missing")

    body, _, checksum = text[1:].rpartition("*")
    if len(checksum) < 2:
        return error(ErrorKind.TRUNCATED, f"checksum {checksum!r} is incomplete")
    if not _HEX2.fullmatch(checksum):
        return error(ErrorKind.BAD_CHECKSUM, f"checksum {checksum!r} is not two hex digits")
    expected = nmea_checksum(body)
    if checksum.upper() != expected:
        return error(
            ErrorKind.BAD_CHECKSUM, f"checksum is {checksum.upper()}, expected {expected}"
        )

    address, *fields = body.split(",")
    if _PROPRIETARY_ADDRESS.fullmatch(address):
        return OtherSentence(talker=None, formatter=address, proprietary=True)
    if not _STANDARD_ADDRESS.fullmatch(address):
        return error(ErrorKind.UNKNOWN_TYPE, f"invalid address {address!r}")

    talker, formatter = address[:2], address[2:]
    parser = _PARSERS.get(formatter)
    if parser is None:
        if formatter in _KNOWN_OTHER_FORMATTERS:
            return OtherSentence(talker=talker, formatter=formatter, proprietary=False)
        return error(ErrorKind.UNKNOWN_TYPE, f"unknown sentence type {formatter!r}")

    try:
        return parser(talker, fields)
    except _FieldError as exc:
        return error(exc.kind, exc.message, exc.field)
