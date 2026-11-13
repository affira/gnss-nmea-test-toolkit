"""gnsskit command-line interface."""

from __future__ import annotations

import argparse
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
    return parser


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
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
