from __future__ import annotations

import argparse
from pathlib import Path
import sys

def main() -> int:
    repository = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Graphical HAL wiring editor for cia402")
    parser.add_argument(
        "--xml",
        type=Path,
        default=repository / "example" / "ethercat-conf.xml",
        help="EtherCAT XML configuration to open",
    )
    parser.add_argument(
        "--comp",
        type=Path,
        default=repository / "cia402.comp",
        help="cia402 component source",
    )
    parser.add_argument("--joints", type=int, default=1, help="number of joint blocks")
    parser.add_argument("--instances", type=int, default=1, help="number of cia402 blocks")
    args = parser.parse_args()

    try:
        from .gui import build_project, run
    except ImportError as exc:
        print("cia402-wiring-gui: %s" % exc, file=sys.stderr)
        return 2

    try:
        project = build_project(args.xml, args.comp, args.joints, args.instances)
    except Exception as exc:
        print("cia402-wiring-gui: cannot load configuration: %s" % exc, file=sys.stderr)
        return 1
    return run(project, args.comp)


if __name__ == "__main__":
    raise SystemExit(main())
