from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib import error, request

from .storage import atomic_write_text


CLAUDE_MODEL_FALLBACKS = [
    "gpt-5.5",
    "claude-opus-4-6-thinking",
    "claude-opus-4-6",
    "claude-opus-4-7-thinking",
    "claude-opus-4-7",
    "claude-opus-4-8-thinking",
    "claude-opus-4-8",
]
CODEX_MODEL_FALLBACKS = ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex"]
TARGET_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claudecode": "claude-code",
    "anthropic": "claude-code",
    "codex": "codex",
    "openai": "codex",
    "vscode": "vscode",
    "code": "vscode",
    "vs-code": "vscode",
}


def normalize_target(value: str | None) -> str | None:
    if not value:
        return None
    return TARGET_ALIASES.get(value.strip().lower())


def redact_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def load_config_env(state: Path) -> dict[str, str]:
    path = state / "config.env"
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key:
            data[key] = value
    return data


def apply_config_env(state: Path, updates: dict[str, str]) -> dict[str, str]:
    data = load_config_env(state)
    data.update({key: str(value) for key, value in updates.items()})
    order = [
        "AI_REMOTE_STATE",
        "AI_WORKSPACE_ROOT",
        "AI_RUNNER_PROVIDERS",
        "AI_PERMISSION_SCOPE",
        "AI_REQUIRE_SHELL_CONFIRMATION",
        "HOME",
        "CODEX_HOME",
        "PATH",
        "CLAUDE_MODEL",
        "VSCODE_CLAUDE_MODEL",
        "CLAUDE_MAX_TURNS",
        "CODEX_MODEL",
        "AI_TASK_RESERVED_USD",
        "TELEGRAM_RESERVED_USD",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_BASE_URL",
        "AI_BRIDGE_SHARED_SECRET",
    ]
    keys = [key for key in order if key in data] + sorted(key for key in data if key not in order)
    text = "".join(f"{key}={data[key]}\n" for key in keys)
    path = state / "config.env"
    atomic_write_text(path, text)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    os.environ.update(updates)
    return data


def tool_home() -> Path:
    return Path(os.environ.get("AI_TOOL_HOME") or os.environ.get("HOME") or "/root")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or os.environ.get("AI_CODEX_HOME") or str(tool_home() / ".codex"))


def claude_settings_path() -> Path:
    return tool_home() / ".claude" / "settings.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_codex_auth() -> dict[str, Any]:
    return _read_json(codex_home() / "auth.json")


def _read_codex_config() -> str:
    path = codex_home() / "config.toml"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        'model_provider = "openai"\n'
        'model = "gpt-5.5"\n'
        'review_model = "gpt-5.5"\n'
        'model_reasoning_effort = "xhigh"\n'
        'openai_base_url = "https://api.openai.com/v1"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "danger-full-access"\n'
        '\n'
        '[shell_environment_policy]\n'
        'inherit = "all"\n'
        '\n'
        '[sandbox_workspace_write]\n'
        'network_access = true\n'
        '\n'
        '[features]\n'
        'goals = true\n'
    )


def _replace_or_prepend_toml_key(text: str, key: str, value: str) -> str:
    line = f'{key} = "{value}"'
    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=\s*\".*\"$")
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    return line + "\n" + text


def _replace_or_append_provider_base_url(text: str, base_url: str) -> str:
    openai_base_pattern = re.compile(r'(?m)^openai_base_url\s*=\s*".*"$')
    if openai_base_pattern.search(text):
        text = openai_base_pattern.sub(f'openai_base_url = "{base_url}"', text, count=1)
    else:
        text = f'openai_base_url = "{base_url}"\n' + text
    if "[model_providers.OpenAI]" in text:
        provider_block_pattern = re.compile(
            r'(?ms)(^\[model_providers\.OpenAI\]\n.*?)(?=^\[|\Z)'
        )

        def replace_legacy_provider(match: re.Match[str]) -> str:
            block = match.group(1)
            pattern = re.compile(r'(?m)^base_url\s*=\s*".*"$')
            if pattern.search(block):
                return pattern.sub(f'base_url = "{base_url}"', block, count=1)
            return block.replace("[model_providers.OpenAI]\n", f'[model_providers.OpenAI]\nbase_url = "{base_url}"\n', 1)

        text = provider_block_pattern.sub(replace_legacy_provider, text, count=1)
    return text


