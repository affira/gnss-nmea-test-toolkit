# Prompt 1: plan and data

I'm building a small portfolio project to show GNSS test skills for a Test Engineer role at a GNSS receiver company. Python 3.11+, pytest and Robot Framework.

Goal: a toolkit that parses recorded NMEA 0183 logs from a GNSS receiver, checks their integrity, and produces a test performance report, with a solid automated test suite around it.

First, before any code:
1. Create a CLAUDE.md with working rules: tests before or with every feature, the full suite runs after each step, small commits after each green step, never weaken a failing test, no invented data or results, and ask before large changes.
2. Find 2–3 real recorded NMEA logs with a licence that allows reuse (for example, check gpsd's regression test logs and confirm the licence). Record each source and licence in data/SOURCES.md. If you can't confirm a licence, don't use that log. Synthetic logs are fine only if they're clearly labelled synthetic.
3. Propose a plan covering:
   - the parser for GGA, RMC, GSA and GSV, including multi-constellation talker IDs (GP, GL, GA, GB, GN) and checksum validation
   - robust handling of bad input: wrong checksum, truncated lines, empty fields, unknown sentence types and a UTC day rollover. Never crash; count and report the problems
   - a log analyser: time to first fix, fix availability %, fix-lost and fix-regained events, satellites used over time, HDOP statistics, and for static logs CEP50/CEP95 against the mean position or a given reference point
   - a fault-injection tool that corrupts a clean log in controlled ways (bad checksums, dropped sentences, a simulated signal outage) so the tests can prove the analyser detects each fault
   - a CLI that writes a Markdown test report from a log
   - tests: pytest unit tests for parsing, edge cases and the analyser, plus Robot Framework acceptance tests in Given/When/Then style, for example "Given a static log with a 30-second outage, When it is analysed, Then one fix-lost event is reported"
   - GitHub Actions CI that runs both test suites
Explain each GNSS term the plan uses in one line, so I can learn it. Stop and wait for my approval.
