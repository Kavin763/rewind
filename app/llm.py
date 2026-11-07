"""LLM access through Groq's OpenAI-compatible endpoint.

Open models sometimes return broken JSON or fail structured-output validation,
so every call goes through three layers of defence: JSON mode, a retry without
JSON mode, then a fallback model. Whatever comes back is parsed loosely.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("rewind.llm")


class LLMError(RuntimeError):
    pass


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_loose(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply that may contain reasoning, fences or prose."""
    if not raw or not raw.strip():
        raise LLMError("empty reply")
    text = _THINK.sub("", raw).strip()
    text = _FENCE.sub("", text).strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} block.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            # Trailing commas are the most common defect.
            fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                value = json.loads(fixed)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                pass
    raise LLMError(f"could not parse JSON from reply: {raw[:200]!r}")


class GroqLLM:
    def __init__(self, api_key: str, base_url: str, model: str, fallback_model: str | None = None):
        from openai import AsyncOpenAI  # lazy import so tests don't need the package

        if not api_key:
            raise LLMError("GROQ_API_KEY is not set")
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=1)
        self.model = model
        self.fallback_model = fallback_model
        self.last_model = model

    @classmethod
    def from_settings(cls, settings: Any) -> "GroqLLM":
        return cls(settings.llm_api_key, settings.llm_base_url, settings.llm_model, settings.llm_fallback_model)

    async def _call(self, model: str, messages: list[dict[str, str]], json_mode: bool) -> str:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def complete_json(self, system: str, user: str) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        attempts = [(self.model, True), (self.model, False)]
        if self.fallback_model and self.fallback_model != self.model:
            attempts.append((self.fallback_model, False))
        errors: list[str] = []
        for model, json_mode in attempts:
            try:
                raw = await self._call(model, messages, json_mode)
                result = parse_json_loose(raw)
                self.last_model = model
                return result
            except Exception as exc:
                log.warning("LLM attempt failed (model=%s json_mode=%s): %s", model, json_mode, exc)
                errors.append(f"{model}: {exc}")
        raise LLMError("all LLM attempts failed: " + " | ".join(errors))
