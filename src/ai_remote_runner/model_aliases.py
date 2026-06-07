from __future__ import annotations


CLAUDE_MODEL_ALIASES = {
    "anthropic": "opus",
    "claude": "opus",
    "claude-opus": "opus",
    "claude-sonnet": "sonnet",
    "gpt": "gpt-5.5",
    "gpt5": "gpt-5.5",
    "gpt-5": "gpt-5.5",
    "gpt5.5": "gpt-5.5",
    "openai": "gpt-5.5",
}

CODEX_MODEL_ALIASES = {
    "codex": "gpt-5.3-codex",
    "gpt": "gpt-5.5",
    "gpt5": "gpt-5.5",
    "gpt-5": "gpt-5.5",
    "gpt5.5": "gpt-5.5",
    "openai": "gpt-5.5",
}

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


def normalize_model_name(target: str, model: str) -> str:
    value = " ".join(str(model).strip().split())
    value = _strip_target_prefix(value)
    if len(value.split()) > 1:
        return ""
    normalized = value.lower()
    if target in {"claude-code", "vscode"}:
        return CLAUDE_MODEL_ALIASES.get(normalized, value)
    if target == "codex":
        return CODEX_MODEL_ALIASES.get(normalized, value)
    return value
