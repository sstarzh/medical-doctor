"""Anthropic (Claude) consult engine — the default and most capable configuration.

Uses features the other providers lack: prompt caching of the skill library and of the growing
conversation, server-side guideline web search restricted to trusted publishers, server-side
refusal fallbacks, and adaptive thinking controlled by effort.
"""

from __future__ import annotations

import asyncio
from typing import Any

import anthropic

from .base import ConsultOutcome, ConsultRun, run_tool_calls

# Guideline publishers only. Broad hosts with user-generated content (e.g. comment sections,
# rapid responses) are deliberately excluded to reduce prompt-injection exposure.
GUIDELINE_DOMAINS = [
    "cdc.gov", "fda.gov", "ncbi.nlm.nih.gov", "who.int", "nice.org.uk", "uspreventiveservicestaskforce.org",
    "acc.org", "ahajournals.org", "idsociety.org", "acog.org", "aap.org", "diabetesjournals.org", "kdigo.org",
    "escardio.org", "cochranelibrary.com", "nejm.org", "jamanetwork.com",
]

FINAL_NUDGE = "Tool budget exhausted. Write the consult note now from the evidence gathered; mark anything unverified."

_clients: dict[int, anthropic.AsyncAnthropic] = {}


def shared_client(timeout: float) -> anthropic.AsyncAnthropic:
    """One client (and HTTP connection pool) per event loop instead of one per consult."""
    loop_id = id(asyncio.get_running_loop())
    client = _clients.get(loop_id)
    if client is None:
        _clients.clear()
        client = anthropic.AsyncAnthropic(timeout=timeout, max_retries=1)
        _clients[loop_id] = client
    return client


class AnthropicEngine:
    provider = "anthropic"

    def __init__(
        self,
        *,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        web_search: bool = True,
        web_search_uses: int = 3,
        fast: bool = False,
        fallback_model: str | None = None,
        request_timeout: float = 240.0,
        client: Any = None,
    ):
        self.model = model
        self.effort = effort
        self.web_search = web_search
        self.web_search_uses = web_search_uses
        self.fast = fast
        self.fallback_model = fallback_model
        self.request_timeout = request_timeout
        self._client = client

    @property
    def client(self) -> Any:
        return self._client or shared_client(self.request_timeout)

    def _common(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "output_config": {"effort": self.effort},
            # Server-side refusal fallback: a declined clinical question is retried on Anthropic's
            # recommended fallback model instead of failing the consult.
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
        if self.fast:
            params["betas"] = [*params["betas"], "fast-mode-2026-02-01"]
            params["speed"] = "fast"
        return params

    async def _final_message(self, **kwargs: Any) -> Any:
        """One streamed request. Fast mode has its own rate limit and is Claude-API-only: on a 429
        or a rejection, drop to standard speed for the rest of this consult instead of failing."""
        try:
            async with self.client.beta.messages.stream(**kwargs, **self._common()) as stream:
                return await stream.get_final_message()
        except (anthropic.RateLimitError, anthropic.BadRequestError) as exc:
            if self.fast:
                self.fast = False
            elif self.fallback_model and isinstance(exc, anthropic.BadRequestError) and self.model != self.fallback_model:
                self.model = self.fallback_model  # e.g. the reviewer model is not available to this account
            else:
                raise
            async with self.client.beta.messages.stream(**kwargs, **self._common()) as stream:
                return await stream.get_final_message()

    async def complete(self, system: str, prompt: str, max_tokens: int = 8000) -> str:
        msg = await self._final_message(max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": prompt}])
        if msg.stop_reason == "refusal":
            raise RuntimeError("The model declined this request.")
        return "\n".join(b.text for b in msg.content if b.type == "text").strip()

    async def consult(self, run: ConsultRun) -> ConsultOutcome:
        out = ConsultOutcome(
            ok=False, provider=self.provider, model=self.model,
            capabilities=["tools", "parallel_tools", "prompt_cache", "refusal_fallback"] + (["guideline_web_search"] if self.web_search else []) + (["fast_mode"] if self.fast else []),
        )
        tools: list[dict[str, Any]] = list(run.tools)
        if self.web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": self.web_search_uses, "allowed_domains": GUIDELINE_DOMAINS})
        system = [
            {"type": "text", "text": run.charter},
            {"type": "text", "text": "# Clinical skill library\nApply the relevant skills below to every case.\n\n" + run.skills_text,
             # 1 h: consults are sporadic, so the default 5-minute cache would usually be cold.
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": run.user_prompt}]
        if run.progress:
            await run.progress(0, "Attending reviewing the case")

        response = None
        for round_no in range(run.max_rounds + 1):
            final_round = round_no == run.max_rounds
            extra: dict[str, Any] = {}
            if final_round:
                # Out of tool budget: force a written answer from what was gathered instead of
                # throwing the whole consult away.
                extra["tool_choice"] = {"type": "none"}
                nudge = {"type": "text", "text": FINAL_NUDGE}
                last = messages[-1]
                if last["role"] == "user" and isinstance(last["content"], list):
                    last["content"].append(nudge)
                else:
                    messages.append({"role": "user", "content": [nudge]})
            response = await self._final_message(
                max_tokens=64000,
                system=system,
                tools=tools,
                messages=messages,
                # Automatic breakpoint on the conversation, so tool results (drug labels,
                # abstracts) are not re-billed at full price every round.
                cache_control={"type": "ephemeral"},
                **extra,
            )
            out.usage.rounds += 1
            u = response.usage
            out.usage.input_tokens += u.input_tokens or 0
            out.usage.output_tokens += u.output_tokens or 0
            out.usage.cache_read_input_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
            out.model = getattr(response, "model", self.model) or self.model

            if response.stop_reason == "refusal":
                out.error = "The model declined this request."
                return out
            if response.stop_reason == "model_context_window_exceeded":
                out.error = "The case and gathered evidence exceeded the model's context window."
                return out
            # Append-only history keeps thinking blocks valid and the cache warm.
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use" or final_round:
                break
            calls = [(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
            if run.progress:
                await run.progress(round_no + 1, "Verifying with " + ", ".join(c[1] for c in calls))
            results = await run_tool_calls(calls, run.execute, out.tool_calls)
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": cid, "content": text, "is_error": err} for cid, text, err in results],
            })

        note = "\n".join(b.text for b in response.content if b.type == "text").strip() if response else ""
        if not note:
            out.error = "The model returned no consult note."
            return out
        out.ok = True
        out.note = note
        out.truncated = response.stop_reason == "max_tokens"
        return out
