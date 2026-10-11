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
    images: list[dict[str, str]] = field(default_factory=list)  # validated by normalize_images


MAX_IMAGES = 4
MAX_IMAGE_B64_CHARS = 7_000_000  # ~5 MB decoded
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def normalize_images(images: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Validate photos for a consult: {"url": "https://..."} or {"data": <base64>, "media_type": "image/png"}.
    URLs are fetched by the model provider, never by this server (no SSRF surface here)."""
    import base64
    import binascii
    from urllib.parse import urlparse

    out: list[dict[str, str]] = []
    for img in (images or [])[: MAX_IMAGES + 1]:
        if len(out) == MAX_IMAGES:
            raise ValueError(f"At most {MAX_IMAGES} images per consult")
        if url := img.get("url"):
            if urlparse(url).scheme != "https":
                raise ValueError("Image URLs must be https")
            out.append({"url": url})
            continue
        data, media = str(img.get("data") or ""), str(img.get("media_type") or "")
        if media not in IMAGE_TYPES:
            raise ValueError(f"media_type must be one of {', '.join(IMAGE_TYPES)}")
        if not data or len(data) > MAX_IMAGE_B64_CHARS:
            raise ValueError("Image data missing or larger than about 5 MB")
        try:
            base64.b64decode(data[:4096] + "=" * (-len(data[:4096]) % 4), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Image data must be base64") from None
        out.append({"data": data, "media_type": media})
    return out


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    rounds: int = 0
    model_seconds: float = 0.0  # wall-clock waiting on the model, summed over rounds
    tool_seconds: float = 0.0  # wall-clock running tool calls (each round's calls run concurrently)

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "model_seconds": round(self.model_seconds, 1), "tool_seconds": round(self.tool_seconds, 1)}


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
