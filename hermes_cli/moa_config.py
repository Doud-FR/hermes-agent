"""Mixture-of-Agents configuration and slash-command helpers."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from typing import Any

MOA_MARKER_PREFIX = "__HERMES_MOA_TURN_V1__"

DEFAULT_MOA_REFERENCE_MODELS: list[dict[str, str]] = [
    {"provider": "openai-codex", "model": "gpt-5.5"},
    {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"},
]

DEFAULT_MOA_AGGREGATOR: dict[str, str] = {
    "provider": "openrouter",
    "model": "anthropic/claude-opus-4.8",
}


def _clean_slot(slot: Any) -> dict[str, str] | None:
    if not isinstance(slot, dict):
        return None
    provider = str(slot.get("provider") or "").strip()
    model = str(slot.get("model") or "").strip()
    if not provider or not model:
        return None
    return {"provider": provider, "model": model}


def normalize_moa_config(raw: Any) -> dict[str, Any]:
    """Return a validated MoA config with provider/model slots."""
    if not isinstance(raw, dict):
        raw = {}

    refs = [_clean_slot(item) for item in raw.get("reference_models") or []]
    refs = [item for item in refs if item is not None]
    if not refs:
        refs = deepcopy(DEFAULT_MOA_REFERENCE_MODELS)

    aggregator = _clean_slot(raw.get("aggregator")) or deepcopy(DEFAULT_MOA_AGGREGATOR)

    return {
        "enabled": bool(raw.get("enabled", True)),
        "reference_models": refs,
        "aggregator": aggregator,
        "reference_temperature": float(raw.get("reference_temperature", 0.6) or 0.6),
        "aggregator_temperature": float(raw.get("aggregator_temperature", 0.4) or 0.4),
        "max_tokens": int(raw.get("max_tokens", 4096) or 4096),
    }


def encode_moa_turn(prompt: str, config: Any = None) -> str:
    """Encode a /moa turn so all frontends can route it as a normal user turn."""
    payload = {
        "prompt": str(prompt or ""),
        "config": normalize_moa_config(config or {}),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return f"{MOA_MARKER_PREFIX}{encoded}"


def decode_moa_turn(message: Any) -> tuple[str, dict[str, Any] | None]:
    """Decode a hidden /moa turn marker.

    Returns ``(user_prompt, moa_config_or_none)``. Invalid or non-marker input is
    returned unchanged with ``None`` so callers can use this unconditionally.
    """
    if not isinstance(message, str) or not message.startswith(MOA_MARKER_PREFIX):
        return message, None
    encoded = message[len(MOA_MARKER_PREFIX):].strip()
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
    except Exception:
        return message, None
    prompt = str(payload.get("prompt") or "")
    return prompt, normalize_moa_config(payload.get("config") or {})


def build_moa_turn_prompt(user_prompt: str, config: Any = None) -> str:
    """Build the hidden turn payload used by /moa slash commands."""
    return encode_moa_turn(user_prompt, config)


def moa_usage() -> str:
    return "Usage: /moa <prompt>"
