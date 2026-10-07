"""Optional AI rewording of resume bullets (OpenAI-compatible or Azure OpenAI chat API).

Only bullet text and target terminology are sent, never contact details. Every rewrite is
verified against the cited facts by the tailoring service and discarded if it adds anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

SYSTEM_PROMPT = (
    "You edit resume bullets for clarity and concision. You may reorder clauses, tighten wording "
    "and, where the bullet already describes the same thing, use the job description's "
    "terminology. Strict rules: never add a technology, tool, number, metric, employer, team "
    "size, responsibility, leadership claim, seniority or production claim that the original "
    "bullet does not state; copy every number exactly; do not lengthen a bullet by more than "
    "10%. If a bullet cannot be improved under these rules, return it unchanged. Respond only "
    'with JSON: {"rewrites": [{"id": "<id>", "text": "<bullet>"}]}.'
)


@dataclass
class RewriteRequest:
    id: str
    text: str
    focus_terms: list[str]


class LLMError(Exception):
    pass


class LLMRewriter:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport

    @property
    def configured(self) -> bool:
        s = self.settings
        if not (s.llm_provider and s.llm_api_key and s.llm_model):
            return False
        return s.llm_provider == "openai" or bool(s.llm_base_url)

    @property
    def name(self) -> str:
        return f"llm:{self.settings.llm_provider}:{self.settings.llm_model}"

    def _endpoint(self) -> tuple[str, dict[str, str], dict[str, str]]:
        s = self.settings
        if s.llm_provider == "azure":
            base = (s.llm_base_url or "").rstrip("/")
            url = f"{base}/openai/deployments/{s.llm_model}/chat/completions"
            return url, {"api-key": s.llm_api_key or ""}, {"api-version": s.llm_api_version}
        base = (s.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions", {"Authorization": f"Bearer {s.llm_api_key}"}, {}

    async def rewrite(
        self, items: list[RewriteRequest], *, jd_terms: list[str], company: str, role: str
    ) -> dict[str, str]:
        if not self.configured:
            raise LLMError("AI rewording is not configured.")
        if not items:
            return {}
        url, headers, params = self._endpoint()
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "role": role,
                            "company": company,
                            "job_description_terms": jd_terms[:25],
                            "bullets": [
                                {
                                    "id": item.id,
                                    "text": item.text,
                                    "emphasize": item.focus_terms[:6],
                                }
                                for item in items
                            ],
                        }
                    ),
                },
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        if self.settings.llm_provider != "azure":
            payload["model"] = self.settings.llm_model
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self.settings.llm_timeout_seconds
        ) as client:
            try:
                response = await client.post(url, headers=headers, params=params, json=payload)
            except httpx.HTTPError as exc:
                raise LLMError(f"AI rewording request failed: {exc.__class__.__name__}.") from exc
        if response.status_code >= 400:
            raise LLMError(f"AI rewording failed with HTTP {response.status_code}.")
        try:
            message = response.json()["choices"][0]["message"]["content"]
            rewrites = json.loads(message).get("rewrites", [])
        except (KeyError, IndexError, ValueError, TypeError, AttributeError) as exc:
            raise LLMError("AI rewording returned an unreadable response.") from exc
        wanted = {item.id for item in items}
        return {
            str(entry["id"]): str(entry["text"]).strip()
            for entry in rewrites
            if isinstance(entry, dict) and str(entry.get("id")) in wanted and entry.get("text")
        }
