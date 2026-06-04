from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as fh:
            fh.write(text)
            tmp_path = Path(fh.name)
        tmp_path.replace(path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


def atomic_write_json(path: Path, data: Any, *, ensure_ascii: bool = False) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=ensure_ascii, indent=2, sort_keys=True))
