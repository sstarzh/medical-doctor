"""OpenAI, Meta and Llama consult engine (OpenAI SDK, Chat Completions + function calling).

Chat Completions is used because it is the one tool-calling API that OpenAI, the Meta Model
API (Muse Spark, https://api.meta.ai/v1) and every common open-weight Llama host (Together,
Groq, Fireworks, vLLM, Bedrock's OpenAI-compatible endpoint) all implement.

Capabilities compared with the Anthropic engine:
  * no server-side guideline web search — the model uses the PubMed/FDA/ClinicalTrials tools;
  * no explicit prompt-cache control (OpenAI caches long prefixes automatically);
  * refusals are reported via `message.refusal` where the provider supports it.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .base import ConsultOutcome, ConsultRun, run_tool_calls

log = logging.getLogger("medical_doctor.llm")
_UNSET = object()
# (base_url, model) -> the reasoning_effort that host accepted after rejecting the requested one.
_EFFORT_OVERRIDE: dict[tuple[str, str], str | None] = {}


class OpenAICompatEngine:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        max_output_tokens: int = 16000,
        timeout: float = 240.0,
        client: Any = None,
    ):
        self.provider = provider
        self.model = model
        self._key = (base_url or "", model)
        known = _EFFORT_OVERRIDE.get(self._key, _UNSET)
        self.reasoning_effort = reasoning_effort if known is _UNSET or reasoning_effort is None else known
        self.max_output_tokens = max_output_tokens
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)
        self.client = client

    def _params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"model": self.model, "max_completion_tokens": self.max_output_tokens}
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        return params

    async def _create(self, **kwargs: Any) -> Any:
        """chat.completions.create. If the host rejects the request while reasoning_effort is set (400/422),
        retry with a lower supported level, then without it; the first that works is remembered for this
        host and model, so later consults skip the failing request. A failure that persists without the
        parameter was never about it and is raised."""
        try:
            return await self.client.chat.completions.create(**{**self._params(), **kwargs})
        except Exception as exc:
            if self.reasoning_effort is None or getattr(exc, "status_code", None) not in (400, 422):
                raise
            first, requested = exc, self.reasoning_effort
        fallbacks: list[str | None] = ["high"] if self.reasoning_effort in ("xhigh", "max") else []
        for effort in [*fallbacks, None]:
            self.reasoning_effort = effort
            try:
                resp = await self.client.chat.completions.create(**{**self._params(), **kwargs})
            except Exception as exc:
                if getattr(exc, "status_code", None) not in (400, 422):
                    raise
                continue
            _EFFORT_OVERRIDE[self._key] = effort
            log.warning("%s %s rejected reasoning_effort; using %s", self.provider, self.model, effort or "the model default")
            return resp
        self.reasoning_effort = requested  # the parameter was not the problem
        raise first

    async def complete(self, system: str, prompt: str, max_tokens: int = 8000) -> str:
        resp = await self._create(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_completion_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    async def consult(self, run: ConsultRun) -> ConsultOutcome:
        out = ConsultOutcome(ok=False, provider=self.provider, model=self.model, capabilities=["tools", "parallel_tools"])
        tools = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"][:1024],
                                              "parameters": t["input_schema"]}}
            for t in run.tools
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": run.charter + "\n\n# Clinical skill library\n" + run.skills_text},
            {"role": "user", "content": run.user_prompt if not run.images else [
                {"type": "image_url", "image_url": {"url": i.get("url") or f"data:{i['media_type']};base64,{i['data']}"}}
                for i in run.images
            ] + [{"type": "text", "text": run.user_prompt}]},
        ]
        if run.progress:
            await run.progress(0, "Attending reviewing the case")
        for round_no in range(run.max_rounds + 1):
            final_round = round_no == run.max_rounds
            if final_round:  # out of tool budget: force a written answer instead of discarding the work
                messages.append({"role": "user", "content": "Tool budget exhausted. Write the consult note now from the evidence gathered; mark anything unverified."})
            t0 = time.monotonic()
            resp = await self._create(messages=messages, tools=tools, tool_choice="none" if final_round else "auto")
            out.usage.last_round_seconds = time.monotonic() - t0
            out.usage.model_seconds += out.usage.last_round_seconds
            out.usage.rounds += 1
            if resp.usage is not None:
                out.usage.input_tokens += resp.usage.prompt_tokens or 0
                out.usage.output_tokens += resp.usage.completion_tokens or 0
                details = getattr(resp.usage, "completion_tokens_details", None)
                out.usage.reasoning_tokens += getattr(details, "reasoning_tokens", 0) or 0
            choice = resp.choices[0]
            msg = choice.message
            if getattr(msg, "refusal", None):
                out.error = f"The model declined this request: {msg.refusal}"
                return out
            calls = [] if final_round else (msg.tool_calls or [])
            if not calls:
                note = (msg.content or "").strip()
                if not note:
                    out.error = "The model returned no consult note."
                    return out
                out.ok = True
                out.note = note
                out.truncated = choice.finish_reason == "length"
                return out
            messages.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ],
            })
            if run.progress:
                await run.progress(round_no + 1, "Verifying with " + ", ".join(c.function.name for c in calls))
            t0 = time.monotonic()
            results = await run_tool_calls([(c.id, c.function.name, c.function.arguments) for c in calls], run.execute, out.tool_calls)
            out.usage.tool_seconds += time.monotonic() - t0
            for call_id, text, is_error in results:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": ("ERROR: " if is_error else "") + text})
        out.error = "The model returned no consult note."
        return out


