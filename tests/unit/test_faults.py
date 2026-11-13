"""Unit tests for the fault injector (gnsskit.faults) and the `gnsskit inject` CLI.

Each test injects faults into a clean real log and checks, through the reader and
analyser, that exactly the expected problems are detected.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from gnsskit.analysis import EventKind, analyse
from gnsskit.cli import main
from gnsskit.faults import FaultPlan, inject
from gnsskit.log import LogIssueKind, read_lines
from gnsskit.nmea import ErrorKind, OtherSentence, parse_sentence

DATA = Path(__file__).resolve().parents[2] / "data" / "real"
NEO = (DATA / "ublox-neo-m9n-nmea.log").read_text().splitlines()  # 61 epochs, 22:37:45-22:38:45
ZED = (DATA / "ublox-zed-f9p-nmea.log").read_text().splitlines()  # 29 epochs
UNICORE = (DATA / "unicore-um621.log").read_text().splitlines()  # 20 epochs, 220 unknown


def counts(lines: list[str]) -> dict:
    return read_lines(lines).issue_counts()


# ------------------------------------------------------------------- general rules


class TestGeneral:
    def test_same_seed_gives_identical_output_and_manifest(self):
        plan = FaultPlan(seed=7, bad_checksums=5, truncated=3, unknown_sentences=2)
        first, second = inject(NEO, plan), inject(NEO, plan)
        assert first.lines == second.lines
        assert first.manifest == second.manifest

    def test_different_seed_picks_different_lines(self):
        a = inject(NEO, FaultPlan(seed=1, bad_checksums=5))
        b = inject(NEO, FaultPlan(seed=2, bad_checksums=5))
        assert a.lines != b.lines

    def test_input_is_not_modified(self):
        original = list(NEO)
        inject(NEO, FaultPlan(seed=1, bad_checksums=5, gaps=((5, 5),)))
        assert NEO == original

    def test_empty_plan_only_adds_the_label_header(self):
        result = inject(ZED, FaultPlan(seed=0), source="zed.log")
        assert result.lines[0].startswith("# FAULT-INJECTED")
        body = [line for line in result.lines if not line.startswith("# FAULT-INJECTED")]
        assert body == ZED

    def test_manifest_is_json_and_records_source_seed_and_line_counts(self):
        result = inject(ZED, FaultPlan(seed=3, bad_checksums=1), source="zed.log")
        manifest = json.loads(json.dumps(result.manifest))
        assert manifest["source"] == "zed.log"
        assert manifest["seed"] == 3
        assert manifest["input_lines"] == len(ZED)
        assert manifest["output_lines"] == len(result.lines)

    def test_manifest_line_numbers_point_at_the_faulty_output_lines(self):
        result = inject(NEO, FaultPlan(seed=4, bad_checksums=6, truncated=4))
        issues = read_lines(result.lines).issues
        for fault, kind in (("bad_checksums", ErrorKind.BAD_CHECKSUM), ("truncated", ErrorKind.TRUNCATED)):
            (entry,) = [f for f in result.manifest["faults"] if f["type"] == fault]
            assert entry["lines"] == sorted(i.line_no for i in issues if i.kind is kind)

    def test_asking_for_more_faults_than_lines_is_a_value_error(self):
        with pytest.raises(ValueError, match="bad_checksums"):
            inject(ZED, FaultPlan(seed=0, bad_checksums=10**6))

    def test_negative_count_is_a_value_error(self):
        with pytest.raises(ValueError):
            inject(ZED, FaultPlan(seed=0, truncated=-1))


# ---------------------------------------------------------------- line-level faults


class TestLineFaults:
    def test_bad_checksums_are_counted_exactly_and_fix_is_unaffected(self):
        result = inject(NEO, FaultPlan(seed=1, bad_checksums=5))
        assert counts(result.lines).get(ErrorKind.BAD_CHECKSUM) == 5
        analysis = analyse(read_lines(result.lines))
        assert analysis.availability_pct == pytest.approx(100.0)
        assert analysis.events == ()

    def test_truncated_lines_are_counted_exactly(self):
        result = inject(ZED, FaultPlan(seed=2, truncated=3))
        assert counts(result.lines).get(ErrorKind.TRUNCATED) == 3

    def test_unknown_sentences_add_to_existing_ones(self):
        result = inject(UNICORE, FaultPlan(seed=3, unknown_sentences=4))
        assert counts(result.lines)[ErrorKind.UNKNOWN_TYPE] == 220 + 4

    def test_garbage_lines_are_non_nmea(self):
        result = inject(ZED, FaultPlan(seed=4, garbage_lines=2))
        assert counts(result.lines) == {ErrorKind.NON_NMEA: 2}

    def test_blanked_positions_are_empty_fields_and_rmc_keeps_the_fix(self):
        result = inject(NEO, FaultPlan(seed=5, blank_positions=3))
        # Exactly 3 EMPTY_FIELD. In u-blox logs the GGA comes first, so losing it also
        # pushes that second's GSV parts into the previous epoch (INCOMPLETE_GSV there).
        assert counts(result.lines)[ErrorKind.EMPTY_FIELD] == 3
        assert analyse(read_lines(result.lines)).availability_pct == pytest.approx(100.0)

    def test_dropped_gsv_parts_are_incomplete_gsv(self):
        result = inject(ZED, FaultPlan(seed=6, dropped_gsv_parts=4))
        assert counts(result.lines) == {LogIssueKind.INCOMPLETE_GSV: 4}
        assert len(result.lines) == len(ZED) + 1 - 4  # +1 header, -4 dropped

    def test_never_corrupts_both_gga_and_rmc_of_one_epoch(self):
        # Heavy corruption of the Unicore log: every epoch must survive.
        result = inject(UNICORE, FaultPlan(seed=8, bad_checksums=40, truncated=40))
        log = read_lines(result.lines)
        assert len(log.epochs) == 20
        assert analyse(log).fix_epochs == 16

    def test_comment_lines_are_never_corrupted(self):
        result = inject(ZED, FaultPlan(seed=9, bad_checksums=100, truncated=100))
        comments = [line for line in ZED if line.startswith("#")]
        assert [line for line in result.lines[1:] if line.startswith("#")] == comments


# --------------------------------------------------------------- time-window faults


class TestOutage:
    def test_thirty_second_outage_gives_one_loss_and_one_regain(self):
        result = inject(NEO, FaultPlan(seed=0, outages=((10, 30),)))
        log = read_lines(result.lines)
        assert log.issues == []  # every modified sentence is valid NMEA
        analysis = analyse(log)
        lost, regained = analysis.events
        assert (lost.kind, lost.time) == (EventKind.FIX_LOST, dt.time(22, 37, 55))
        assert (regained.kind, regained.time) == (EventKind.FIX_REGAINED, dt.time(22, 38, 25))
        assert regained.duration_s == pytest.approx(30.0)
        assert analysis.fix_epochs == 31
        assert analysis.availability_pct == pytest.approx(100 * 31 / 61)

    def test_outage_sets_receiver_outputs_to_no_fix(self):
        result = inject(NEO, FaultPlan(seed=0, outages=((10, 1),)))
        epoch = read_lines(result.lines).epochs[10]
        assert epoch.gga.quality == 0 and epoch.gga.lat is None and epoch.gga.num_sats == 0
        assert epoch.rmc.status == "V" and epoch.rmc.mode == "N" and epoch.rmc.date is not None
        assert all(g.fix_type == 1 and g.prns == () for g in epoch.gsa)
        assert all(s.snr_dbhz is None for g in epoch.gsv_groups for s in g.satellites)

    def test_epochs_outside_the_window_are_untouched(self):
        result = inject(NEO, FaultPlan(seed=0, outages=((10, 30),)))
        clean, faulty = read_lines(NEO).epochs, read_lines(result.lines).epochs
        for i in (9, 40):  # last epoch before and first epoch after the window
            assert (faulty[i].gga, faulty[i].rmc, faulty[i].gsa, faulty[i].gsv_groups) == (
                clean[i].gga, clean[i].rmc, clean[i].gsa, clean[i].gsv_groups
            )

    def test_outage_running_past_end_of_log_is_never_regained(self):
        result = inject(NEO, FaultPlan(seed=0, outages=((50, 100),)))
        (lost,) = analyse(read_lines(result.lines)).events
        assert lost.kind is EventKind.FIX_LOST and lost.duration_s is None

    def test_outage_window_outside_the_log_is_a_value_error(self):
        with pytest.raises(ValueError, match="no epochs"):
            inject(NEO, FaultPlan(seed=0, outages=((500, 10),)))


class TestGap:
    def test_dropped_seconds_give_one_data_gap(self):
        result = inject(NEO, FaultPlan(seed=0, gaps=((20, 10),)))
        log = read_lines(result.lines)
        assert log.issues == []
        analysis = analyse(log)
        (gap,) = analysis.events
        assert gap.kind is EventKind.DATA_GAP
        assert gap.duration_s == pytest.approx(10.0)
        assert analysis.epochs_present == 51
        assert analysis.epochs_expected == 61
        assert analysis.availability_pct == pytest.approx(100 * 51 / 61)


class TestMidnightShift:
    def test_shift_across_midnight_gives_one_rollover_and_continuous_time(self):
        result = inject(NEO, FaultPlan(seed=0, midnight_shift_s=30))
        log = read_lines(result.lines)
        assert log.issues == []
        assert log.rollovers == 1
        assert log.epochs[0].utc == dt.datetime(2020, 7, 11, 23, 59, 30, tzinfo=dt.timezone.utc)
        assert log.epochs[30].utc == dt.datetime(2020, 7, 12, 0, 0, 0, tzinfo=dt.timezone.utc)
        steps = {b.seconds - a.seconds for a, b in zip(log.epochs, log.epochs[1:])}
        assert steps == {1.0}

    def test_shift_updates_rmc_and_zda_dates_after_midnight(self):
        result = inject(NEO, FaultPlan(seed=0, midnight_shift_s=30))
        rmc_dates = {line.split(",")[9] for line in result.lines if line.startswith("$GNRMC")}
        assert rmc_dates == {"110720", "120720"}
        zda = [line for line in result.lines if line.startswith("$GNZDA")]
        assert zda[0].startswith("$GNZDA,235930.00,11,07,2020")
        assert zda[-1].startswith("$GNZDA,000030.00,12,07,2020")
        assert all(isinstance(parse_sentence(line), OtherSentence) for line in zda)

    def test_shifted_sentences_keep_valid_checksums(self):
        result = inject(NEO, FaultPlan(seed=0, midnight_shift_s=30))
        assert read_lines(result.lines).issue_counts() == {}


# ------------------------------------------------------------------ combined + CLI


def test_combined_faults_each_detected_as_the_manifest_expects():
    plan = FaultPlan(
        seed=11, bad_checksums=6, truncated=4, blank_positions=2, unknown_sentences=3,
        garbage_lines=2, dropped_gsv_parts=3, outages=((5, 15),), gaps=((40, 5),),
    )  # fmt: skip
    result = inject(NEO, plan)
    found = counts(result.lines)
    for fault in result.manifest["faults"]:
        expect = fault.get("expect_issue")
        if expect is None:
            continue
        got = found.get(expect["kind"], 0)
        if expect["match"] == "exactly":
            assert got == expect["count"], fault["type"]
        else:
            assert got >= expect["count"], fault["type"]
    events = analyse(read_lines(result.lines)).events
    assert [e.kind for e in events].count(EventKind.FIX_LOST) == 1
    assert [e.kind for e in events].count(EventKind.DATA_GAP) == 1


class TestCli:
    def test_inject_writes_log_and_manifest(self, tmp_path):
        source = DATA / "ublox-neo-m9n-nmea.log"
        out = tmp_path / "faulty.log"
        code = main(["inject", str(source), "-o", str(out), "--seed", "3",
                     "--bad-checksums", "2", "--outage", "10:30"])  # fmt: skip
        assert code == 0
        manifest = json.loads((tmp_path / "faulty.log.manifest.json").read_text())
        assert manifest["seed"] == 3
        assert {f["type"] for f in manifest["faults"]} == {"bad_checksums", "outage"}
        assert counts(out.read_text().splitlines())[ErrorKind.BAD_CHECKSUM] == 2

    def test_impossible_request_exits_with_code_2(self, tmp_path, capsys):
        code = main(["inject", str(DATA / "ublox-zed-f9p-nmea.log"), "-o",
                     str(tmp_path / "x.log"), "--bad-checksums", "1000000"])  # fmt: skip
        assert code == 2
        assert "bad_checksums" in capsys.readouterr().err
        assert not (tmp_path / "x.log").exists()

    def test_malformed_window_is_rejected(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            main(["inject", str(DATA / "ublox-zed-f9p-nmea.log"), "-o",
                  str(tmp_path / "x.log"), "--outage", "10"])  # fmt: skip
        assert exc.value.code == 2


# ------------------------------------------------------- small logs that force choices


def _cs(body: str) -> str:
    from functools import reduce

    return f"${body}*{reduce(lambda a, c: a ^ ord(c), body, 0):02X}"


def tiny_log(epochs: int = 10, with_gsa: bool = False) -> list[str]:
    """Synthetic: one GGA + RMC (+ GSA) per second; built here, not from real data."""
    lines = []
    for i in range(epochs):
        t = f"1200{i:02d}"
        lines.append(_cs(f"GPGGA,{t},4404.12994,N,12118.84613,W,1,08,0.9,100.0,M,,M,,"))
        lines.append(_cs(f"GPRMC,{t},A,4404.12994,N,12118.84613,W,0.0,,090624,,,A"))
        if with_gsa:
            lines.append(_cs("GPGSA,A,3,01,02,03,04,,,,,,,,,1.5,0.9,1.2"))
    return lines


class TestSelectionRules:
    def test_at_most_one_of_gga_and_rmc_is_corrupted_per_epoch(self):
        # 20 GGA/RMC lines, 10 corruptions: only possible with one per epoch.
        result = inject(tiny_log(10), FaultPlan(seed=0, bad_checksums=10))
        log = read_lines(result.lines)
        assert len(log.epochs) == 10
        assert log.issue_counts() == {ErrorKind.BAD_CHECKSUM: 10}

    def test_one_more_than_the_epoch_count_is_impossible(self):
        with pytest.raises(ValueError, match="bad_checksums"):
            inject(tiny_log(10), FaultPlan(seed=0, bad_checksums=11))

    def test_a_line_is_never_given_two_faults(self):
        # 10 GSA + 10 of the GGA/RMC lines are eligible: 20 faults use every one of them.
        plan = FaultPlan(seed=0, bad_checksums=10, truncated=10)
        result = inject(tiny_log(10, with_gsa=True), plan)
        assert counts(result.lines) == {ErrorKind.BAD_CHECKSUM: 10, ErrorKind.TRUNCATED: 10}

    def test_truncated_lines_always_lose_the_checksum(self):
        result = inject(NEO, FaultPlan(seed=0, truncated=200))
        (entry,) = result.manifest["faults"]
        assert all("*" not in result.lines[n - 1] for n in entry["lines"])


class TestValidation:
    @pytest.mark.parametrize(
        "plan, message",
        [
            (FaultPlan(outages=((10, 0),)), "duration > 0"),
            (FaultPlan(gaps=((-1, 5),)), "start >= 0"),
            (FaultPlan(midnight_shift_s=0), "between 0 and 86400"),
            (FaultPlan(gaps=((0, 5),)), "before and after"),
            (FaultPlan(gaps=((55, 100),)), "before and after"),
            (FaultPlan(gaps=((500, 5),)), "no epochs"),
            (FaultPlan(dropped_gsv_parts=10**6), "dropped_gsv_parts"),
        ],
    )
    def test_impossible_plans_are_rejected(self, plan, message):
        with pytest.raises(ValueError, match=message):
            inject(NEO, plan)

    def test_midnight_shift_needs_timed_epochs(self):
        with pytest.raises(ValueError, match="no timed epochs"):
            inject(["$GPGSA,A,1,,,,,,,,,,,,,,,*1E"], FaultPlan(midnight_shift_s=10))


def test_faults_on_unicore_log_leave_its_unknown_sentences_alone():
    # The Unicore log has 220 sentences the parser rejects and an RMC-first order.
    plan = FaultPlan(seed=0, midnight_shift_s=10, outages=((2, 3),))
    log = read_lines(inject(UNICORE, plan).lines)
    assert log.issue_counts() == {ErrorKind.UNKNOWN_TYPE: 220}
    assert log.rollovers == 1
    assert [e.kind for e in analyse(log).events].count(EventKind.FIX_LOST) == 2


def test_single_part_gsv_messages_are_never_dropped():
    # Dropping the only part of a GSV message removes the whole group: undetectable.
    # Synthetic: each epoch has one single-part and one two-part GSV message.
    lines = []
    for line in tiny_log(10):
        lines.append(line)
        if line.startswith("$GPGGA"):
            lines.append(_cs("GLGSV,1,1,01,65,10,100,30"))
            lines.append(_cs("GPGSV,2,1,05,01,10,100,30,02,20,200,31,03,30,300,32,04,40,040,33"))
            lines.append(_cs("GPGSV,2,2,05,05,50,050,20"))
    result = inject(lines, FaultPlan(seed=0, dropped_gsv_parts=10))
    assert counts(result.lines) == {LogIssueKind.INCOMPLETE_GSV: 10}
    with pytest.raises(ValueError, match="dropped_gsv_parts"):
        inject(lines, FaultPlan(seed=0, dropped_gsv_parts=11))
