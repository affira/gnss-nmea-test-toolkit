# GNSS log test report: unicore-um621.log

| Item | Value |
|---|---|
| Log file | `unicore-um621.log` |
| SHA-256 | `182ac39fd16c26e5b87ec6f3c6aa89412b5a04c1a05b65e9efac2bfa4e2c4af7` |
| Generated | 2026-11-13 22:19:33 UTC by gnsskit 0.1.0 |

## Verdict: FAIL

2 criteria failed: Fix availability; HDOP, 95th percentile.

Test profile: **standard-gnss** (built in, same values as `profiles/standard-gnss.yaml`). General-purpose GNSS receiver acceptance check (illustrative).
_Illustrative example values: not taken from any product datasheet, standard or company specification._

| Criterion | Measured | Threshold | Threshold source | Result |
|---|---|---|---|---|
| Fix availability | 80.0 % | ≥ 95.0 % | profile standard-gnss | **FAIL** |
| Time to first fix | not measurable | ≤ 60.0 s | profile standard-gnss | NOT MEASURABLE: log starts with a fix |
| HDOP, 95th percentile | 4.73 | ≤ 2.00 | profile standard-gnss | **FAIL** |
| CEP95 | 2.895 m | ≤ 5.00 m | profile standard-gnss | **PASS** |
| Integrity errors | 0 | ≤ 0 | profile standard-gnss | **PASS** |

## Log summary

| Item | Value |
|---|---|
| Start (UTC) | 2024-06-09 00:06:53 |
| End (UTC) | 2024-06-09 00:07:12 |
| Duration | 19.0 s |
| Epoch interval | 1.00 s |
| Epochs present / expected | 20 / 20 |
| Epochs without a time | 0 |
| Lines (blank, comment) | 899 (0, 17) |
| Valid sentences (types not analysed) | 662 (42) |
| UTC day rollovers | 0 |

## Integrity

| Problem | Count | Counted as integrity error |
|---|---|---|
| unknown_type | 220 | no (unrecognised sentence type) |

First 10 of 220 problems:

| Line | Problem | Detail |
|---|---|---|
| 51 | unknown_type | invalid address 'SNRSTAT' |
| 52 | unknown_type | invalid address 'GYOACC' |
| 53 | unknown_type | invalid address 'GYOACC' |
| 54 | unknown_type | invalid address 'GYOACC' |
| 57 | unknown_type | invalid address 'GYOACC' |
| 58 | unknown_type | invalid address 'GYOACC' |
| 59 | unknown_type | invalid address 'GYOACC' |
| 60 | unknown_type | invalid address 'GYOACC' |
| 61 | unknown_type | invalid address 'GYOACC' |
| 62 | unknown_type | invalid address 'GYOACC' |

## Fix and outages

| Item | Value |
|---|---|
| Fix epochs | 16 of 20 expected |
| Fix availability | 80.0 % |
| Time to first fix | not measurable (log starts with a fix) |
| Fix-lost events | 1 |
| Longest completed outage | 4.0 s |

### Events

| Event | Time (UTC) | Line | Duration |
|---|---|---|---|
| Fix lost | 2024-06-09 00:07:06 | 592 | 4.0 s |
| Fix regained | 2024-06-09 00:07:10 | 768 | 4.0 s |

## Satellites used

| Source | n | min | mean | median | p95 | max |
|---|---|---|---|---|---|---|
| GGA (all systems) | 16 | 7 | 24.6 | 29.0 | 29.0 | 29 |
| GSA (all systems) | 16 | 7 | 24.6 | 29.0 | 29.0 | 29 |
| GSA: BeiDou | 16 | 3 | 7.8 | 9.0 | 9.0 | 9 |
| GSA: GLONASS | 16 | 0 | 4.1 | 5.0 | 5.0 | 5 |
| GSA: GPS | 16 | 4 | 9.4 | 11.0 | 11.0 | 11 |
| GSA: Galileo | 16 | 0 | 3.3 | 4.0 | 4.0 | 4 |
| GSA: QZSS | 16 | 0 | 0.0 | 0.0 | 0.0 | 0 |

### Satellites used over time

All epochs, 10 s windows from the first epoch, GSA counts:

| Window | min | mean | max |
|---|---|---|---|
| 0–10 s | 28 | 28.9 | 29 |
| 10–20 s | 0 | 10.4 | 27 |

## HDOP (fix epochs)

| n | min | mean | median | p95 | max |
|---|---|---|---|---|---|
| 16 | 0.60 | 1.60 | 0.68 | 4.73 | 10.16 |

## Position accuracy

| Item | Value |
|---|---|
| Reference | mean of the fix positions (44.0688252, -121.3141053) |
| Positions (n) | 16 |
| CEP50 | 0.732 m |
| CEP95 | 2.895 m |
| Largest error | 3.614 m |

- CEP assumes the receiver was static during the log.
- Against the mean position this measures precision (spread), not accuracy against a surveyed point.
- Small sample (n = 16 < 100): CEP95 depends on the few largest errors and is only indicative.

## Definitions

- **Epoch**: one measurement instant; at 1 Hz, the set of sentences for one second.
- **Fix**: a valid position: GGA fix quality 1-5 (GPS, DGPS, PPS, RTK fixed, RTK float). Quality 6 (estimated / dead reckoning) is not a fix.
- **Fix availability**: fix epochs divided by expected epochs. Expected epochs include those missing from data gaps, which count as no fix.
- **TTFF**: time to first fix: from the first epoch in the log to the first fix epoch. The log start stands in for receiver power-on.
- **Fix lost / regained**: a change from fix to no fix, and back. A log that starts without a fix is a cold start, not a loss.
- **Data gap**: a step between epochs longer than 1.5 times the normal interval.
- **HDOP**: horizontal dilution of precision: how much satellite geometry magnifies horizontal error. Lower is better.
- **Satellites used**: GGA's count, and the unique satellites listed in GSA, by constellation.
- **CEP50 / CEP95**: circular error probable: the radius containing 50 % / 95 % of horizontal positions around the reference point.
- **Integrity errors**: lines that fail checksum, framing or field checks, plus log-level problems (time going backwards, duplicates, incomplete GSV). Valid sentences of an unrecognised type are reported but not counted.
- **Percentiles**: linear interpolation between closest ranks.

---
_All values are computed from the log file above by gnsskit 0.1.0._
