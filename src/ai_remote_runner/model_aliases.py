from __future__ import annotations


CLAUDE_MODEL_ALIASES = {
    "anthropic": "opus",
    "claude": "opus",
    "claude-opus": "opus",
    "claude-sonnet": "sonnet",
}

GPT_MODEL_ALIASES = {
    "gpt": "gpt-5.5",
    "gpt5": "gpt-5.5",
    "gpt-5": "gpt-5.5",
    "gpt5.5": "gpt-5.5",
    "openai": "gpt-5.5",
    "codex": "gpt-5.5",
}

LEGACY_CLAUDE_MODEL_ALIASES = CLAUDE_MODEL_ALIASES | {key: value for key, value in GPT_MODEL_ALIASES.items() if key != "codex"}
CODEX_MODEL_ALIASES = GPT_MODEL_ALIASES

TARGET_PREFIX_ALIASES = {
    "anthropic",
    "claude",
    "claude-code",
    "claudecode",
    "code",
    "codex",
    "openai",
    "vs-code",
    "vscode",
}
MULTI_TOKEN_TARGET_PREFIX_ALIASES = {
    ("claude", "code"),
    ("visual", "studio", "code"),
    ("vs", "code"),
}


def _strip_target_prefix(value: str) -> str:
    parts = value.split()
    if len(parts) <= 1:
        return value
    lowered = [part.lower() for part in parts]
    for size in (3, 2):
        if len(lowered) > size and tuple(lowered[:size]) in MULTI_TOKEN_TARGET_PREFIX_ALIASES:
            return " ".join(parts[size:])
    if lowered[0] in TARGET_PREFIX_ALIASES:
        return " ".join(parts[1:])
    return value


def model_family_from_name(model: str) -> str | None:
    value = " ".join(str(model).strip().split())
    value = _strip_target_prefix(value)
    if len(value.split()) > 1:
        return None
    normalized = value.lower()
    if normalized in CLAUDE_MODEL_ALIASES or normalized.startswith(("claude", "opus", "sonnet", "haiku")):
        return "claude"
    if normalized in GPT_MODEL_ALIASES or normalized.startswith(("gpt", "openai", "o1", "o3", "o4", "o5")) or "codex" in normalized:
        return "gpt"
    return None


def normalize_model_name(target: str, model: str, model_family: str | None = None) -> str:
    value = " ".join(str(model).strip().split())
    value = _strip_target_prefix(value)
    if len(value.split()) > 1:
        return ""
    normalized = value.lower()
    if model_family == "claude":
        return CLAUDE_MODEL_ALIASES.get(normalized, value)
    if model_family == "gpt":
        return GPT_MODEL_ALIASES.get(normalized, value)
    if target in {"claude-code", "vscode"}:
        return LEGACY_CLAUDE_MODEL_ALIASES.get(normalized, value)
    if target == "codex":
        return CODEX_MODEL_ALIASES.get(normalized, value)
    return value
