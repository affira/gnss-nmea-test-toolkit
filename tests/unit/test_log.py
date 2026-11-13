"""Unit tests for the log reader (gnsskit.log): epochs, day rollover, integrity issues.

Expected values for the real logs were checked independently with grep (see the
step 2 notes in the commit message), not taken from the code under test.
"""

from __future__ import annotations

import datetime as dt
import random
from functools import reduce
from pathlib import Path

import pytest

from gnsskit.log import Epoch, Issue, LogIssueKind, LogReadResult, read_lines, read_log
from gnsskit.nmea import ErrorKind, SatelliteInView

DATA = Path(__file__).resolve().parents[2] / "data" / "real"
UTC = dt.timezone.utc


def cs(body: str) -> str:
    checksum = reduce(lambda acc, ch: acc ^ ord(ch), body, 0)
    return f"${body}*{checksum:02X}"


def gga(time: str, quality: int = 1) -> str:
    return cs(f"GPGGA,{time},4404.12994,N,12118.84613,W,{quality},08,0.9,100.0,M,,M,,")


def rmc(time: str, date: str, status: str = "A") -> str:
    return cs(f"GPRMC,{time},{status},4404.12994,N,12118.84613,W,0.0,,{date},,,A")


GSA = cs("GNGSA,A,3,01,02,03,04,,,,,,,,,1.5,0.9,1.2,1")


def kinds(result: LogReadResult) -> list:
    return [issue.kind for issue in result.issues]


# --------------------------------------------------------------------------- grouping


class TestGrouping:
    def test_sentences_with_same_time_form_one_epoch(self):
        result = read_lines([gga("120000"), GSA, rmc("120000", "010124")])
        assert len(result.epochs) == 1
        epoch = result.epochs[0]
        assert epoch.time == dt.time(12, 0, 0)
        assert epoch.gga is not None and epoch.rmc is not None
        assert len(epoch.gsa) == 1
        assert result.issues == []

    def test_new_time_starts_new_epoch(self):
        result = read_lines([gga("120000"), GSA, gga("120001"), GSA])
        assert [e.time for e in result.epochs] == [dt.time(12, 0, 0), dt.time(12, 0, 1)]
        assert all(len(e.gsa) == 1 for e in result.epochs)

    def test_rmc_first_order_groups_correctly(self):
        # unicore-um621.log order: RMC, GGA, GSA..., GSV..., then next RMC.
        lines = [rmc("120000", "010124"), gga("120000"), GSA, rmc("120001", "010124"), gga("120001")]
        result = read_lines(lines)
        assert len(result.epochs) == 2
        assert all(e.gga and e.rmc for e in result.epochs)

    def test_epoch_records_first_line_number(self):
        result = read_lines(["# header", gga("120000"), GSA, gga("120001")])
        assert [e.first_line for e in result.epochs] == [2, 4]

    def test_untimed_no_fix_ggas_each_form_an_epoch(self):
        # Cold start: receivers often output GGA with no time until time is known.
        empty = cs("GPGGA,,,,,,0,,,,,,,,")
        result = read_lines([empty, GSA, empty, GSA, empty, gga("120000")])
        assert len(result.epochs) == 4
        assert [e.time for e in result.epochs[:3]] == [None, None, None]
        assert [e.seconds for e in result.epochs[:3]] == [None, None, None]
        assert result.epochs[3].time == dt.time(12, 0, 0)
        assert result.issues == []

    def test_untimed_gga_and_rmc_in_one_cycle_share_an_epoch(self):
        lines = [cs("GPGGA,,,,,,0,,,,,,,,"), cs("GPRMC,,V,,,,,,,,,,N"), GSA]
        result = read_lines(lines)
        assert len(result.epochs) == 1
        assert result.epochs[0].gga is not None and result.epochs[0].rmc is not None

    def test_duplicate_gga_in_one_epoch_keeps_first_and_reports(self):
        first = gga("120000", quality=1)
        second = gga("120000", quality=2)
        result = read_lines([first, second])
        assert len(result.epochs) == 1
        assert result.epochs[0].gga.quality == 1
        assert kinds(result) == [LogIssueKind.DUPLICATE_SENTENCE]
        assert result.issues[0].line_no == 2

    def test_sentence_before_any_epoch_is_orphan(self):
        result = read_lines([GSA, gga("120000")])
        assert kinds(result) == [LogIssueKind.ORPHAN_SENTENCE]
        assert result.issues[0].line_no == 1
        assert len(result.epochs) == 1 and result.epochs[0].gsa == ()


