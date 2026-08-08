from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

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

    def _openai_client(self):
        if not self.config.openai_api_key:
            raise AIUnavailable("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIUnavailable("The openai Python package is not installed.") from exc
        return OpenAI(
            api_key=self.config.openai_api_key,
            timeout=self.config.timeout_seconds,
        )

    def _model_for_feature(self, feature: str) -> str:
        if (
            self.config.provider == "gemini"
            and feature.strip().lower() in self.config.pro_features
        ):
            return self.config.pro_model
        return self.config.model

    def _generate_openai(self, *, model: str, instructions: str, input_text: str) -> AIResult:
        client = self._openai_client()
        request_args = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": self.config.max_output_tokens,
        }
        if not model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
            request_args["temperature"] = self.config.temperature
        response = client.responses.create(**request_args)
        text = (response.output_text or "").strip()
        if not text:
            raise AIUnavailable("OpenAI returned an empty response.")
        usage = getattr(response, "usage", None)
        return AIResult(
            text=text,
            model=model,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            total_tokens=getattr(usage, "total_tokens", None) if usage else None,
        )

    @staticmethod
    def _gemini_error(response: requests.Response) -> AIUnavailable:
        status = response.status_code
        detail = ""
        try:
            payload = response.json()
            error = payload.get("error") or {}
            detail = str(error.get("message") or error.get("status") or "").strip()
        except Exception:
            detail = (response.text or "").strip()

        detail = " ".join(detail.split())[:700]
        if status == 400:
            hint = "Check GEMINI_MODEL and request parameters."
        elif status in (401, 403):
            hint = "Check GEMINI_API_KEY and Google AI API permissions."
        elif status == 404:
            hint = "The configured Gemini model/endpoint was not found. Check GEMINI_MODEL."
        elif status == 429:
            hint = "Gemini quota/rate limit reached. Check Google AI quota/billing."
        elif status >= 500:
            hint = "Gemini service returned a server error; retry shortly."
        else:
            hint = "Check Gemini configuration and API availability."

        message = f"Gemini HTTP {status}. {hint}"
        if detail:
            message += f" Provider message: {detail}"
        return AIUnavailable(message)

    def _generate_gemini(self, *, model: str, instructions: str, input_text: str) -> AIResult:
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        try:
            response = requests.post(
                endpoint,
                headers={
                    "x-goog-api-key": self.config.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "systemInstruction": {"parts": [{"text": instructions}]},
                    "contents": [{"role": "user", "parts": [{"text": input_text}]}],
                    "generationConfig": {
                        "maxOutputTokens": self.config.max_output_tokens,
                        "temperature": self.config.temperature,
                    },
                },
                timeout=(10, self.config.timeout_seconds),
            )
        except requests.Timeout as exc:
            raise AIUnavailable(
                f"Gemini request timed out after {self.config.timeout_seconds}s."
            ) from exc
        except requests.RequestException as exc:
            raise AIUnavailable(
                f"Gemini network request failed: {type(exc).__name__}: {str(exc)[:500]}"
            ) from exc

        if not response.ok:
            raise self._gemini_error(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise AIUnavailable(
                f"Gemini returned invalid JSON (HTTP {response.status_code})."
            ) from exc

        parts = []
        for candidate in payload.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                if isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))
        text = "\n".join(parts).strip()
        if not text:
            reason = (payload.get("promptFeedback") or {}).get("blockReason")
            finish_reasons = [
                str(c.get("finishReason"))
                for c in (payload.get("candidates") or [])
                if c.get("finishReason")
            ]
            suffix_parts = []
            if reason:
                suffix_parts.append(f"blockReason={reason}")
            if finish_reasons:
                suffix_parts.append("finishReason=" + ",".join(finish_reasons))
            suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
            raise AIUnavailable(f"Gemini returned an empty response{suffix}.")

        usage = payload.get("usageMetadata") or {}
        input_tokens = usage.get("promptTokenCount")
        output_tokens = usage.get("candidatesTokenCount")
        thinking_tokens = usage.get("thoughtsTokenCount")
        if output_tokens is not None and thinking_tokens is not None:
            output_tokens = int(output_tokens) + int(thinking_tokens)
        return AIResult(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=usage.get("totalTokenCount"),
        )

    def generate(self, *, user_id: int, session_id: int, user_text: str,
                 feature: str = "general", office_context: str | None = None) -> AIResult:
        started = time.monotonic()
        if not self.config.enabled:
            raise AIUnavailable("Ajay AI is disabled. Set AI_ENABLED=true.")
        if not self.config.api_key:
            required = "GEMINI_API_KEY" if self.config.provider == "gemini" else "OPENAI_API_KEY"
            raise AIUnavailable(f"{required} is not configured.")
        prior = self.store.recent_messages(session_id, limit=8)
        conversation = []
        for message in prior:
            conversation.append(f"{message['role'].upper()}: {message['content']}")
        if office_context:
            conversation.append(f"VERIFIED OFFICE CONTEXT:\n{office_context}")
        conversation.append(f"USER: {user_text}")
        input_text = "\n\n".join(conversation)
        instructions = build_instructions(feature)
        selected_model = self._model_for_feature(feature)
        try:
            if self.config.provider == "gemini":
                try:
                    result = self._generate_gemini(
                        model=selected_model,
                        instructions=instructions,
                        input_text=input_text,
                    )
                except Exception:
                    if not (
                        self.config.openai_fallback_enabled
                        and self.config.openai_api_key
                    ):
                        raise
                    result = self._generate_openai(
                        model=self.config.openai_model,
                        instructions=instructions,
                        input_text=input_text,
                    )
            else:
                result = self._generate_openai(
                    model=selected_model,
                    instructions=instructions,
                    input_text=input_text,
                )
            duration = int((time.monotonic() - started) * 1000)
            self.store.log_usage(session_id=session_id, user_id=user_id, feature=feature,
                                 model=result.model, input_tokens=result.input_tokens,
                                 output_tokens=result.output_tokens, total_tokens=result.total_tokens,
                                 duration_ms=duration, status="SUCCESS")
            return result
        except Exception as exc:
            duration = int((time.monotonic() - started) * 1000)
            try:
                self.store.log_usage(session_id=session_id, user_id=user_id, feature=feature,
                                     model=selected_model, input_tokens=None, output_tokens=None,
                                     total_tokens=None, duration_ms=duration, status="FAILED",
                                     error_type=type(exc).__name__)
            except Exception:
                pass
            if isinstance(exc, AIUnavailable):
                raise
            raise AIUnavailable(
                f"AI request failed: {type(exc).__name__}: {str(exc)[:700]}"
            ) from exc
