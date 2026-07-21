from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class AIConfig:
    enabled: bool
    api_key: str
    model: str
    max_output_tokens: int
    temperature: float
    timeout_seconds: int
    admin_user_ids: frozenset[int]

    @classmethod
    def from_env(cls) -> "AIConfig":
        raw_ids = os.getenv("AI_ADMIN_USER_IDS", "")
        parsed: set[int] = set()
        for item in raw_ids.split(","):
            item = item.strip()
            if item.lstrip("-").isdigit():
                parsed.add(int(item))
        fallback = os.getenv("ADMIN_CHAT_ID", "").strip()
        if fallback.lstrip("-").isdigit():
            parsed.add(int(fallback))
        return cls(
            enabled=_bool("AI_ENABLED", False),
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            model=os.getenv("OPENAI_MODEL", "gpt-5.5").strip(),
            max_output_tokens=max(256, _int("AI_MAX_OUTPUT_TOKENS", 1800)),
            temperature=min(1.0, max(0.0, _float("AI_TEMPERATURE", 0.2))),
            timeout_seconds=max(10, _int("AI_TIMEOUT_SECONDS", 90)),
            admin_user_ids=frozenset(parsed),
        )
