from __future__ import annotations

import os
from pathlib import Path


def state_root() -> Path:
    return Path(os.environ.get("AI_REMOTE_STATE", "/var/lib/ai-remote-runner"))


def workspace_root() -> Path:
    return Path(os.environ.get("AI_WORKSPACE_ROOT", "/srv/ai-workspaces"))


def ensure_runtime_dirs() -> None:
    for path in [
        state_root(),
        state_root() / "credentials",
        state_root() / "instructions" / "snapshots",
        state_root() / "budget",
        workspace_root(),
    ]:
        path.mkdir(parents=True, exist_ok=True)
