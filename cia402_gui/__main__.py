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
        default=None,
        help="optional EtherCAT XML configuration to open immediately",
    )
    parser.add_argument(
        "--comp",
        type=Path,
        default=repository / "cia402.comp",
        help="cia402 component source",
    )
    parser.add_argument(
        "--joints",
        type=int,
        default=None,
        help="offline fallback joint count (normally discovered from LinuxCNC)",
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=None,
        help="offline fallback cia402 count (normally discovered from LinuxCNC)",
    )
    args = parser.parse_args()

    try:
        from .gui import build_project, build_runtime_project, run
    except ImportError as exc:
        print("cia402-wiring-gui: %s" % exc, file=sys.stderr)
        return 2

    try:
        if args.xml is None:
            project = build_runtime_project(args.comp)
        else:
            project = build_project(args.xml, args.comp, args.joints, args.instances)
    except Exception as exc:
        print("cia402-wiring-gui: cannot load configuration: %s" % exc, file=sys.stderr)
        return 1
    return run(project, args.comp)


if __name__ == "__main__":
    raise SystemExit(main())
