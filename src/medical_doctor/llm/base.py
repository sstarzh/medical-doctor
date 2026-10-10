"""Provider-neutral types for the consult engines."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]
Progress = Callable[[int, str], Awaitable[None]]


@dataclass
class ConsultRun:
    """Inputs for one consult, shared by every provider."""

    charter: str
    skills_text: str
    user_prompt: str
    tools: list[dict[str, Any]]  # {"name", "description", "input_schema"}
    execute: ToolExecutor
    progress: Progress | None = None
    max_rounds: int = 12


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    rounds: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


@dataclass
class ConsultOutcome:
    ok: bool
    provider: str
    model: str
    note: str = ""
    error: str | None = None
    truncated: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    capabilities: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "note": self.note,
            "truncated": self.truncated,
            "tool_calls": self.tool_calls,
            "usage": self.usage.as_dict(),
            "capabilities": self.capabilities,
        }
        if self.error:
            d["error"] = self.error
        return d


class Engine(Protocol):
    provider: str
    model: str

    async def consult(self, run: ConsultRun) -> ConsultOutcome: ...

    async def complete(self, system: str, prompt: str, max_tokens: int = 8000) -> str: ...


async def run_tool_calls(
    calls: list[tuple[str, str, Any]], execute: ToolExecutor, log: list[dict[str, Any]]
) -> list[tuple[str, str, bool]]:
    """Execute (id, name, raw_args) tool calls concurrently. Returns (id, output, is_error)."""

    async def one(call_id: str, name: str, raw: Any) -> tuple[str, str, bool]:
        try:
            args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be a JSON object")
        except (ValueError, TypeError) as exc:
            log.append({"tool": name, "input": raw, "error": True})
            return call_id, f"Invalid tool arguments: {exc}", True
        try:
            out = await execute(name, args)
            log.append({"tool": name, "input": args, "error": False})
            return call_id, out, False
        except Exception as exc:  # surfaced to the model, which can recover
            log.append({"tool": name, "input": args, "error": True})
            return call_id, f"{type(exc).__name__}: {exc}", True

    return list(await asyncio.gather(*(one(*c) for c in calls)))
