from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import requests

from ai.config import AIConfig
from ai.prompt_engine import build_instructions
from ai.session_store import AISessionStore

logger = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    pass


class GeminiHTTPError(AIUnavailable):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = int(status_code)


@dataclass(frozen=True)
class AIResult:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class AIGateway:
    def __init__(
        self,
        config: AIConfig | None = None,
        store: AISessionStore | None = None,
    ):
        self.config = config or AIConfig.from_env()
        self.store = store or AISessionStore()

    def _openai_client(self):
        if not self.config.openai_api_key:
            raise AIUnavailable("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIUnavailable(
                "The openai Python package is not installed."
            ) from exc
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

    def _generate_openai(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ) -> AIResult:
        client = self._openai_client()
        request_args: dict[str, Any] = {
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
            input_tokens=(
                getattr(usage, "input_tokens", None) if usage else None
            ),
            output_tokens=(
                getattr(usage, "output_tokens", None) if usage else None
            ),
            total_tokens=(
                getattr(usage, "total_tokens", None) if usage else None
            ),
        )

    @staticmethod
    def _gemini_error(response: requests.Response) -> GeminiHTTPError:
        status = response.status_code
        detail = ""
        try:
            payload = response.json()
            error = payload.get("error") or {}
            detail = str(
                error.get("message") or error.get("status") or ""
            ).strip()
        except Exception:
            detail = (response.text or "").strip()

        detail = " ".join(detail.split())[:700]
        if status == 400:
            hint = "Check GEMINI_MODEL and request parameters."
        elif status in (401, 403):
            hint = "Check GEMINI_API_KEY and Google AI API permissions."
        elif status == 404:
            hint = (
                "The configured Gemini model/endpoint was not found. "
                "Check GEMINI_MODEL."
            )
        elif status == 429:
            hint = (
                "Gemini quota/rate limit reached. "
                "Check Google AI quota/billing."
            )
        elif status >= 500:
            hint = "Gemini service returned a server error; retry shortly."
        else:
            hint = "Check Gemini configuration and API availability."

        message = f"Gemini HTTP {status}. {hint}"
        if detail:
            message += f" Provider message: {detail}"
        return GeminiHTTPError(status, message)

    def _generate_gemini(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ) -> AIResult:
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )

        # Gemini 3.5+ deprecates sampling parameters. Omitting temperature
        # works with both current 2.5 and newer Gemini text models.
        generation_config = {
            "maxOutputTokens": self.config.max_output_tokens,
        }

        try:
            response = requests.post(
                endpoint,
                headers={
                    "x-goog-api-key": self.config.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "systemInstruction": {
                        "parts": [{"text": instructions}]
                    },
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": input_text}],
                        }
                    ],
                    "generationConfig": generation_config,
                },
                timeout=(10, self.config.timeout_seconds),
            )
        except requests.Timeout as exc:
            raise AIUnavailable(
                f"Gemini request timed out after "
                f"{self.config.timeout_seconds}s."
            ) from exc
        except requests.RequestException as exc:
            raise AIUnavailable(
                "Gemini network request failed: "
                f"{type(exc).__name__}: {str(exc)[:500]}"
            ) from exc

        if not response.ok:
            raise self._gemini_error(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise AIUnavailable(
                f"Gemini returned invalid JSON "
                f"(HTTP {response.status_code})."
            ) from exc

        parts: list[str] = []
        for candidate in payload.get("candidates") or []:
            for part in (
                (candidate.get("content") or {}).get("parts") or []
            ):
                if isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))

        text = "\n".join(parts).strip()
        if not text:
            reason = (
                payload.get("promptFeedback") or {}
            ).get("blockReason")
            finish_reasons = [
                str(c.get("finishReason"))
                for c in (payload.get("candidates") or [])
                if c.get("finishReason")
            ]
            suffix_parts: list[str] = []
            if reason:
                suffix_parts.append(f"blockReason={reason}")
            if finish_reasons:
                suffix_parts.append(
                    "finishReason=" + ",".join(finish_reasons)
                )
            suffix = (
                f" ({'; '.join(suffix_parts)})"
                if suffix_parts
                else ""
            )
            raise AIUnavailable(
                f"Gemini returned an empty response{suffix}."
            )

        usage = payload.get("usageMetadata") or {}
        input_tokens = usage.get("promptTokenCount")
        output_tokens = usage.get("candidatesTokenCount")
        thinking_tokens = usage.get("thoughtsTokenCount")
        if (
            output_tokens is not None
            and thinking_tokens is not None
        ):
            output_tokens = int(output_tokens) + int(thinking_tokens)

        return AIResult(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=usage.get("totalTokenCount"),
        )

    @staticmethod
    def _retryable_gemini_error(exc: Exception) -> bool:
        if isinstance(exc, GeminiHTTPError):
            return exc.status_code == 429 or exc.status_code >= 500

        # Network timeouts/errors are wrapped as AIUnavailable. They are
        # reasonable transient failures to retry before provider fallback.
        text = str(exc).lower()
        return (
            "timed out" in text
            or "network request failed" in text
            or "connection" in text
        )

    def _sleep_before_retry(self, retry_number: int) -> None:
        # Exponential backoff + small jitter. retry_number starts at 1.
        base = self.config.retry_base_seconds * (2 ** (retry_number - 1))
        delay = min(15.0, base) + random.uniform(0.0, min(0.5, base / 4))
        time.sleep(delay)

    def _gemini_models(self, selected_model: str) -> list[str]:
        ordered = [selected_model, *self.config.gemini_fallback_models]
        result: list[str] = []
        seen: set[str] = set()
        for model in ordered:
            model = (model or "").strip()
            if model and model not in seen:
                seen.add(model)
                result.append(model)
        return result

    def _generate_gemini_resilient(
        self,
        *,
        selected_model: str,
        instructions: str,
        input_text: str,
    ) -> AIResult:
        errors: list[str] = []

        for model in self._gemini_models(selected_model):
            for attempt in range(1, self.config.retry_attempts + 1):
                try:
                    result = self._generate_gemini(
                        model=model,
                        instructions=instructions,
                        input_text=input_text,
                    )
                    if model != selected_model or attempt > 1:
                        logger.warning(
                            "Ajay AI recovered with Gemini model=%s "
                            "attempt=%s",
                            model,
                            attempt,
                        )
                    return result
                except Exception as exc:
                    errors.append(
                        f"{model} attempt {attempt}: "
                        f"{type(exc).__name__}: {str(exc)[:350]}"
                    )
                    retryable = self._retryable_gemini_error(exc)

                    # A 400/401/403/404 is not helped by retrying the same
                    # endpoint. Move to the next configured Gemini model.
                    if not retryable:
                        break

                    if attempt < self.config.retry_attempts:
                        self._sleep_before_retry(attempt)

        tail = " | ".join(errors[-4:])
        raise AIUnavailable(
            "Gemini primary and fallback models are unavailable. "
            f"{tail[:1400]}"
        )

    def generate(
        self,
        *,
        user_id: int,
        session_id: int,
        user_text: str,
        feature: str = "general",
        office_context: str | None = None,
    ) -> AIResult:
        started = time.monotonic()

        if not self.config.enabled:
            raise AIUnavailable(
                "Ajay AI is disabled. Set AI_ENABLED=true."
            )

        if self.config.provider == "gemini" and not self.config.api_key:
            raise AIUnavailable("GEMINI_API_KEY is not configured.")
        if self.config.provider == "openai" and not self.config.api_key:
            raise AIUnavailable("OPENAI_API_KEY is not configured.")

        prior = self.store.recent_messages(session_id, limit=8)
        conversation: list[str] = []
        for message in prior:
            conversation.append(
                f"{message['role'].upper()}: {message['content']}"
            )
        if office_context:
            conversation.append(
                f"VERIFIED OFFICE CONTEXT:\n{office_context}"
            )
        conversation.append(f"USER: {user_text}")

        input_text = "\n\n".join(conversation)
        instructions = build_instructions(feature)
        selected_model = self._model_for_feature(feature)
        attempted_model = selected_model

        try:
            if self.config.provider == "gemini":
                try:
                    result = self._generate_gemini_resilient(
                        selected_model=selected_model,
                        instructions=instructions,
                        input_text=input_text,
                    )
                except Exception as gemini_exc:
                    if not (
                        self.config.openai_fallback_enabled
                        and self.config.openai_api_key
                    ):
                        raise

                    logger.warning(
                        "Gemini exhausted; switching Ajay AI to OpenAI "
                        "fallback: %s",
                        str(gemini_exc)[:500],
                    )
                    attempted_model = self.config.openai_model
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

            duration = int(
                (time.monotonic() - started) * 1000
            )
            self.store.log_usage(
                session_id=session_id,
                user_id=user_id,
                feature=feature,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
                duration_ms=duration,
                status="SUCCESS",
            )
            return result

        except Exception as exc:
            duration = int(
                (time.monotonic() - started) * 1000
            )
            try:
                self.store.log_usage(
                    session_id=session_id,
                    user_id=user_id,
                    feature=feature,
                    model=attempted_model,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    duration_ms=duration,
                    status="FAILED",
                    error_type=type(exc).__name__,
                )
            except Exception:
                pass

            if isinstance(exc, AIUnavailable):
                raise

            raise AIUnavailable(
                "AI request failed: "
                f"{type(exc).__name__}: {str(exc)[:700]}"
            ) from exc
