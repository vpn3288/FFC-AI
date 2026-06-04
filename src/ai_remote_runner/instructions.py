from __future__ import annotations

import hashlib
import time
from pathlib import Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class InstructionStore:
    def __init__(self, global_path: Path, workspace_root: Path) -> None:
        self.global_path = global_path
        self.workspace_root = workspace_root
        self.snapshot_root = global_path.parent / "snapshots"
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self.global_path.parent.mkdir(parents=True, exist_ok=True)

    def project_path(self, workspace_id: str) -> Path:
        return self.workspace_root / workspace_id / "project.md"

    def show(self, scope: str, workspace_id: str = "default", preview_chars: int = 1000) -> dict[str, str]:
        path = self.global_path if scope == "global" else self.project_path(workspace_id)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return {"path": str(path), "sha256": sha256_text(text), "preview": text[:preview_chars]}

    def snapshot(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        snap_id = f"{path.stem}-{int(time.time())}-{sha256_text(text)[:12]}"
        snap_path = self.snapshot_root / f"{snap_id}.md"
        snap_path.write_text(text, encoding="utf-8")
        return snap_id

    def write(self, scope: str, text: str, workspace_id: str = "default", append: bool = False) -> dict[str, str]:
        path = self.global_path if scope == "global" else self.project_path(workspace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        snap_id = self.snapshot(path)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        new_text = old + text if append else text
        path.write_text(new_text, encoding="utf-8")
        return {"path": str(path), "snapshot": snap_id, "sha256": sha256_text(new_text)}

    def rollback(self, scope: str, snapshot_id: str, workspace_id: str = "default") -> dict[str, str]:
        path = self.global_path if scope == "global" else self.project_path(workspace_id)
        snap_path = self.snapshot_root / f"{snapshot_id}.md"
        if not snap_path.exists():
            raise FileNotFoundError(snapshot_id)
        current_snapshot = self.snapshot(path)
        text = snap_path.read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return {"path": str(path), "restored_snapshot": snapshot_id, "previous_snapshot": current_snapshot, "sha256": sha256_text(text)}
