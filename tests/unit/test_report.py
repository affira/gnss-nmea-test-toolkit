"""Unit tests for the Markdown report (gnsskit.report) and the `gnsskit report` CLI."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

import pytest

from gnsskit.analysis import analyse
from gnsskit.cli import main
from gnsskit.faults import FaultPlan, inject
from gnsskit.log import read_lines
from gnsskit.profile import DEFAULT_PROFILE, evaluate, load_profile, with_overrides
from gnsskit.report import build_report

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "data" / "real"
SYN = ROOT / "data" / "synthetic"
WHEN = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


def report_for(path: Path, profile=DEFAULT_PROFILE, reference=None, lines=None) -> str:
    lines = lines if lines is not None else path.read_text().splitlines()
    log = read_lines(lines)
    analysis = analyse(log, reference=reference)
    return build_report(
        log_name=path.name,
        sha256=hashlib.sha256("\n".join(lines).encode()).hexdigest(),
        header_lines=[line for line in lines if line.startswith("#")],
        log=log,
        analysis=analysis,
        evaluation=evaluate(log, analysis, profile),
        generated_at=WHEN,
    )


def verdict_section(report: str) -> str:
    start = report.index("## Verdict")
    end = report.index("\n## ", start + 1)
    return report[start:end]


def criterion_row(report: str, label: str) -> str:
    """The row for `label` in the verdict table (labels also appear in later sections)."""
    rows = [line for line in verdict_section(report).splitlines() if line.startswith(f"| {label} |")]
    assert len(rows) == 1, f"expected one row for {label}"
    return rows[0]


# ----------------------------------------------------------------------- verdict


class TestVerdictSection:
    def test_unicore_report_fails_on_availability_with_criteria_shown(self):
        report = report_for(REAL / "unicore-um621.log")
        assert "## Verdict: FAIL" in report
        row = criterion_row(report, "Fix availability")
        assert "80.0 %" in row and "≥ 95.0 %" in row and "standard-gnss" in row and "FAIL" in row

    def test_each_criterion_names_its_threshold_and_profile(self):
        report = report_for(REAL / "unicore-um621.log")
        expected = {
            "Fix availability": "≥ 95.0 %",
            "Time to first fix": "≤ 60.0 s",
            "HDOP, 95th percentile": "≤ 2.00",
            "CEP95": "≤ 5.00 m",
            "Integrity errors": "≤ 0",
        }
        for label, threshold in expected.items():
            row = criterion_row(report, label)
            assert threshold in row, row
            assert "standard-gnss" in row, row

    def test_not_measurable_criterion_explains_why(self):
        row = criterion_row(report_for(REAL / "unicore-um621.log"), "Time to first fix")
        assert "NOT MEASURABLE" in row and "starts with a fix" in row

    def test_profile_is_named_with_source_and_illustrative_note(self):
        profile = load_profile(ROOT / "profiles" / "high-precision.yaml")
        report = report_for(SYN / "SYNTHETIC-static-10min.log", profile=profile)
        assert "**high-precision**" in report
        assert "`high-precision.yaml`" in report
        assert "illustrative example values" in report.lower()
        assert "not taken from any product datasheet" in report.lower()
        assert "high-precision" in criterion_row(report, "CEP95")

    def test_cli_override_is_marked_on_its_row_only(self):
        profile = with_overrides(DEFAULT_PROFILE, {"max_cep95_m": 3.0})
        report = report_for(SYN / "SYNTHETIC-static-10min.log", profile=profile)
        assert "command-line override" in criterion_row(report, "CEP95")
        assert "≤ 3.00 m" in criterion_row(report, "CEP95")
        assert "command-line override" not in criterion_row(report, "Fix availability")

    def test_disabled_criterion_is_not_checked(self):
        profile = with_overrides(DEFAULT_PROFILE, {"max_ttff_s": None})
        report = report_for(SYN / "SYNTHETIC-static-10min.log", profile=profile)
        assert "## Verdict: PASS" in report
        row = criterion_row(report, "Time to first fix")
        assert "NOT CHECKED" in row and "disabled" in row

    def test_incomplete_verdict_is_explained(self):
        report = report_for(SYN / "SYNTHETIC-static-10min.log")
        assert "## Verdict: INCOMPLETE" in report
        assert "could not be measured" in report


# ------------------------------------------------------------------ other sections


class TestSections:
    def test_header_identifies_the_log(self):
        report = report_for(REAL / "ublox-zed-f9p-nmea.log")
        assert report.startswith("# GNSS log test report: ublox-zed-f9p-nmea.log")
        assert re.search(r"SHA-256 \| `[0-9a-f]{64}`", report)
        assert "2026-01-02 03:04:05 UTC" in report

    def test_synthetic_log_is_flagged(self):
        report = report_for(SYN / "SYNTHETIC-cold-start.log")
        assert "**SYNTHETIC LOG**" in report

    def test_fault_injected_log_is_flagged(self):
        lines = inject(REAL.joinpath("ublox-zed-f9p-nmea.log").read_text().splitlines(),
                       FaultPlan(seed=1, bad_checksums=2)).lines  # fmt: skip
        report = report_for(Path("faulty.log"), lines=lines)
        assert "**FAULT-INJECTED LOG**" in report
        assert "| bad_checksum | 2 | yes |" in report

    def test_real_log_is_not_flagged(self):
        report = report_for(REAL / "ublox-zed-f9p-nmea.log")
        assert "SYNTHETIC LOG" not in report and "FAULT-INJECTED LOG" not in report

    def test_integrity_table_separates_unknown_types(self):
        report = report_for(REAL / "unicore-um621.log")
        assert "| unknown_type | 220 | no (unrecognised sentence type) |" in report
        assert "| Line | Problem | Detail |" in report  # examples listed

    def test_clean_log_says_no_problems(self):
        assert "No problems found." in report_for(REAL / "ublox-zed-f9p-nmea.log")

    def test_event_table_lists_the_real_outage(self):
        report = report_for(REAL / "unicore-um621.log")
        assert re.search(r"\| Fix lost \| 2024-06-09 00:07:06 \| \d+ \| 4\.0 s \|", report)
        assert re.search(r"\| Fix regained \| 2024-06-09 00:07:10 \| \d+ \| 4\.0 s \|", report)

    def test_no_events_sentence(self):
        assert "No fix-lost, fix-regained or data-gap events." in report_for(
            REAL / "ublox-zed-f9p-nmea.log"
        )

    def test_gga_cap_note_on_ublox_log(self):
        report = report_for(REAL / "ublox-neo-m9n-nmea.log")
        assert "GGA reports at most 12" in report

    def test_no_gga_cap_note_when_counts_agree(self):
        assert "GGA reports at most" not in report_for(SYN / "SYNTHETIC-static-10min.log")

    def test_satellites_by_constellation_and_over_time(self):
        report = report_for(SYN / "SYNTHETIC-static-10min.log")
        assert re.search(r"\| GSA: GPS \| 600 \| 7 \|", report)
        assert "### Satellites used over time" in report
        # 600 s in 60 s windows
        assert len(re.findall(r"^\| \d+–\d+ s \|", report, flags=re.M)) == 10

    def test_small_sample_cep_warning(self):
        assert "small sample" in report_for(REAL / "ublox-zed-f9p-nmea.log").lower()
        assert "small sample" not in report_for(SYN / "SYNTHETIC-static-10min.log").lower()

    def test_reference_point_is_described(self):
        mean = report_for(SYN / "SYNTHETIC-static-10min.log")
        assert "mean of the fix positions" in mean
        given = report_for(SYN / "SYNTHETIC-static-10min.log", reference=(51.477928, -0.001545))
        assert "given reference point 51.4779280, -0.0015450" in given

    def test_definitions_section(self):
        report = report_for(REAL / "ublox-zed-f9p-nmea.log")
        for term in ("**TTFF**", "**CEP50 / CEP95**", "**HDOP**", "**Fix**"):
            assert term in report

    def test_empty_log_report_does_not_crash(self):
        report = report_for(Path("empty.log"), lines=[])
        assert "## Verdict: INCOMPLETE" in report
        assert "No problems found." in report

    def test_report_is_deterministic(self):
        path = REAL / "unicore-um621.log"
        assert report_for(path) == report_for(path)


# --------------------------------------------------------------------------- CLI


class TestCli:
    def test_writes_report_and_exits_with_verdict_code(self, tmp_path):
        out = tmp_path / "r.md"
        code = main(["report", str(SYN / "SYNTHETIC-cold-start.log"), "-o", str(out)])
        assert code == 1  # FAIL: availability 75 %
        assert "## Verdict: FAIL" in out.read_text()

    def test_pass_exit_code_zero(self, tmp_path):
        code = main(["report", str(SYN / "SYNTHETIC-static-10min.log"), "-o",
                     str(tmp_path / "r.md"), "--max-ttff", "none"])  # fmt: skip
        assert code == 0

    def test_incomplete_exit_code_three(self, tmp_path):
        code = main(["report", str(SYN / "SYNTHETIC-static-10min.log"), "-o", str(tmp_path / "r.md")])
        assert code == 3

    def test_profile_file_and_override_flags(self, tmp_path):
        out = tmp_path / "r.md"
        main(["report", str(SYN / "SYNTHETIC-static-10min.log"), "-o", str(out),
              "--profile", str(ROOT / "profiles" / "high-precision.yaml"),
              "--max-cep95", "3", "--ref", "51.477928,-0.001545"])  # fmt: skip
        text = out.read_text()
        assert "**high-precision**" in text
        row = criterion_row(text, "CEP95")
        assert "≤ 3.00 m" in row and "command-line override" in row
        assert "given reference point" in text

    def test_report_to_stdout(self, capsys):
        main(["report", str(REAL / "ublox-zed-f9p-nmea.log")])
        assert capsys.readouterr().out.startswith("# GNSS log test report")

    def test_bad_profile_exits_2(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: x\n")
        code = main(["report", str(REAL / "ublox-zed-f9p-nmea.log"), "--profile", str(bad)])
        assert code == 2
        assert "gnsskit report: error" in capsys.readouterr().err

    def test_missing_log_exits_2(self, tmp_path):
        assert main(["report", str(tmp_path / "missing.log")]) == 2

    @pytest.mark.parametrize("ref", ["51.4", "91,0", "0,181", "a,b"])
    def test_bad_reference_is_rejected(self, ref):
        with pytest.raises(SystemExit) as exc:
            main(["report", str(REAL / "ublox-zed-f9p-nmea.log"), "--ref", ref])
        assert exc.value.code == 2

    @pytest.mark.parametrize("value", ["-1", "abc"])
    def test_bad_threshold_flag_is_rejected(self, value, capsys):
        try:
            code = main(["report", str(REAL / "ublox-zed-f9p-nmea.log"), "--max-ttff", value])
        except SystemExit as exc:
            code = exc.code
        assert code == 2


def test_failed_criteria_are_listed_unambiguously():
    # "HDOP, 95th percentile" contains a comma, so the list uses semicolons.
    report = report_for(REAL / "unicore-um621.log")
    assert "2 criteria failed: Fix availability; HDOP, 95th percentile." in report


def test_built_in_profile_is_described():
    report = report_for(REAL / "unicore-um621.log")
    assert "**standard-gnss** (built in, same values as `profiles/standard-gnss.yaml`)" in report