def _write_codex_config(text: str) -> None:
    path = codex_home() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text.rstrip() + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_claude_env() -> dict[str, str]:
    data = _read_json(claude_settings_path())
    env = data.get("env")
    return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}


def _write_claude_env(updates: dict[str, str]) -> None:
    path = claude_settings_path()
    data = _read_json(path)
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    env = {str(k): str(v) for k, v in env.items()}
    env.update({key: value for key, value in updates.items() if value})
    data["env"] = env
    _write_json_private(path, data)


def apply_model(state: Path, target: str, model: str) -> dict[str, Any]:
    updates: dict[str, str]
    if target == "codex":
        text = _read_codex_config()
        text = _replace_or_prepend_toml_key(text, "model", model)
        _write_codex_config(text)
        updates = {"CODEX_MODEL": model}
    elif target == "vscode":
        _write_claude_env({"CLAUDE_MODEL": model})
        updates = {"VSCODE_CLAUDE_MODEL": model, "CLAUDE_MODEL": model}
    else:
        _write_claude_env({"CLAUDE_MODEL": model})
        updates = {"CLAUDE_MODEL": model}
    apply_config_env(state, updates)
    return {"target": target, "model": model}


def apply_api_key(state: Path, target: str, api_key: str) -> dict[str, Any]:
    if target == "codex":
        auth = _read_codex_auth()
        auth["OPENAI_API_KEY"] = api_key
        _write_json_private(codex_home() / "auth.json", auth)
        apply_config_env(state, {"OPENAI_API_KEY": api_key})
    elif target in {"claude-code", "vscode"}:
        _write_claude_env({"ANTHROPIC_AUTH_TOKEN": api_key})
        apply_config_env(state, {"ANTHROPIC_AUTH_TOKEN": api_key})
    else:
        raise ValueError(f"unsupported_target:{target}")
    return {"target": target, "api_key": redact_secret(api_key), "api_key_configured": True}


def apply_base_url(state: Path, target: str, base_url: str) -> dict[str, Any]:
    if target == "codex":
        _write_codex_config(_replace_or_append_provider_base_url(_read_codex_config(), base_url))
        apply_config_env(state, {"CODEX_BASE_URL": base_url})
    elif target in {"claude-code", "vscode"}:
        _write_claude_env({"ANTHROPIC_BASE_URL": base_url})
        apply_config_env(state, {"ANTHROPIC_BASE_URL": base_url})
    else:
        raise ValueError(f"unsupported_target:{target}")
    return {"target": target, "base_url": base_url}


UNLIMITED_BUDGET_VALUES = {"", "0", "off", "none", "no", "false", "unlimited", "infinite", "inf", "无限", "不限", "关闭"}


def _task_budget_value(reserved_usd: float | str) -> tuple[str, float, bool]:
    raw = str(reserved_usd).strip()
    if raw.lower() in UNLIMITED_BUDGET_VALUES:
        return "0", 0.0, True
    value = max(0.0, float(raw))
    formatted = f"{value:.6f}".rstrip("0").rstrip(".") or "0"
    return formatted, value, value == 0.0


def apply_task_budget(state: Path, reserved_usd: float | str) -> dict[str, Any]:
    value, parsed, unlimited = _task_budget_value(reserved_usd)
    apply_config_env(state, {"AI_TASK_RESERVED_USD": value, "TELEGRAM_RESERVED_USD": value})
    return {"task_reserved_usd": parsed, "telegram_reserved_usd": parsed, "budget_unlimited": unlimited}


def _claude_max_turns_value(max_turns: int | str) -> tuple[str, int, bool]:
    raw = str(max_turns).strip()
    if raw.lower() in UNLIMITED_BUDGET_VALUES:
        return "0", 0, True
    parsed = int(raw)
    if parsed < 0:
        raise ValueError("max_turns must be 0/unlimited or a positive integer")
    return str(parsed), parsed, parsed == 0


