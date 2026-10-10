import json
from types import SimpleNamespace

import pytest
from mcp import Client

from medical_doctor import physician
from medical_doctor.llm.anthropic_engine import AnthropicEngine
from medical_doctor.server import mcp


def _payload(result):
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def test_lists_tools_resources_prompts():
    async with Client(mcp) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"consult", "red_flag_screen", "calc_meld_na", "drug_label", "pubmed_search", "get_skill"} <= tools
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert {"attending_consult", "pediatric_consult", "medication_review"} <= prompts
        assert {"calc_pediatric_vital_signs", "calc_weight_based_dose", "calc_pecarn_head_trauma"} <= tools


async def test_calculator_via_mcp():
    async with Client(mcp) as client:
        r = await client.call_tool("calc_cha2ds2_vasc", {"age": 80, "sex": "male", "hypertension": True})
        assert _payload(r)["value"] == 3


async def test_calculator_bad_input_is_tool_error():
    async with Client(mcp) as client:
        r = await client.call_tool("calc_glasgow_coma_scale", {"eye": 9, "verbal": 5, "motor": 6})
        assert r.is_error


async def test_skill_tool_and_resource():
    async with Client(mcp) as client:
        r = await client.call_tool("get_skill", {"name": "cardiology"})
        assert "CHA2DS2-VASc" in r.content[0].text
        res = await client.read_resource("skill://pediatrics")
        assert "Reye" in res.contents[0].text
        res = await client.read_resource("skill://neonatology")
        assert "BRUE" in res.contents[0].text


async def test_consult_without_credentials_returns_protocol(monkeypatch):
    monkeypatch.setattr(physician, "credentials_for", lambda provider: False)
    async with Client(mcp) as client:
        r = await client.call_tool("consult", {"case": "45F, crushing chest pain for 1 hour"})
        p = _payload(r)
        assert p["ok"] is False
        assert p["red_flag_screen"]["urgency"] == "emergency"
        assert "attending" in p["protocol"]


# --- consult loop with a fake Claude client --------------------------------


def _msg(stop_reason, blocks):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, content=blocks, model="fake", usage=usage, stop_details=None)


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot
        return _Stream(self.scripted.pop(0))


async def test_consult_runs_tool_loop(monkeypatch):
    tool_use = SimpleNamespace(type="tool_use", id="tu_1", name="calc_bmi", input={"weight_kg": 90, "height_cm": 180})
    final = SimpleNamespace(type="text", text="## Urgency\nROUTINE")
    fake = FakeClient([_msg("tool_use", [tool_use]), _msg("end_turn", [final])])

    original = physician.run_consult

    async def patched(**kw):
        return await original(**{**kw, "engine": AnthropicEngine(client=fake)})

    monkeypatch.setattr(physician, "credentials_for", lambda provider: True)
    monkeypatch.setattr(physician, "run_consult", patched)
    async with Client(mcp) as client:
        r = await client.call_tool("consult", {"case": "40M wants weight advice", "audience": "patient"})
    p = _payload(r)
    assert p["ok"] is True and p["note"].startswith("## Urgency")
    assert p["tool_calls"] == [{"tool": "calc_bmi", "input": {"weight_kg": 90, "height_cm": 180}, "error": False}]

    # second request carried the tool result computed by the real calculator
    tool_result = fake.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "tu_1" and '"value": 27.8' in tool_result["content"]
    assert tool_result["content"].startswith('<tool_output tool="calc_bmi" trust="untrusted">')
    # consult tool is never offered to the inner model (no recursion)
    names = {t.get("name") for t in fake.calls[0]["tools"]}
    assert "consult" not in names and "calc_bmi" in names
    assert fake.calls[0]["model"] == "claude-opus-5-5"
    # skill library is in the cached system prompt
    assert fake.calls[0]["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


async def test_consult_handles_refusal(monkeypatch):
    fake = FakeClient([_msg("refusal", [])])
    original = physician.run_consult

    async def patched(**kw):
        return await original(**{**kw, "engine": AnthropicEngine(client=fake)})

    monkeypatch.setattr(physician, "credentials_for", lambda provider: True)
    monkeypatch.setattr(physician, "run_consult", patched)
    async with Client(mcp) as client:
        p = _payload(await client.call_tool("consult", {"case": "x"}))
    assert p["ok"] is False


# --- ChatGPT-style search / fetch, progress, rate limit ---------------------


async def test_search_and_fetch(monkeypatch):
    from medical_doctor import sources

    async def no_pubmed(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(sources, "pubmed_search", no_pubmed)
    async with Client(mcp) as client:
        res = _payload(await client.call_tool("search", {"query": "kawasaki fever child"}))["results"]
        ids = [r["id"] for r in res]
        assert "skill:pediatric-infectious-disease" in ids
        assert all({"id", "title", "url"} <= set(r) for r in res)
        doc = _payload(await client.call_tool("fetch", {"id": "skill:pediatric-infectious-disease"}))
        assert "IVIG" in doc["text"] and doc["url"].startswith("https://")
        calc = _payload(await client.call_tool("fetch", {"id": "calc:pecarn_head_trauma"}))
        assert "calc_pecarn_head_trauma" in calc["text"]
        assert (await client.call_tool("fetch", {"id": "nope:1"})).is_error


async def test_tool_annotations_are_accurate():
    # ChatGPT treats tools without readOnlyHint as write actions that need confirmation, so every
    # read tool must say so; tools that change the chart must NOT claim to be read-only.
    writes = {"patient_create", "patient_update", "patient_delete_item", "patient_erase", "care_plan_update",
              "draft_prescription", "draft_referral", "patient_import_fhir"}
    destructive = {"patient_delete_item", "patient_erase"}
    async with Client(mcp) as client:
        for t in (await client.list_tools()).tools:
            assert t.annotations is not None, t.name
            assert t.annotations.read_only_hint == (t.name not in writes), t.name
            assert bool(t.annotations.destructive_hint) == (t.name in destructive), t.name


async def test_consult_reports_progress(monkeypatch):
    tool_use = SimpleNamespace(type="tool_use", id="tu_1", name="calc_bmi", input={"weight_kg": 90, "height_cm": 180})
    fake = FakeClient([_msg("tool_use", [tool_use]), _msg("end_turn", [SimpleNamespace(type="text", text="ok")])])
    seen = []
    original = physician.run_consult

    async def patched(**kw):
        inner = kw["progress"]

        async def spy(step, message):
            seen.append(message)
            await inner(step, message)

        return await original(**{**kw, "progress": spy, "engine": AnthropicEngine(client=fake)})

    monkeypatch.setattr(physician, "credentials_for", lambda provider: True)
    monkeypatch.setattr(physician, "run_consult", patched)
    async with Client(mcp) as client:
        await client.call_tool("consult", {"case": "x"})
    assert seen[0].startswith("Attending") and "calc_bmi" in seen[1]


async def test_rate_limiter():
    from mcp.server.mcpserver.exceptions import ToolError

    from medical_doctor.server import RateLimiter

    rl = RateLimiter(2)
    await rl.check("a")
    await rl.check("a")
    await rl.check("b")
    with pytest.raises(ToolError):
        await rl.check("a")
    await RateLimiter(0).check("a")  # unlimited
