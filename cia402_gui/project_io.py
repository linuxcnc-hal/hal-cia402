from __future__ import annotations

import json
from pathlib import Path

from .model import WiringProject


def save_project(project: WiringProject, path: Path) -> None:
    Path(path).write_text(
        json.dumps(project.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def load_project(path: Path) -> WiringProject:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return WiringProject.from_dict(data)

