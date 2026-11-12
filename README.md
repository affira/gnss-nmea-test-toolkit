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

## Data

The real logs come from the gpsd project (BSD-2-Clause). See [data/SOURCES.md](data/SOURCES.md).