def apply_claude_max_turns(state: Path, max_turns: int | str) -> dict[str, Any]:
    value, parsed, unlimited = _claude_max_turns_value(max_turns)
    apply_config_env(state, {"CLAUDE_MAX_TURNS": value})
    return {"claude_max_turns": parsed, "max_turns_unlimited": unlimited}


def _toml_string_value(text: str, key: str) -> str:
    match = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else ""


def config_summary(target: str) -> dict[str, Any]:
    if target == "codex":
        auth = _read_codex_auth()
        text = _read_codex_config()
        return {
            "target": target,
            "model": os.environ.get("CODEX_MODEL") or _toml_string_value(text, "model"),
            "base_url": os.environ.get("CODEX_BASE_URL") or _toml_string_value(text, "base_url") or _toml_string_value(text, "openai_base_url"),
            "api_key_configured": bool(os.environ.get("OPENAI_API_KEY") or auth.get("OPENAI_API_KEY")),
            "config_file": str(codex_home() / "config.toml"),
            "auth_file": str(codex_home() / "auth.json"),
        }
    env = _read_claude_env()
    model_key = "VSCODE_CLAUDE_MODEL" if target == "vscode" else "CLAUDE_MODEL"
    return {
        "target": target,
        "model": os.environ.get(model_key) or os.environ.get("CLAUDE_MODEL") or env.get("CLAUDE_MODEL", ""),
        "base_url": os.environ.get("ANTHROPIC_BASE_URL") or env.get("ANTHROPIC_BASE_URL", ""),
        "api_key_configured": bool(os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")),
        "claude_max_turns": os.environ.get("CLAUDE_MAX_TURNS") or load_config_env(Path(os.environ.get("AI_REMOTE_STATE", "/var/lib/ai-remote-runner"))).get("CLAUDE_MAX_TURNS", "0"),
        "config_file": str(claude_settings_path()),
    }


def _candidate_model_urls(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    if not base:
        return []
    if base.endswith("/v1"):
        return [f"{base}/models", f"{base[:-3]}/models"]
    return [f"{base}/v1/models", f"{base}/models"]


def _parse_models(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or payload.get("result")
    else:
        items = payload
    models: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                value = item.get("id") or item.get("name") or item.get("model")
                if value:
                    models.append(str(value))
    return sorted(dict.fromkeys(models))


def _fetch_json(url: str, headers: dict[str, str], timeout: int) -> Any:
    req = request.Request(url, headers=headers, method="GET")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_supported_models(target: str, timeout: int = 15) -> dict[str, Any]:
    summary = config_summary(target)
    fallback = CLAUDE_MODEL_FALLBACKS if target in {"claude-code", "vscode"} else CODEX_MODEL_FALLBACKS
    if summary.get("model") and summary["model"] not in fallback:
        fallback = [str(summary["model"]), *fallback]
    key = ""
    base_url = str(summary.get("base_url") or "")
    headers: dict[str, str] = {}
    if target == "codex":
        key = os.environ.get("OPENAI_API_KEY") or str(_read_codex_auth().get("OPENAI_API_KEY") or "")
        base_url = base_url or "https://api.openai.com/v1"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    else:
        env = _read_claude_env()
        key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN", "") or env.get("ANTHROPIC_API_KEY", "")
        base_url = base_url or "https://api.anthropic.com"
        headers = {
            "x-api-key": key,
            "Authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
        } if key else {"anthropic-version": "2023-06-01"}
    if not key:
        return {"target": target, "source": "fallback_unverified", "models": fallback, "error": "api_key_not_configured", "configured": summary}
    last_error = ""
    for url in _candidate_model_urls(base_url):
        try:
            models = _parse_models(_fetch_json(url, headers, timeout))
        except (OSError, TimeoutError, error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)[:300]
            continue
        if models:
            return {"target": target, "source": "api", "models": models, "models_url": url, "configured": summary}
    return {"target": target, "source": "fallback_unverified", "models": fallback, "error": last_error or "models_endpoint_returned_empty", "configured": summary}
