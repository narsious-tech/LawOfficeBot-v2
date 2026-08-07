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
    provider: str
    api_key: str
    model: str
    pro_model: str
    pro_features: frozenset[str]
    openai_api_key: str
    openai_model: str
    openai_fallback_enabled: bool
    max_output_tokens: int
    temperature: float
    timeout_seconds: int
    admin_user_ids: frozenset[int]

    @classmethod
    def from_env(cls) -> "AIConfig":
        provider = (os.getenv("AI_PROVIDER", "openai") or "openai").strip().lower()
        if provider not in {"openai", "gemini"}:
            provider = "openai"
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_model = os.getenv("OPENAI_MODEL", "gpt-5.5").strip()
        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        gemini_pro_model = os.getenv("GEMINI_PRO_MODEL", "gemini-2.5-pro").strip()
        raw_pro_features = os.getenv(
            "GEMINI_PRO_FEATURES",
            "general,case_intelligence,hearing_intelligence",
        )
        pro_features = frozenset(
            item.strip().lower() for item in raw_pro_features.split(",") if item.strip()
        )
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
            provider=provider,
            api_key=gemini_api_key if provider == "gemini" else openai_api_key,
            model=gemini_model if provider == "gemini" else openai_model,
            pro_model=gemini_pro_model if provider == "gemini" else openai_model,
            pro_features=pro_features if provider == "gemini" else frozenset(),
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            openai_fallback_enabled=_bool("AI_OPENAI_FALLBACK_ENABLED", False),
            max_output_tokens=max(256, _int("AI_MAX_OUTPUT_TOKENS", 1800)),
            temperature=min(1.0, max(0.0, _float("AI_TEMPERATURE", 0.2))),
            timeout_seconds=max(10, _int("AI_TIMEOUT_SECONDS", 90)),
            admin_user_ids=frozenset(parsed),
        )