# --------------------------------------------------------------------------- dates


class TestDatesAndRollover:
    def test_rmc_date_at_end_of_epoch_applies_to_that_epoch(self):
        # u-blox order: GGA first, RMC (with the date) last.
        result = read_lines([gga("120000"), GSA, rmc("120000", "120419")])
        epoch = result.epochs[0]
        assert epoch.date == dt.date(2019, 4, 12)
        assert epoch.utc == dt.datetime(2019, 4, 12, 12, 0, 0, tzinfo=UTC)

    def test_date_propagates_forward_and_backward(self):
        lines = [gga("120000"), gga("120001"), rmc("120001", "120419"), gga("120002")]
        result = read_lines(lines)
        assert [e.date for e in result.epochs] == [dt.date(2019, 4, 12)] * 3

    def test_without_any_date_utc_is_none_but_seconds_work(self):
        result = read_lines([gga("120000"), gga("120001")])
        assert [e.date for e in result.epochs] == [None, None]
        assert [e.utc for e in result.epochs] == [None, None]
        assert [e.seconds for e in result.epochs] == [43200.0, 43201.0]

    def test_rollover_with_rmc_dates(self):
        lines = [
            gga("235959"), rmc("235959", "311224"),
            gga("000000"), rmc("000000", "010125"),
        ]  # fmt: skip
        result = read_lines(lines)
        first, second = result.epochs
        assert (first.day, second.day) == (0, 1)
        assert first.utc == dt.datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)
        assert second.utc == dt.datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert second.seconds - first.seconds == 1.0
        assert result.rollovers == 1
        assert result.issues == []

    def test_rollover_inferred_without_dates(self):
        times = ["235958", "235959.5", "000000", "000001"]
        result = read_lines([gga(t) for t in times])
        assert [e.day for e in result.epochs] == [0, 0, 1, 1]
        seconds = [e.seconds for e in result.epochs]
        assert seconds == sorted(seconds)
        assert seconds[2] - seconds[1] == pytest.approx(0.5)
        assert result.rollovers == 1
        assert result.issues == []

    def test_rollover_inferred_then_date_backfilled_across_midnight(self):
        # RMC only appears after midnight; the epochs before midnight get the previous day.
        lines = [gga("235959"), gga("000000"), rmc("000000", "010125")]
        result = read_lines(lines)
        assert [e.date for e in result.epochs] == [dt.date(2024, 12, 31), dt.date(2025, 1, 1)]

    def test_two_rollovers_in_a_multi_day_log(self):
        times = ["235959", "000000", "120000", "235959", "000000"]
        result = read_lines([gga(t) for t in times])
        assert [e.day for e in result.epochs] == [0, 1, 1, 1, 2]
        assert result.rollovers == 2

    def test_small_backward_jump_is_time_backwards_not_rollover(self):
        result = read_lines([gga("120005"), gga("120003")])
        assert [e.day for e in result.epochs] == [0, 0]
        assert result.rollovers == 0
        assert kinds(result) == [LogIssueKind.TIME_BACKWARDS]
        assert result.issues[0].line_no == 2

    def test_backward_time_on_same_rmc_date_is_time_backwards(self):
        lines = [rmc("120005", "010124"), rmc("010000", "010124")]
        result = read_lines(lines)
        assert result.rollovers == 0
        assert kinds(result) == [LogIssueKind.TIME_BACKWARDS]

    def test_date_advancing_while_time_moves_forward_is_date_jump(self):
        # A new day must come with the time of day wrapping back past midnight.
        lines = [rmc("120000", "010124"), rmc("120001", "020124")]
        result = read_lines(lines)
        assert kinds(result) == [LogIssueKind.DATE_JUMP]
        assert result.rollovers == 0
        assert [e.day for e in result.epochs] == [0, 0]

    def test_implausible_date_jump_is_reported_and_ignored(self):
        lines = [rmc("120000", "010124"), rmc("120001", "110124")]  # +10 days in 1 s
        result = read_lines(lines)
        assert kinds(result) == [LogIssueKind.DATE_JUMP]
        assert [e.day for e in result.epochs] == [0, 0]
        assert result.epochs[1].seconds - result.epochs[0].seconds == 1.0


