# gnsskit: GNSS NMEA log test toolkit

gnsskit reads recorded NMEA 0183 logs from GNSS receivers, checks their integrity, measures
receiver performance and writes a Markdown test report with PASS/FAIL against a test profile.
It also corrupts clean logs in controlled ways, so the tests can prove that each fault is
detected.

It is a portfolio project for GNSS test engineering. The emphasis is on testing: every
feature was written together with its tests, and expected values for real logs were counted
independently of the code (with grep and awk) before being used in tests.

Status: parser, log reader, analyser, fault injector, synthetic logs and report CLI are done.
GitHub Actions CI ([ci.yml](.github/workflows/ci.yml)) runs pytest and Robot Framework on
Python 3.11 and 3.12, checks the synthetic logs, generates a sample report and uploads the
Robot report as an artifact.

## What it does

- **Parses** GGA, RMC, GSA and GSV for any talker (GP, GL, GA, GB, GN and others), including
  NMEA 4.10+ system and signal IDs, and validates checksums.
- **Never crashes on bad input.** Wrong checksums, truncated lines, empty or malformed
  fields, unknown sentence types, non-NMEA lines, time going backwards, duplicates and
  incomplete GSV messages are counted and reported with line numbers.
- **Groups sentences into epochs** and handles UTC day rollover, from RMC dates or inferred.
- **Analyses** time to first fix (TTFF), fix availability, fix-lost / fix-regained and
  data-gap events, satellites used (overall, per constellation, over time), HDOP statistics,
  and CEP50/CEP95 against the mean position or a given reference point.
- **Injects faults** with a fixed seed: bad checksums, truncation, blanked positions, unknown
  sentences, garbage lines, dropped GSV parts, simulated outages, removed time windows and a
  midnight time shift. A JSON manifest says what the analyser must report.
- **Writes a test report** with PASS/FAIL against thresholds from a YAML test profile.

## Setup

Requires Python 3.11 or newer.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Runtime dependency: PyYAML (profile files). Development: pytest, pytest-cov, Robot Framework.

## Usage

```sh
# Test report (Markdown) for a log
gnsskit report data/real/unicore-um621.log -o report.md

# With a profile, a reference point for CEP, and overridden or disabled thresholds
gnsskit report LOG --profile profiles/high-precision.yaml --ref 51.477928,-0.001545
gnsskit report LOG --max-ttff none --max-cep95 3

# Copy of a clean log with faults, plus faulty.log.manifest.json
gnsskit inject data/real/ublox-neo-m9n-nmea.log -o faulty.log --seed 42 \
    --outage 10:30 --bad-checksums 3 --midnight 20

# Regenerate, or check, the synthetic logs
python scripts/make_synthetic.py [--check]
```

`gnsskit report` exit codes: `0` PASS, `1` FAIL, `3` INCOMPLETE (nothing failed, but a
criterion could not be measured), `2` usage or input error.

`gnsskit inject` options: `--bad-checksums N`, `--truncate N`, `--blank-positions N`,
`--unknown N`, `--garbage N`, `--drop-gsv-parts N`, `--outage START:DURATION`,
`--gap START:DURATION`, `--midnight SECONDS`. Windows are seconds from the first epoch.
Output starts with a `# FAULT-INJECTED` line so it cannot be mistaken for a real recording.

## Running the tests

```sh
pytest                                          # unit tests
pytest --cov=gnsskit --cov-branch               # with branch coverage
robot --outputdir results tests/acceptance      # acceptance tests
```

At the time of writing: 346 pytest tests and 20 Robot Framework tests pass, with 99 %
branch coverage. Robot tests are written Given/When/Then, for example:

```robotframework
Static Log With A 30-Second Outage Reports One Fix Loss
    Given the recorded log "ublox-neo-m9n-nmea.log"
    And a 30-second outage starting at 10 s
    When the log is analysed
    Then 1 fix-lost event reported
    And the outage lasts 30 seconds
```

## Example report from a real log

Generated with `gnsskit report data/real/unicore-um621.log` (exit code 1). Full report:
[docs/example-report-unicore-um621.md](docs/example-report-unicore-um621.md). The thresholds
are the built-in `standard-gnss` values, which are **illustrative examples, not taken from
any product datasheet, standard or company specification**.

