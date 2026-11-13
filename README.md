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