# ------------------------------------------------------------------------------ GSV


class TestGsvAssembly:
    def test_multi_part_gsv_is_assembled(self):
        lines = [
            gga("120000"),
            cs("GPGSV,2,1,05,01,10,100,30,02,20,200,31,03,30,300,32,04,40,040,33,1"),
            cs("GPGSV,2,2,05,05,50,050,,1"),
        ]
        group = read_lines(lines).epochs[0].gsv_groups[0]
        assert (group.talker, group.signal_id, group.complete) == ("GP", 1, True)
        assert [s.prn for s in group.satellites] == [1, 2, 3, 4, 5]
        assert group.satellites[4] == SatelliteInView(5, 50, 50, None)

    def test_groups_are_split_by_talker_and_signal(self):
        lines = [
            gga("120000"),
            cs("GPGSV,1,1,01,01,10,100,30,1"),
            cs("GPGSV,1,1,01,01,10,100,25,7"),
            cs("GLGSV,1,1,01,65,10,100,28,1"),
        ]
        groups = read_lines(lines).epochs[0].gsv_groups
        assert [(g.talker, g.signal_id) for g in groups] == [("GP", 1), ("GP", 7), ("GL", 1)]
        assert all(g.complete for g in groups)

    def test_missing_part_is_incomplete_gsv(self):
        lines = [
            gga("120000"),
            cs("GPGSV,3,1,09,01,10,100,30,02,20,200,31,03,30,300,32,04,40,040,33"),
            cs("GPGSV,3,3,09,09,50,050,20"),
            gga("120001"),
        ]
        result = read_lines(lines)
        assert result.epochs[0].gsv_groups[0].complete is False
        assert kinds(result) == [LogIssueKind.INCOMPLETE_GSV]

    def test_satellite_count_disagreeing_with_sats_in_view_is_incomplete(self):
        lines = [gga("120000"), cs("GPGSV,1,1,03,01,10,100,30,02,20,200,31")]
        result = read_lines(lines)
        assert result.epochs[0].gsv_groups[0].complete is False
        assert kinds(result) == [LogIssueKind.INCOMPLETE_GSV]


# --------------------------------------------------------------------- bad input


