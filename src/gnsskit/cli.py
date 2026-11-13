"""gnsskit command-line interface."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from gnsskit import __version__


def _window(text: str) -> tuple[float, float]:
    """Parse START:DURATION (seconds from the first epoch)."""
    try:
        start, duration = (float(part) for part in text.split(":"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected START:DURATION in seconds, e.g. 10:30, got {text!r}"
        ) from None
    return start, duration


def _reference(text: str) -> tuple[float, float]:
    """Parse LAT,LON in decimal degrees."""
    try:
        lat, lon = (float(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected LAT,LON in decimal degrees, e.g. 51.4779,-0.0015, got {text!r}"
        ) from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise argparse.ArgumentTypeError(f"reference {text!r} is outside -90..90, -180..180")
    return lat, lon


def _threshold(text: str) -> float | None:
    """A threshold value, or 'none' to disable the check."""
    if text.strip().lower() == "none":
        return None
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number or 'none', got {text!r}") from None


# CLI flag -> profile threshold key
_THRESHOLD_FLAGS = {
    "min_availability": "min_availability_pct",
    "max_ttff": "max_ttff_s",
    "max_hdop_p95": "max_hdop_p95",
    "max_cep95": "max_cep95_m",
    "max_integrity_errors": "max_integrity_errors",
}
EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCOMPLETE": 3}  # 2 = usage or input error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gnsskit", description=__doc__)
    parser.add_argument("--version", action="version", version=f"gnsskit {__version__}")
    commands = parser.add_subparsers(dest="command")

    inj = commands.add_parser(
        "inject",
        help="write a copy of a log with controlled faults, plus a JSON manifest",
        description="Inject controlled faults into a clean NMEA log. Windows are "
        "START:DURATION in seconds from the first epoch.",
    )
    inj.add_argument("input", type=Path, help="clean NMEA log")
    inj.add_argument("-o", "--output", type=Path, required=True, help="faulty log to write")
    inj.add_argument("--manifest", type=Path, help="default: OUTPUT.manifest.json")
    inj.add_argument("--seed", type=int, default=0)
    inj.add_argument("--bad-checksums", type=int, default=0, metavar="N")
    inj.add_argument("--truncate", type=int, default=0, metavar="N")
    inj.add_argument("--blank-positions", type=int, default=0, metavar="N")
    inj.add_argument("--unknown", type=int, default=0, metavar="N")
    inj.add_argument("--garbage", type=int, default=0, metavar="N")
    inj.add_argument("--drop-gsv-parts", type=int, default=0, metavar="N")
    inj.add_argument("--outage", type=_window, action="append", default=[],
                     metavar="START:DURATION")  # fmt: skip
    inj.add_argument("--gap", type=_window, action="append", default=[],
                     metavar="START:DURATION")  # fmt: skip
    inj.add_argument("--midnight", type=float, metavar="SECONDS",
                     help="shift times so the first epoch is SECONDS before 00:00 UTC")  # fmt: skip

    rep = commands.add_parser(
        "report",
        help="analyse a log and write a Markdown test report",
        description="Analyse an NMEA log and write a Markdown test report with PASS/FAIL "
        "against a test profile. Exit code: 0 PASS, 1 FAIL, 3 INCOMPLETE (a criterion "
        "could not be measured), 2 usage or input error.",
    )
    rep.add_argument("log", type=Path, help="NMEA log file")
    rep.add_argument("-o", "--output", type=Path, help="report file (default: standard output)")
    rep.add_argument("--profile", type=Path,
                     help="YAML test profile (default: built-in standard-gnss values)")  # fmt: skip
    rep.add_argument("--ref", type=_reference, metavar="LAT,LON",
                     help="reference point for CEP (default: mean of the fix positions)")  # fmt: skip
    for flag, key in _THRESHOLD_FLAGS.items():
        rep.add_argument("--" + flag.replace("_", "-"), dest=flag, type=_threshold,
                         default=argparse.SUPPRESS, metavar="VALUE|none",
                         help=f"override the profile's {key}")  # fmt: skip
    return parser


def _report(args: argparse.Namespace) -> int:
    from gnsskit.analysis import analyse
    from gnsskit.log import read_lines
    from gnsskit.profile import DEFAULT_PROFILE, ProfileError, evaluate, load_profile, with_overrides
    from gnsskit.report import build_report

    try:
        profile = load_profile(args.profile) if args.profile else DEFAULT_PROFILE
        overrides = {key: getattr(args, flag) for flag, key in _THRESHOLD_FLAGS.items()
                     if hasattr(args, flag)}  # fmt: skip
        profile = with_overrides(profile, overrides)
        data = args.log.read_bytes()
    except (OSError, ProfileError) as exc:
        print(f"gnsskit report: error: {exc}", file=sys.stderr)
        return 2

    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    log = read_lines(lines)
    analysis = analyse(log, reference=args.ref)
    evaluation = evaluate(log, analysis, profile)
    report = build_report(
        log_name=args.log.name,
        sha256=hashlib.sha256(data).hexdigest(),
        header_lines=[line.strip() for line in lines if line.lstrip().startswith("#")],
        log=log,
        analysis=analysis,
        evaluation=evaluation,
        generated_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0),
    )
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return EXIT_CODES[str(evaluation.verdict)]


def _inject(args: argparse.Namespace) -> int:
    from gnsskit.faults import FaultPlan, inject

    plan = FaultPlan(
        seed=args.seed,
        bad_checksums=args.bad_checksums,
        truncated=args.truncate,
        blank_positions=args.blank_positions,
        unknown_sentences=args.unknown,
        garbage_lines=args.garbage,
        dropped_gsv_parts=args.drop_gsv_parts,
        outages=tuple(args.outage),
        gaps=tuple(args.gap),
        midnight_shift_s=args.midnight,
    )
    try:
        text = args.input.read_text(encoding="utf-8", errors="replace")
        result = inject(text.splitlines(), plan, source=args.input.name)
    except (OSError, ValueError) as exc:
        print(f"gnsskit inject: error: {exc}", file=sys.stderr)
        return 2
    manifest_path = args.manifest or args.output.with_name(args.output.name + ".manifest.json")
    args.output.write_text("\n".join(result.lines) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(result.manifest, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inject":
        return _inject(args)
    if args.command == "report":
        return _report(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
