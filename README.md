# gnsskit: GNSS NMEA test toolkit

Parses recorded NMEA 0183 logs from GNSS receivers, checks their integrity, analyses
receiver performance (TTFF, fix availability, outages, HDOP, CEP) and writes a
Markdown test report.

Status: early development.

## Setup

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Run the tests

```sh
.venv/bin/pytest
.venv/bin/robot --outputdir results tests/acceptance
```

## Test report

```sh
gnsskit report data/real/unicore-um621.log -o report.md
gnsskit report LOG --profile profiles/high-precision.yaml --ref 51.477928,-0.001545
gnsskit report LOG --max-ttff none --max-cep95 3
```

The report covers: verdict, log summary, integrity problems with line numbers,
fix availability, TTFF, fix-lost/regained and data-gap events, satellites used (overall,
per constellation, over time), HDOP statistics, CEP50/CEP95, and definitions.

Each PASS/FAIL line shows the measured value, the threshold, and where the threshold came
from (profile name or command-line override). A criterion the log cannot show, such as
TTFF in a log that starts with a fix, is NOT MEASURABLE. Exit codes: `0` PASS, `1` FAIL,
`3` INCOMPLETE (nothing failed, but something could not be measured), `2` usage or input
error.

### Test profiles

Thresholds come from a YAML profile. Without `--profile`, the built-in values equal
[profiles/standard-gnss.yaml](profiles/standard-gnss.yaml). Each threshold can be
overridden with a flag (`--min-availability`, `--max-ttff`, `--max-hdop-p95`,
`--max-cep95`, `--max-integrity-errors`); `none` disables that check.

| Profile | Availability | TTFF | HDOP p95 | CEP95 | Integrity errors |
|---|---|---|---|---|---|
| `standard-gnss` | ≥ 95 % | ≤ 60 s | ≤ 2.0 | ≤ 5 m | 0 |
| `high-precision` | ≥ 99 % | ≤ 30 s | ≤ 1.0 | ≤ 0.5 m | 0 |

**Both profiles are illustrative examples chosen for this project. They are not taken
from any product datasheet, standard or company specification.**

## Fault injection

Write a copy of a clean log with controlled faults, plus a JSON manifest that says
what was injected and what the analyser must report:

```sh
gnsskit inject data/real/ublox-neo-m9n-nmea.log -o faulty.log --seed 42 \
    --outage 10:30 --bad-checksums 3 --midnight 20
```

Options: `--bad-checksums N`, `--truncate N`, `--blank-positions N`, `--unknown N`,
`--garbage N`, `--drop-gsv-parts N`, `--outage START:DURATION`, `--gap START:DURATION`,
`--midnight SECONDS`. Windows are in seconds from the first epoch. The output starts
with a `# FAULT-INJECTED` header line so it cannot be mistaken for a real recording.

## Data

The real logs come from the gpsd project (BSD-2-Clause). See [data/SOURCES.md](data/SOURCES.md).
