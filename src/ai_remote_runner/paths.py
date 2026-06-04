from __future__ import annotations

import os
from pathlib import Path


def state_root() -> Path:
    if "AI_REMOTE_STATE" in os.environ:
        return Path(os.environ["AI_REMOTE_STATE"])
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return Path.cwd() / "work" / "local-state"
    return Path("/var/lib/ai-remote-runner")


def workspace_root() -> Path:
    if "AI_WORKSPACE_ROOT" in os.environ:
        return Path(os.environ["AI_WORKSPACE_ROOT"])
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return Path.cwd() / "work" / "local-workspaces"
    return Path("/srv/ai-workspaces")


def ensure_runtime_dirs() -> None:
    for path in [
        state_root(),
        state_root() / "credentials",
        state_root() / "instructions" / "snapshots",
        state_root() / "budget",
        workspace_root(),
    ]:
        path.mkdir(parents=True, exist_ok=True)
