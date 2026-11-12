"""Command-line entry point. Subcommands are added in later steps."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from gnsskit import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gnsskit", description=__doc__)
    parser.add_argument("--version", action="version", version=f"gnsskit {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
