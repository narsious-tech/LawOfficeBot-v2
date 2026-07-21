from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ai.config import AIConfig
from ai.prompt_engine import build_instructions
from ai.session_store import AISessionStore


class AIUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AIResult:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class AIGateway:
    def __init__(self, config: AIConfig | None = None, store: AISessionStore | None = None):
        self.config = config or AIConfig.from_env()
        self.store = store or AISessionStore()

    def _client(self):
        if not self.config.enabled:
            raise AIUnavailable("Ajay AI is disabled. Set AI_ENABLED=true.")
        if not self.config.api_key:
            raise AIUnavailable("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIUnavailable("The openai Python package is not installed.") from exc
        return OpenAI(api_key=self.config.api_key, timeout=self.config.timeout_seconds)

    def generate(self, *, user_id: int, session_id: int, user_text: str,
                 feature: str = "general", office_context: str | None = None) -> AIResult:
        started = time.monotonic()
        client = self._client()
        prior = self.store.recent_messages(session_id, limit=8)
        conversation = []
        for message in prior:
            conversation.append(f"{message['role'].upper()}: {message['content']}")
        if office_context:
            conversation.append(f"VERIFIED OFFICE CONTEXT:\n{office_context}")
        conversation.append(f"USER: {user_text}")
        input_text = "\n\n".join(conversation)
        try:
            response = client.responses.create(
                model=self.config.model,
                instructions=build_instructions(feature),
                input=input_text,
                max_output_tokens=self.config.max_output_tokens,
                temperature=self.config.temperature,
            )
            text = (response.output_text or "").strip()
            if not text:
                raise AIUnavailable("OpenAI returned an empty response.")
            usage = getattr(response, "usage", None)
            in_tokens = getattr(usage, "input_tokens", None) if usage else None
            out_tokens = getattr(usage, "output_tokens", None) if usage else None
            total_tokens = getattr(usage, "total_tokens", None) if usage else None
            duration = int((time.monotonic() - started) * 1000)
            self.store.log_usage(session_id=session_id, user_id=user_id, feature=feature,
                                 model=self.config.model, input_tokens=in_tokens,
                                 output_tokens=out_tokens, total_tokens=total_tokens,
                                 duration_ms=duration, status="SUCCESS")
            return AIResult(text=text, model=self.config.model, input_tokens=in_tokens,
                            output_tokens=out_tokens, total_tokens=total_tokens)
        except Exception as exc:
            duration = int((time.monotonic() - started) * 1000)
            try:
                self.store.log_usage(session_id=session_id, user_id=user_id, feature=feature,
                                     model=self.config.model, input_tokens=None, output_tokens=None,
                                     total_tokens=None, duration_ms=duration, status="FAILED",
                                     error_type=type(exc).__name__)
            except Exception:
                pass
            if isinstance(exc, AIUnavailable):
                raise
            raise AIUnavailable(f"AI request failed: {type(exc).__name__}") from exc
