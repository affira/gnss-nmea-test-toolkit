"""Write the SYNTHETIC logs to data/synthetic/ (or check they are up to date).

Usage:
    python scripts/make_synthetic.py           # (re)write the files
    python scripts/make_synthetic.py --check   # exit 1 if any file differs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gnsskit.synthetic import SCENARIOS, generate

OUT = Path(__file__).resolve().parents[1] / "data" / "synthetic"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only check, do not write")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for scenario in SCENARIOS.values():
        path = OUT / scenario.filename
        content = "\n".join(generate(scenario)) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(path.name)
        else:
            path.write_text(content)
            print(f"wrote {path.relative_to(OUT.parents[1])}")
    if stale:
        print("out of date: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
