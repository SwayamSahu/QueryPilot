"""Minimal structured-output client for free, self-hosted models.

Supported providers (all open source, no paid APIs):
  * ollama            - native /api/chat with JSON-schema constrained decoding
  * openai_compatible - llama.cpp server, vLLM, LM Studio, LocalAI (/v1/chat/completions)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from querypilot.config import get_settings


class LLMError(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    latency_ms: float = 0.0
    calls_detail: list[dict] = field(default_factory=list)

    def add(self, purpose: str, inp: int, out: int, ms: float):
        self.input_tokens += inp
        self.output_tokens += out
        self.calls += 1
        self.latency_ms += ms
        self.calls_detail.append({"purpose": purpose, "input_tokens": inp, "output_tokens": out, "ms": round(ms, 1)})

    def cost_usd(self) -> float:
        s = get_settings()
        return round(self.input_tokens / 1000 * s.llm_usd_per_1k_input + self.output_tokens / 1000 * s.llm_usd_per_1k_output, 6)


class LLM(Protocol):
    model: str

    def complete_json(self, system: str, user: str, schema: dict, usage: Usage, purpose: str) -> dict[str, Any]: ...


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise LLMError(f"model did not return JSON: {text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON from model: {e}") from e


class OllamaLLM:
    def __init__(self, base_url: str, model: str, timeout: float, temperature: float):
        self.base, self.model, self.timeout, self.temperature = base_url.rstrip("/"), model, timeout, temperature

    def complete_json(self, system, user, schema, usage, purpose):
        t0 = time.perf_counter()
        body = {"model": self.model, "stream": False, "format": schema,
                "options": {"temperature": self.temperature, "num_ctx": 8192},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = httpx.post(f"{self.base}/api/chat", json=body, timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"ollama request failed: {e}") from e
        data = r.json()
        usage.add(purpose, int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)), (time.perf_counter() - t0) * 1000)
        return _parse_json(data["message"]["content"])


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, model: str, timeout: float, temperature: float):
        self.base, self.model, self.timeout, self.temperature = base_url.rstrip("/"), model, timeout, temperature
        self._schema_ok = True

    def complete_json(self, system, user, schema, usage, purpose):
        t0 = time.perf_counter()
        body: dict[str, Any] = {"model": self.model, "temperature": self.temperature,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self._schema_ok:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}}
        else:
            body["response_format"] = {"type": "json_object"}
        url = f"{self.base}/v1/chat/completions" if not self.base.endswith("/v1") else f"{self.base}/chat/completions"
        try:
            r = httpx.post(url, json=body, timeout=self.timeout)
            if r.status_code == 400 and self._schema_ok:
                self._schema_ok = False  # server lacks json_schema support; fall back to json mode
                return self.complete_json(system, user, schema, usage, purpose)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"LLM request failed: {e}") from e
        data = r.json()
        u = data.get("usage") or {}
        usage.add(purpose, int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)), (time.perf_counter() - t0) * 1000)
        return _parse_json(data["choices"][0]["message"]["content"])


def build_llm() -> LLM | None:
    s = get_settings()
    if s.llm_provider == "ollama":
        return OllamaLLM(s.llm_base_url, s.llm_model, s.llm_timeout_s, s.llm_temperature)
    if s.llm_provider == "openai_compatible":
        return OpenAICompatibleLLM(s.llm_base_url, s.llm_model, s.llm_timeout_s, s.llm_temperature)
    return None
