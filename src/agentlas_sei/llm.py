from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from .errors import ConfigurationError, ProviderError

SYSTEM_PROMPT = """You are the semantic investigator inside a software assurance system.
You receive bounded summaries, never source code. Generate competing hypotheses
and the smallest falsifiable next experiment. Do not declare a defect, approve a
repair, or claim user success. Return JSON only with keys hypotheses and caveats.
Each hypothesis must contain statement, discriminator, and expected_observation."""


class LLMProvider(Protocol):
    def investigate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return bounded semantic hypotheses."""


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> OpenAICompatibleProvider:
        base_url = os.environ.get("SEI_LLM_BASE_URL", "").strip()
        model = os.environ.get("SEI_LLM_MODEL", "").strip()
        api_key = os.environ.get("SEI_LLM_API_KEY", "").strip() or None
        if not base_url or not model:
            raise ConfigurationError(
                "Set SEI_LLM_BASE_URL and SEI_LLM_MODEL to enable LLM analysis."
            )
        return cls(base_url=base_url.rstrip("/"), model=model, api_key=api_key)

    def investigate(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        endpoint = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else self.base_url + "/chat/completions"
        )
        http_request = request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(
                http_request, timeout=self.timeout_seconds
            ) as response:
                raw = response.read()
        except (error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"LLM request failed: {exc}") from exc
        try:
            response_value = json.loads(raw)
            content = response_value["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("LLM returned an invalid JSON response.") from exc
        return validate_investigation(result)


def validate_investigation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError("LLM investigation must be an object.")
    hypotheses = value.get("hypotheses")
    caveats = value.get("caveats", [])
    if not isinstance(hypotheses, list) or not isinstance(caveats, list):
        raise ProviderError("LLM investigation has invalid list fields.")
    clean_hypotheses: list[dict[str, str]] = []
    for item in hypotheses[:10]:
        if not isinstance(item, dict):
            continue
        clean = {}
        for key in ("statement", "discriminator", "expected_observation"):
            candidate = item.get(key)
            if not isinstance(candidate, str) or not candidate.strip():
                break
            clean[key] = candidate.strip()[:1000]
        if len(clean) == 3:
            clean_hypotheses.append(clean)
    if not clean_hypotheses:
        raise ProviderError("LLM returned no valid hypotheses.")
    clean_caveats = [
        item.strip()[:500]
        for item in caveats[:20]
        if isinstance(item, str) and item.strip()
    ]
    return {"hypotheses": clean_hypotheses, "caveats": clean_caveats}