> ## Verdict: FAIL
>
> 2 criteria failed: Fix availability; HDOP, 95th percentile.
>
> | Criterion | Measured | Threshold | Threshold source | Result |
> |---|---|---|---|---|
> | Fix availability | 80.0 % | ≥ 95.0 % | profile standard-gnss | **FAIL** |
> | Time to first fix | not measurable | ≤ 60.0 s | profile standard-gnss | NOT MEASURABLE: log starts with a fix |
> | HDOP, 95th percentile | 4.73 | ≤ 2.00 | profile standard-gnss | **FAIL** |
> | CEP95 | 2.895 m | ≤ 5.00 m | profile standard-gnss | **PASS** |
> | Integrity errors | 0 | ≤ 0 | profile standard-gnss | **PASS** |
>
> | Event | Time (UTC) | Line | Duration |
> |---|---|---|---|
> | Fix lost | 2024-06-09 00:07:06 | 592 | 4.0 s |
> | Fix regained | 2024-06-09 00:07:10 | 768 | 4.0 s |

Other values from the same report: 20 epochs at 1 Hz; 24.6 satellites used on average
(GSA); HDOP median 0.68, max 10.16; CEP50 0.732 m around the mean position from 16 fixes
(the report flags this as a small sample); 220 receiver-specific sentences (`$SNRSTAT`,
`$GYOACC`) listed as unrecognised but not counted as integrity errors.

## Test profiles

| Profile | Availability | TTFF | HDOP p95 | CEP95 | Integrity errors |
|---|---|---|---|---|---|
| [`standard-gnss`](profiles/standard-gnss.yaml) (default) | ≥ 95 % | ≤ 60 s | ≤ 2.0 | ≤ 5 m | 0 |
| [`high-precision`](profiles/high-precision.yaml) | ≥ 99 % | ≤ 30 s | ≤ 1.0 | ≤ 0.5 m | 0 |

**Both profiles are illustrative examples chosen for this project. They are not taken from
any product datasheet, standard or company specification.** Each threshold can be overridden
with a flag (`--min-availability`, `--max-ttff`, `--max-hdop-p95`, `--max-cep95`,
`--max-integrity-errors`); `none` disables it. The report shows, next to every PASS/FAIL, the
threshold and whether it came from the profile or a command-line override.

## Design decisions

- **Errors are values, not exceptions.** The parser returns a typed `ParseError`; the reader
  turns it into an issue with a line number. A seeded fuzz test feeds 10,000 mutated real
  lines through the parser.
- **Epochs start at a GGA or RMC with a new time**; GSA and GSV join the current epoch. This
  works for receivers that send GGA first (u-blox) and RMC first (Unicore). A second pass
  assigns dates: RMC dates when consistent, otherwise a rollover when the time of day goes
  back by more than 12 hours.
- **A fix is GGA quality 1–5.** Quality 6 (estimated / dead reckoning) is not a fix.
- **Availability uses expected epochs.** Missing seconds count against availability and are
  reported as data gaps, not as fix losses.
- **Satellites used come from GSA as well as GGA**, because the u-blox logs cap the GGA count
  at 12 while GSA lists up to 25.
- **CEP** uses WGS84 radii of curvature for local east/north metres, and empirical
  percentiles with linear interpolation.
- **Fault injection is reproducible and self-checking.** A seed fixes the output, and the
  manifest records exact expected counts. The injector never corrupts both the GGA and RMC of
  one epoch, so every epoch survives.
- **Synthetic logs are generated, labelled and reproducible.** A test fails if a committed
  synthetic file differs from the generator output. The generator converts metres to degrees
  with a published formula, not the analyser's, so CEP is checked independently.
- **Unmeasurable is not passing.** A criterion the log cannot show is NOT MEASURABLE and
  makes the verdict INCOMPLETE. Valid sentences of an unrecognised type are reported but not
  counted as integrity errors.

## Limitations

- **Tested on recorded logs only, not on live hardware.** Nothing reads a serial port or
  controls a receiver.
- **NMEA 0183 only.** The u-blox UBX binary protocol, RTCM and other formats are not parsed.
  Only GGA, RMC, GSA and GSV are analysed.
- **Short real logs.** The three gpsd logs last 20–61 s and all start with a fix, so they
  cannot show TTFF and give small-sample CEP.
