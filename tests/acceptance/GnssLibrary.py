"""Robot Framework keywords for gnsskit acceptance tests (Given/When/Then style).

Robot strips the Given/When/Then/And prefixes, so keywords are written without them.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from robot.api.deco import keyword, library

from gnsskit.analysis import EventKind, analyse
from gnsskit.faults import FaultPlan, inject
from gnsskit.log import read_lines

DATA = Path(__file__).resolve().parents[2] / "data" / "real"


@library(scope="TEST", auto_keywords=False)
class GnssLibrary:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.plan = FaultPlan(seed=1)
        self.injected = False
        self.manifest: dict | None = None
        self.log = None
        self.analysis = None

    # ------------------------------------------------------------------ Given

    @keyword('the recorded log "${name}"')
    def the_recorded_log(self, name: str) -> None:
        self.lines = (DATA / name).read_text().splitlines()

    @keyword("a ${seconds:\\d+}-second outage starting at ${start:\\d+} s")
    def outage(self, seconds: int, start: int) -> None:
        self._add(outages=self.plan.outages + ((float(start), float(seconds)),))

    @keyword("${seconds:\\d+} seconds of data removed starting at ${start:\\d+} s")
    def gap(self, seconds: int, start: int) -> None:
        self._add(gaps=self.plan.gaps + ((float(start), float(seconds)),))

    @keyword("${n:\\d+} corrupted checksums")
    def bad_checksums(self, n: int) -> None:
        self._add(bad_checksums=int(n))

    @keyword("${n:\\d+} truncated lines")
    def truncated(self, n: int) -> None:
        self._add(truncated=int(n))

    @keyword("${n:\\d+} inserted unknown sentences")
    def unknown(self, n: int) -> None:
        self._add(unknown_sentences=int(n))

    @keyword("${n:\\d+} inserted garbage lines")
    def garbage(self, n: int) -> None:
        self._add(garbage_lines=int(n))

    @keyword("${n:\\d+} dropped GSV parts")
    def dropped_gsv(self, n: int) -> None:
        self._add(dropped_gsv_parts=int(n))

    @keyword("${n:\\d+} positions blanked")
    def blank_positions(self, n: int) -> None:
        self._add(blank_positions=int(n))

    @keyword("its times shifted to start ${seconds:\\d+} seconds before midnight")
    def midnight(self, seconds: int) -> None:
        self._add(midnight_shift_s=float(seconds))

    def _add(self, **changes) -> None:
        self.plan = replace(self.plan, **changes)
        self.injected = True

    # ------------------------------------------------------------------- When

    @keyword("the log is analysed")
    def analysed(self) -> None:
        lines = self.lines
        if self.injected:
            result = inject(lines, self.plan, source="acceptance")
            lines, self.manifest = result.lines, result.manifest
        self.log = read_lines(lines)
        self.analysis = analyse(self.log)

    # ------------------------------------------------------------------- Then

    def _events(self, kind: EventKind) -> list:
        return [e for e in self.analysis.events if e.kind is kind]

    @keyword("${n:\\d+} fix-lost ${_noun:events?} reported")
    def fix_lost_events(self, n: int, _noun: str) -> None:
        _equal(len(self._events(EventKind.FIX_LOST)), int(n), "fix-lost events")

    @keyword("${n:\\d+} fix-regained ${_noun:events?} reported")
    def fix_regained_events(self, n: int, _noun: str) -> None:
        _equal(len(self._events(EventKind.FIX_REGAINED)), int(n), "fix-regained events")

    @keyword("the outage lasts ${seconds:\\d+} seconds")
    def outage_lasts(self, seconds: int) -> None:
        (regained,) = self._events(EventKind.FIX_REGAINED)
        _equal(regained.duration_s, float(seconds), "outage duration (s)")

    @keyword("${n:\\d+} data ${_noun:gaps?} of ${seconds:\\d+} seconds reported")
    def data_gaps(self, n: int, _noun: str, seconds: int) -> None:
        gaps = self._events(EventKind.DATA_GAP)
        _equal(len(gaps), int(n), "data gaps")
        for gap in gaps:
            _equal(gap.duration_s, float(seconds), "data gap duration (s)")

    @keyword("fix availability is ${pct:[\\d.]+} %")
    def availability(self, pct: str) -> None:
        _equal(f"{self.analysis.availability_pct:.1f}", f"{float(pct):.1f}", "availability %")

    @keyword('${n:\\d+} "${kind}" ${_noun:problems?} reported')
    def problems(self, n: int, kind: str, _noun: str) -> None:
        counts = {str(k): v for k, v in self.log.issue_counts().items()}
        _equal(counts.get(kind, 0), int(n), f'"{kind}" problems (all: {counts})')

    @keyword("no integrity problems reported")
    def no_problems(self) -> None:
        _equal(self.log.issue_counts(), {}, "integrity problems")

    @keyword("${n:\\d+} UTC day ${_noun:rollovers?} reported")
    def rollovers(self, n: int, _noun: str) -> None:
        _equal(self.log.rollovers, int(n), "day rollovers")

    @keyword("all ${n:\\d+} epochs are 1 second apart")
    def epochs_one_second_apart(self, n: int) -> None:
        epochs = self.log.epochs
        _equal(len(epochs), int(n), "epochs")
        steps = {b.seconds - a.seconds for a, b in zip(epochs, epochs[1:])}
        _equal(steps, {1.0}, "epoch steps (s)")

    @keyword("every fault in the manifest is detected")
    def every_fault_detected(self) -> None:
        counts = {str(k): v for k, v in self.log.issue_counts().items()}
        for fault in self.manifest["faults"]:
            expect = fault.get("expect_issue")
            if expect:
                got = counts.get(expect["kind"], 0)
                ok = got == expect["count"] if expect["match"] == "exactly" else got >= expect["count"]
                if not ok:
                    raise AssertionError(
                        f"{fault['type']}: expected {expect['match']} {expect['count']} "
                        f"{expect['kind']}, analyser reported {got}"
                    )
            if fault.get("expect_data_gaps"):
                _at_least(len(self._events(EventKind.DATA_GAP)), fault["expect_data_gaps"], "data gaps")
            if fault.get("expect_fix_lost"):
                _at_least(len(self._events(EventKind.FIX_LOST)), 1, "fix-lost events")


def _equal(actual, expected, what: str) -> None:
    if actual != expected:
        raise AssertionError(f"{what}: expected {expected}, got {actual}")


def _at_least(actual, minimum, what: str) -> None:
    if actual < minimum:
        raise AssertionError(f"{what}: expected at least {minimum}, got {actual}")