class TestBadInput:
    def test_parse_errors_become_issues_with_line_numbers(self):
        good = gga("120001")
        bad_checksum = good[:-2] + ("00" if not good.endswith("00") else "01")
        lines = [
            "# comment",
            "",
            gga("120000"),
            bad_checksum,
            "$GPGGA,1200",
            cs("GPXYZ,1"),
        ]
        result = read_lines(lines)
        assert [(i.line_no, i.kind) for i in result.issues] == [
            (4, ErrorKind.BAD_CHECKSUM),
            (5, ErrorKind.TRUNCATED),
            (6, ErrorKind.UNKNOWN_TYPE),
        ]
        assert result.comment_lines == 1
        assert result.blank_lines == 1
        assert result.line_count == 6
        assert len(result.epochs) == 1

    def test_issue_counts_by_kind(self):
        lines = ["$GPGGA,1", "$GPGGA,2", "junk", gga("120000"), gga("120000")]
        counts = read_lines(lines).issue_counts()
        assert counts == {
            ErrorKind.TRUNCATED: 2,
            ErrorKind.NON_NMEA: 1,
            LogIssueKind.DUPLICATE_SENTENCE: 1,
        }

    def test_issues_are_sorted_by_line_number(self):
        # The incomplete GSV (line 2) is only detected when its epoch closes at line 4,
        # after the bad line 3 was already reported.
        lines = [gga("120000"), cs("GPGSV,2,1,01,01,10,100,30"), "junk", gga("120001")]
        result = read_lines(lines)
        assert [(i.line_no, i.kind) for i in result.issues] == [
            (2, LogIssueKind.INCOMPLETE_GSV),
            (3, ErrorKind.NON_NMEA),
        ]

    def test_long_offending_line_is_shortened_in_issue(self):
        result = read_lines(["x" * 5000])
        assert len(result.issues[0].line) <= 120

    def test_invalid_utf8_bytes_in_file_do_not_crash(self, tmp_path):
        path = tmp_path / "binary.log"
        path.write_bytes(b"\xff\xfe\x00garbage\n" + gga("120000").encode() + b"\r\n")
        result = read_log(path)
        assert kinds(result) == [ErrorKind.NON_NMEA]
        assert len(result.epochs) == 1

    def test_crlf_line_endings(self, tmp_path):
        path = tmp_path / "crlf.log"
        path.write_bytes((gga("120000") + "\r\n" + gga("120001") + "\r\n").encode())
        assert len(read_log(path).epochs) == 2

    def test_file_without_trailing_newline(self, tmp_path):
        path = tmp_path / "no-newline.log"
        path.write_text(gga("120000") + "\n" + gga("120001"))
        result = read_log(path)
        assert len(result.epochs) == 2 and result.line_count == 2

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.log"
        path.write_bytes(b"")
        result = read_log(path)
        assert result.epochs == [] and result.issues == [] and result.line_count == 0

    @pytest.mark.parametrize("seed", range(3))
    def test_reader_never_raises_on_shuffled_and_corrupted_real_log(self, seed):
        rng = random.Random(seed)
        lines = (DATA / "unicore-um621.log").read_text().splitlines()
        rng.shuffle(lines)
        for i in rng.sample(range(len(lines)), 100):
            pos = rng.randrange(len(lines[i]) + 1)
            lines[i] = lines[i][:pos]
        result = read_lines(lines)
        assert isinstance(result, LogReadResult)
        assert all(isinstance(issue, Issue) for issue in result.issues)
        assert all(isinstance(epoch, Epoch) for epoch in result.epochs)


# ---------------------------------------------------------------------- real logs


REAL = {
    # file: (epochs, first time, last time, date, comment lines)
    "unicore-um621.log": (20, dt.time(0, 6, 53), dt.time(0, 7, 12), dt.date(2024, 6, 9), 17),
    "ublox-zed-f9p-nmea.log": (29, dt.time(0, 39, 56), dt.time(0, 40, 24), dt.date(2019, 4, 12), 12),
    "ublox-neo-m9n-nmea.log": (61, dt.time(22, 37, 45), dt.time(22, 38, 45), dt.date(2020, 7, 11), 11),
}


@pytest.mark.parametrize("log", REAL)
def test_real_log_epochs(log):
    n_epochs, first, last, date, comments = REAL[log]
    result = read_log(DATA / log)
    assert len(result.epochs) == n_epochs
    assert result.epochs[0].time == first
    assert result.epochs[-1].time == last
    assert {e.date for e in result.epochs} == {date}
    assert result.comment_lines == comments
    assert result.rollovers == 0
    assert all(e.gga is not None for e in result.epochs)
    steps = [b.seconds - a.seconds for a, b in zip(result.epochs, result.epochs[1:])]
    assert steps == [1.0] * (n_epochs - 1)


@pytest.mark.parametrize("log", ["ublox-zed-f9p-nmea.log", "ublox-neo-m9n-nmea.log"])
def test_real_ublox_logs_have_no_integrity_issues(log):
    result = read_log(DATA / log)
    assert result.issues == []
    assert all(g.complete for e in result.epochs for g in e.gsv_groups)


def test_real_unicore_log_issues_are_only_its_documented_invalid_sentences():
    # 20 x $SNRSTAT + 200 x $GYOACC (counted with grep).
    result = read_log(DATA / "unicore-um621.log")
    assert result.issue_counts() == {ErrorKind.UNKNOWN_TYPE: 220}


def test_real_unicore_log_fix_quality_sequence():
    result = read_log(DATA / "unicore-um621.log")
    qualities = [e.gga.quality for e in result.epochs]
    assert qualities == [1] * 13 + [0] * 4 + [1] * 3
