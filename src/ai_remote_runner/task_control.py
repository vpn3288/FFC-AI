from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .storage import atomic_write_json


ACTIVE_PROCESSES_FILE = "active-processes.json"
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def process_control_enabled() -> bool:
    raw = os.environ.get("AI_PROCESS_CONTROL_ENABLED", "1")
    return raw.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled", "开启"}


def active_processes_path(state: Path) -> Path:
    return state / ACTIVE_PROCESSES_FILE


def _load_records(state: Path) -> dict[str, dict[str, Any]]:
    path = active_processes_path(state)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_records(state: Path, data: dict[str, dict[str, Any]]) -> None:
    atomic_write_json(active_processes_path(state), data)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    try:
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], text=True, capture_output=True, check=False, timeout=5)
        return str(pid) in result.stdout
    except Exception:
        return True


def _pgid_for_pid(pid: int) -> int | None:
    if os.name != "posix":
        return None
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def popen_process_group_kwargs() -> dict[str, Any]:
    if os.name == "posix":
        return {"start_new_session": True}
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {}


def register_process(
    state: Path,
    run_id: str,
    provider: str,
    process: subprocess.Popen[Any],
    command: str | list[str],
    cwd: Path,
    *,
    action: str = "provider",
) -> None:
    state.mkdir(parents=True, exist_ok=True)
    data = _load_records(state)
    pid = int(process.pid)
    record_id = f"{run_id}:{pid}"
    data[record_id] = {
        "record_id": record_id,
        "run_id": run_id,
        "provider": provider,
        "action": action,
        "pid": pid,
        "pgid": _pgid_for_pid(pid),
        "command": command if isinstance(command, str) else " ".join(str(part) for part in command),
        "cwd": str(cwd),
        "started_at": int(time.time()),
    }
    _save_records(state, data)


def unregister_process(state: Path, run_id: str, pid: int | None = None) -> None:
    data = _load_records(state)
    updated = {}
    for record_id, record in data.items():
        if str(record.get("run_id")) != str(run_id):
            updated[record_id] = record
            continue
        if pid is not None and int(record.get("pid") or 0) != int(pid):
            updated[record_id] = record
    _save_records(state, updated)


def active_processes(state: Path) -> list[dict[str, Any]]:
    data = _load_records(state)
    alive: dict[str, dict[str, Any]] = {}
    for record_id, record in data.items():
        try:
            pid = int(record.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if _process_alive(pid):
            alive[record_id] = record
    if len(alive) != len(data):
        _save_records(state, alive)
    return sorted(alive.values(), key=lambda item: int(item.get("started_at") or 0), reverse=True)


def request_stop(state: Path, *, force: bool = False) -> dict[str, Any]:
    data = {"stop_requested": True, "force": force, "time": int(time.time())}
    atomic_write_json(state / "stop-request.json", data)
    return data


def _terminate_record(record: dict[str, Any], sig: signal.Signals) -> bool:
    pid = int(record.get("pid") or 0)
    if pid <= 0:
        return False
    if not _process_alive(pid):
        return False
    if os.name == "posix":
        pgid = record.get("pgid")
        try:
            pgid_int = int(pgid) if pgid is not None else os.getpgid(pid)
            os.killpg(pgid_int, sig)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            try:
                os.kill(pid, sig)
                return True
            except OSError:
                return False
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], text=True, capture_output=True, check=False, timeout=10)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def terminate_active_processes(
    state: Path,
    *,
    target_run_id: str | None = None,
    grace_seconds: float = 3.0,
) -> dict[str, Any]:
    records = active_processes(state)
    selected = [record for record in records if not target_run_id or str(record.get("run_id")) == target_run_id]
    stopped: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for record in selected:
        if _terminate_record(record, signal.SIGTERM):
            stopped.append(record)
        else:
            missing.append(record)
    if stopped and grace_seconds > 0:
        time.sleep(grace_seconds)
    killed: list[dict[str, Any]] = []
    for record in stopped:
        try:
            pid = int(record.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if _process_alive(pid) and _terminate_record(record, SIGKILL):
            killed.append(record)
    remaining = active_processes(state)
    remaining_ids = {str(record.get("record_id")) for record in remaining}
    data = _load_records(state)
    for record in selected:
        record_id = str(record.get("record_id") or "")
        if record_id and record_id not in remaining_ids:
            data.pop(record_id, None)
    _save_records(state, data)
    return {
        "requested_run_id": target_run_id or "",
        "matched": len(selected),
        "terminated": len(stopped),
        "killed_after_grace": len(killed),
        "missing": len(missing),
        "records": stopped,
        "remaining": remaining,
    }


def run_registered(
    state: Path,
    run_id: str,
    provider: str,
    command: str | list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    input: str | None = None,
    text: bool = True,
    capture_output: bool = True,
    timeout: int | float | None = None,
    check: bool = False,
    shell: bool = False,
    executable: str | None = None,
    action: str = "provider",
) -> subprocess.CompletedProcess[str]:
    stdout = subprocess.PIPE if capture_output else None
    stderr = subprocess.PIPE if capture_output else None
    popen_kwargs: dict[str, Any] = popen_process_group_kwargs()
    if executable is not None:
        popen_kwargs["executable"] = executable
    process: subprocess.Popen[str] = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=stdout,
        stderr=stderr,
        text=text,
        shell=shell,
        **popen_kwargs,
    )
    register_process(state, run_id, provider, process, command, cwd, action=action)
    try:
        try:
            out, err = process.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_record({"pid": process.pid, "pgid": _pgid_for_pid(process.pid)}, signal.SIGTERM)
            try:
                out, err = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _terminate_record({"pid": process.pid, "pgid": _pgid_for_pid(process.pid)}, SIGKILL)
                out, err = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout, output=out or exc.output, stderr=err or exc.stderr)
        completed = subprocess.CompletedProcess(command, int(process.returncode or 0), out or "", err or "")
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(completed.returncode, command, output=completed.stdout, stderr=completed.stderr)
        return completed
    finally:
        unregister_process(state, run_id, int(process.pid))