- **TTFF is measured from the first epoch in the log**, which stands in for power-on.
- **CEP assumes a static receiver.** Without `--ref` it measures spread around the mean, not
  accuracy against a surveyed point.
- **Synthetic noise is independent from epoch to epoch.** Real receiver errors are correlated
  over time.
- **Lost GGA in u-blox order.** When the GGA that starts a u-blox epoch is corrupted, that
  second's GSA/GSV are attached to the previous epoch, which then reports incomplete GSV.
- **Day rollover without dates** relies on the 12-hour rule, and a leap second (`23:59:60`)
  is rejected as a malformed time.

## Lessons learned and next steps

**Decisions.** I chose to count GGA quality 6 (estimated) as no fix, and to keep pass/fail
thresholds in YAML profiles labelled as illustrative. Criteria a log cannot show are reported
as NOT MEASURABLE, so the verdict becomes INCOMPLETE rather than PASS.

**Caught by tests.** Both u-blox logs report 12 satellites in every GGA while GSA lists
18–25. The ZED-F9P log has BeiDou GSV with an empty signal-ID field, which crashed the
injector's sorting. Two hand-typed checksums in the tests were wrong, and one test passed for
the wrong reason. Deliberate code mutations exposed untested boundaries, such as minutes
equal to 60.0.

**Real versus synthetic.** The gpsd logs start with a fix, so TTFF is not measurable from
them, and only the UM621 log has a real outage (4 s). The synthetic static log matches
Gaussian CEP theory (CEP50 1.181 m against 1.177 m), but its errors are independent between
epochs, unlike real ones.

**Limits.** Without hardware, TTFF starts at the log rather than at power-on, and CEP shows
spread around the mean rather than accuracy.

**Next steps on real hardware**

1. Record cold, warm and hot starts with timed power control to measure true TTFF.
2. Log hour-long static sessions on a surveyed point for accuracy and time-correlated CEP.
3. Create real outages (antenna disconnect or a GNSS simulator) and compare the detected
   events with the injector's.
4. Capture UBX NAV-PVT alongside NMEA to cross-check fix status, satellites and DOP.

## Project layout

```
src/gnsskit/      nmea.py (parser), log.py (epochs, rollover, issues), analysis.py,
                  faults.py (injector), synthetic.py, profile.py, report.py, cli.py
tests/unit/       pytest unit tests
tests/acceptance/ Robot Framework suites and the GnssLibrary.py keyword library
data/real/        recorded gpsd logs (unchanged) and the gpsd licence
data/synthetic/   SYNTHETIC logs written by scripts/make_synthetic.py
profiles/         illustrative YAML test profiles
docs/             example report
prompts/          the prompts used to build this project
```

## Data sources and licences

Full provenance, hashes and notes: [data/SOURCES.md](data/SOURCES.md).

- **Real logs** from the [gpsd project](https://gitlab.com/gpsd/gpsd) regression tests
  (`test/daemon/`, commit `092e6228`), licensed **BSD-2-Clause**. Copyright the GPSD project.
  The files are unchanged, keep their copyright headers, and are accompanied by gpsd's
  licence in [data/real/GPSD-COPYING](data/real/GPSD-COPYING).
  - `unicore-um621.log`: Unicore UM621, submitted by Gary E. Miller (2024)
  - `ublox-zed-f9p-nmea.log`: u-blox ZED-F9P, submitted by Luke Reid (2019)
  - `ublox-neo-m9n-nmea.log`: u-blox NEO-M9N, submitted by Fred Wright (2020)
- **Synthetic logs** (`data/synthetic/SYNTHETIC-*.log`) are generated by this project's
  script and are not real recordings. Their truth position is the Royal Observatory,
  Greenwich, a public landmark.

## Licence

- **Code** (everything outside `data/real/`): MIT licence, Copyright 2026 Affira. See
  [LICENSE](LICENSE). This includes the synthetic logs, which the project's own script
  generates.
- **Recorded logs in `data/real/`**: BSD-2-Clause, Copyright the GPSD project. See
  [data/real/GPSD-COPYING](data/real/GPSD-COPYING). They are redistributed unchanged.

## Prompts

The prompts used to build this project are in [prompts/](prompts/).
