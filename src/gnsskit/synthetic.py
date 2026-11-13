"""Generator for clearly labelled SYNTHETIC NMEA logs with known ground truth.

These logs cover cases the real recordings cannot: a cold start (TTFF), a long
static session (meaningful CEP) and a UTC day and year rollover.

Model (deliberately simple, and stated in each file header):
* Static receiver at TRUTH_LAT/TRUTH_LON (Royal Observatory, Greenwich).
* Horizontal error: independent Gaussian noise per epoch, sigma_m per axis.
  Real receiver errors are time-correlated; this model is not.
* Fixed sky of 19 satellites (7 GPS, 4 GLONASS, 4 Galileo, 4 BeiDou), all used.
* Output order per epoch: RMC, GGA, GSA x4 (NMEA 4.11 system IDs), GSV per talker.

Only `random.random()` is used for noise because Python guarantees its sequence
for a given seed, so the files are reproducible byte for byte.

Metres-to-degrees uses the standard series for the length of a degree on the
WGS84 ellipsoid, not gnsskit.analysis, so the analyser is checked independently.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass

from gnsskit import __version__
from gnsskit.nmea import nmea_checksum

__all__ = ["SCENARIOS", "TRUTH_LAT", "TRUTH_LON", "Scenario", "generate"]

TRUTH_LAT = 51.477_928  # Royal Observatory, Greenwich
TRUTH_LON = -0.001_545
ALTITUDE_M = 45.0
GEOID_SEP_M = 45.9

# (talker, NMEA 4.11 GSA system id, [(prn, elevation, azimuth), ...])
SKY = [
    ("GP", 1, [(2, 62, 41), (5, 35, 300), (12, 48, 120), (15, 22, 210), (24, 71, 280),
               (25, 15, 75), (29, 40, 160)]),
    ("GL", 2, [(65, 55, 90), (66, 30, 20), (72, 44, 250), (81, 18, 330)]),
    ("GA", 3, [(4, 66, 140), (9, 27, 60), (11, 39, 230), (36, 52, 310)]),
    ("GB", 4, [(6, 58, 180), (19, 33, 100), (20, 25, 270), (23, 47, 10)]),
]  # fmt: skip


@dataclass(frozen=True)
class Scenario:
    filename: str
    description: str
    start: dt.datetime  # UTC of the first epoch
    epochs: int  # 1 Hz
    no_fix_epochs: int  # leading epochs without a fix (cold start)
    sigma_m: float  # horizontal noise per axis
    seed: int


SCENARIOS = {
    "cold-start": Scenario(
        filename="SYNTHETIC-cold-start.log",
        description="cold start: no fix for the first 30 s, then 90 s of fix",
        start=dt.datetime(2025, 3, 14, 8, 0, 0),
        epochs=120,
        no_fix_epochs=30,
        sigma_m=1.0,
        seed=1,
    ),
    "static-10min": Scenario(
        filename="SYNTHETIC-static-10min.log",
        description="10 minutes static with fix throughout, for CEP",
        start=dt.datetime(2025, 3, 14, 12, 0, 0),
        epochs=600,
        no_fix_epochs=0,
        sigma_m=1.0,
        seed=2,
    ),
    "midnight-rollover": Scenario(
        filename="SYNTHETIC-midnight-rollover.log",
        description="crosses 00:00 UTC into a new day and a new year",
        start=dt.datetime(2025, 12, 31, 23, 59, 0),
        epochs=121,
        no_fix_epochs=0,
        sigma_m=1.0,
        seed=3,
    ),
}


# ------------------------------------------------------------------------- helpers


def _sentence(body: str) -> str:
    return f"${body}*{nmea_checksum(body)}"


def _gauss(rng: random.Random) -> float:
    """Standard normal via Box-Muller, using only rng.random()."""
    u1 = 1.0 - rng.random()  # (0, 1]
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _metres_per_degree(lat_deg: float) -> tuple[float, float]:
    """(north, east) metres per degree: standard WGS84 series."""
    phi = math.radians(lat_deg)
    north = 111_132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    east = 111_412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    return north, east


def _coord(value: float, degree_digits: int, pos: str, neg: str) -> tuple[str, str]:
    total_minutes = round(abs(value) * 60, 5)
    degrees = int(total_minutes // 60)
    minutes = total_minutes - degrees * 60
    return f"{degrees:0{degree_digits}d}{minutes:08.5f}", (pos if value >= 0 else neg)


def _hdop(i: int) -> float:
    return round(0.75 + 0.10 * math.sin(i / 60), 2)


# ------------------------------------------------------------------------ generate


def _epoch(scenario: Scenario, i: int, rng: random.Random) -> list[str]:
    when = scenario.start + dt.timedelta(seconds=i)
    hhmmss = when.strftime("%H%M%S") + ".00"
    ddmmyy = when.strftime("%d%m%y")
    fix = i >= scenario.no_fix_epochs
    total_sats = sum(len(sats) for _, _, sats in SKY)

    # Before the fix, satellites are acquired gradually.
    tracked = total_sats if fix else (total_sats * i) // max(scenario.no_fix_epochs, 1)
    noise_n, noise_e = _gauss(rng) * scenario.sigma_m, _gauss(rng) * scenario.sigma_m
    m_north, m_east = _metres_per_degree(TRUTH_LAT)
    lat = TRUTH_LAT + noise_n / m_north
    lon = TRUTH_LON + noise_e / m_east
    speed = abs(_gauss(rng)) * 0.02  # knots: static receiver, small noise

    lines = []
    if fix:
        lat_s, ns = _coord(lat, 2, "N", "S")
        lon_s, ew = _coord(lon, 3, "E", "W")
        hdop = _hdop(i)
        lines.append(_sentence(f"GNRMC,{hhmmss},A,{lat_s},{ns},{lon_s},{ew},{speed:.3f},,{ddmmyy},,,A,V"))
        lines.append(_sentence(
            f"GNGGA,{hhmmss},{lat_s},{ns},{lon_s},{ew},1,{total_sats:02d},{hdop:.2f},"
            f"{ALTITUDE_M:.1f},M,{GEOID_SEP_M:.1f},M,,"
        ))  # fmt: skip
        for _, system_id, sats in SKY:
            prns = [f"{prn:02d}" for prn, _, _ in sats] + [""] * (12 - len(sats))
            lines.append(_sentence(
                f"GNGSA,A,3,{','.join(prns)},{hdop * 1.6:.2f},{hdop:.2f},{hdop * 1.3:.2f},{system_id}"
            ))  # fmt: skip
    else:
        lines.append(_sentence(f"GNRMC,{hhmmss},V,,,,,,,{ddmmyy},,,N,V"))
        lines.append(_sentence(f"GNGGA,{hhmmss},,,,,0,00,,,M,,M,,"))
        for _, system_id, _ in SKY:
            lines.append(_sentence(f"GNGSA,A,1,{',' * 11},,,,{system_id}"))

    seen = 0
    for talker, _, sats in SKY:
        parts = [sats[k : k + 4] for k in range(0, len(sats), 4)]
        for n, part in enumerate(parts, start=1):
            blocks = []
            for prn, elev, az in part:
                seen += 1
                snr = f"{int(20 + elev * 0.3 + rng.random() * 4):02d}" if seen <= tracked else ""
                blocks.append(f"{prn:02d},{elev:02d},{az:03d},{snr}")
            lines.append(_sentence(f"{talker}GSV,{len(parts)},{n},{len(sats):02d},{','.join(blocks)},1"))
    return lines


def generate(scenario: Scenario) -> list[str]:
    rng = random.Random(scenario.seed)
    header = [
        f"# SYNTHETIC log generated by scripts/make_synthetic.py (gnsskit {__version__}). "
        "Not a real recording.",
        f"# Scenario: {scenario.description}",
        f"# Truth: static at {TRUTH_LAT:.6f} N, {abs(TRUTH_LON):.6f} W (Royal Observatory, "
        f"Greenwich); horizontal noise: independent Gaussian, sigma {scenario.sigma_m} m per axis",
        f"# Start {scenario.start:%Y-%m-%d %H:%M:%S} UTC, {scenario.epochs} epochs at 1 Hz, "
        f"first {scenario.no_fix_epochs} without fix, seed {scenario.seed}",
    ]
    lines = list(header)
    for i in range(scenario.epochs):
        lines.extend(_epoch(scenario, i, rng))
    return lines
