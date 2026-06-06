from __future__ import annotations

import json
from typing import Any


def _limit(text: str, max_chars: int | None) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return text[:max_chars]
    return text[: max_chars - 12].rstrip() + "\n...(已截断)"


def _json_block(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def render_response_text(response: dict[str, Any], platform: str = "telegram", max_chars: int | None = None) -> str:
    if response.get("status") == "needs_confirmation":
        token = response.get("data", {}).get("confirmation_token", "")
        separator = "\n发送：" if platform == "telegram" else " "
        return _limit(f"{response.get('message_zh', '需要确认')}{separator}/ai 确认 {token}".strip(), max_chars)

    if response.get("error"):
        error_data = response["error"]
        return _limit(f"{response.get('message_zh', '执行失败')}: {error_data.get('detail') or error_data.get('code')}", max_chars)

    data = response.get("data", {})
    output = data.get("output") if isinstance(data, dict) else None
    if output:
        return _limit(str(output), max_chars)

    message = str(response.get("message_zh") or response.get("status") or "OK")
    if isinstance(data, dict) and "items" in data:
        lines = [message]
        for item in data.get("items", []):
            usage = item.get("usage") or item.get("id") or item.get("provider") or "item"
            description = item.get("description_zh") or item.get("status") or item.get("canonical_action") or ""
            lines.append(f"{usage}: {description}".rstrip())
        if data.get("providers"):
            lines.append("\n提供商：")
            for provider in data.get("providers", []):
                lines.append(f"{provider.get('provider')}: available={provider.get('available')} path={provider.get('path')}")
        return _limit("\n".join(lines), max_chars)

    if data:
        return _limit(f"{message}\n{_json_block(data)}", max_chars)

    return _limit(message, max_chars)


def render_event_text(event: dict[str, Any]) -> str | None:
    phase = str(event.get("phase") or "")
    provider = str(event.get("provider") or "runner")
    message = str(event.get("public_message_zh") or "")
    if phase == "queued":
        return f"已收到，正在排队。provider={provider}。"
    if phase == "calling_model":
        return f"正在调用 {provider}。状态：模型正在思考或工具正在运行。"
    if phase == "running":
        return f"{provider} 仍在运行：{message or '模型思考、工具执行、联网等待或生成中；不是卡死。'}"
    if phase == "warning":
        return message or "上下文接近阈值。"
    if phase == "error":
        return f"{provider} 执行出错：{event.get('error') or message or 'unknown'}"
    if phase == "done":
        return f"{provider} 已完成，正在整理回复。"
    return None
