# Medical Doctor MCP

An MCP server that gives Claude, OpenClaw, or any other MCP-capable agent an **attending-physician-grade clinical consult service**.

It has three layers:

1. **A deterministic safety layer.** A red-flag screen catches emergencies such as stroke, ACS, sepsis, anaphylaxis, suicide risk, obstetric and neonatal emergencies, and cauda equina. It runs before any model reasoning and still works when no LLM is available.
2. **Verified tools.** There are 18 validated clinical calculators, plus live FDA drug labels, label-based interaction screening, RxNorm, PubMed, and ClinicalTrials.gov. The physician is required to compute scores and confirm doses with these tools instead of answering from memory.
3. **The attending.** Claude Opus 5.5 runs with a physician charter and a library of 16 specialty **skills**. It produces a structured consult note: urgency, problem representation, a ranked differential with can't-miss diagnoses, recommended workup, evidence-cited management, and a plain-language explanation for the patient.

> ⚠️ **Decision support, not a doctor.** This software does not diagnose, prescribe, or replace a licensed clinician who can examine the patient. It is not a medical device and has not been cleared by the FDA or any other regulator. Do not use it for real patient care without clinical oversight, validation, and the regulatory and privacy review that applies to you (HIPAA, GDPR). In an emergency, call your local emergency number.

## Tools

| Tool | Purpose |
|---|---|
| `consult` | Full attending consult note (`audience`: `clinician` or `patient`) |
| `red_flag_screen` | Deterministic emergency triage of free text. Call it first. |
| `calc_*` (18) | eGFR (CKD-EPI 2021), Cockcroft-Gault, CHA2DS2-VASc, HAS-BLED, HEART, QTc, MAP, Wells PE, PERC, CURB-65, qSOFA, Centor/McIsaac, MELD-Na, GCS, anion gap, corrected Ca, corrected Na, BMI |
| `drug_label` | FDA prescribing information: boxed warning, dosing, contraindications, pregnancy, and more |
| `drug_interaction_check` | Pairwise screen built from each drug's FDA label interaction section |
| `normalize_drug` | Maps brand or misspelled names to RxNorm ingredients |
| `pubmed_search` / `pubmed_abstract` | Literature search, optionally limited to systematic reviews, RCTs, and guidelines |
| `clinical_trials_search` | Recruiting trials from ClinicalTrials.gov |
| `list_skills` / `get_skill` | The specialty playbooks |

The server also exposes **prompts** (`attending_consult`, `medication_review`) and **resources** (`skill://{name}`).

### Skills (Agent Skills format: `skills/<name>/SKILL.md`)

clinical-reasoning · emergency-triage · evidence-based-medicine · pharmacotherapy · lab-interpretation · cardiology · pulmonology · infectious-disease · endocrinology · neurology · gastroenterology · pediatrics · obstetrics-gynecology · psychiatry · geriatrics · patient-communication

Claude Code and OpenClaw both use the Agent Skills standard (a folder containing `SKILL.md` with `name` and `description` frontmatter), so the same folders work in this server, in Claude Code, and in OpenClaw.

## Install

```bash
git clone https://github.com/sstarzh/medical-doctor && cd medical-doctor
pip install -e .            # or: uv pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...   # powers the `consult` tool
```

Without an API key, every tool except `consult` works. In that case `consult` falls back to **protocol mode**: it returns the red-flag screen and the physician charter, and the calling agent (for example Claude Desktop) runs the consult itself using the server's tools and skills.

## Use from Claude

**Claude Code**

```bash
claude mcp add medical-doctor -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY -- medical-doctor-mcp
```

To check in a shared setup instead, see [`examples/claude-code.mcp.json`](examples/claude-code.mcp.json).

**Claude Desktop**: merge [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json) into your config.

**Remote / any agent over HTTP**

```bash
medical-doctor-mcp --transport http --host 0.0.0.0 --port 8000   # endpoint: http://host:8000/mcp
# or
docker build -t medical-doctor-mcp . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY medical-doctor-mcp
```

The HTTP mode has **no built-in authentication**. Put it behind your gateway (OAuth, mTLS, or an API key) before you expose it anywhere.

## Use with OpenClaw

OpenClaw can use this project in two ways, and you can combine them:

1. **As an MCP server.** Register `medical-doctor-mcp` (stdio) or the HTTP endpoint in OpenClaw's saved MCP servers. See OpenClaw's *MCP* docs; the CLI form is `openclaw mcp add`. An example is in [`examples/openclaw-mcp.json`](examples/openclaw-mcp.json).
2. **As native skills.** Copy the skill folders into your OpenClaw workspace:
   ```bash
   ./scripts/install-skills.sh openclaw   # → ~/.openclaw/workspace/skills/
   ./scripts/install-skills.sh claude     # → ~/.claude/skills/
   ```
   You can also publish them to ClawHub.

For an OpenClaw "doctor" agent, use [`examples/openclaw-AGENTS.md`](examples/openclaw-AGENTS.md) as the agent's operating instructions.

> OpenClaw's config keys change fast. Check the current OpenClaw docs for exact paths.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Credentials for `consult` (an `ant auth login` profile also works) |
| `MEDICAL_DOCTOR_MODEL` | `claude-opus-5-5` | Model behind the attending |
| `MEDICAL_DOCTOR_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max` (use `max` for the hardest cases) |
| `MEDICAL_DOCTOR_WEB_SEARCH` | `1` | Lets the attending use Claude web search, restricted to guideline publishers (CDC, NIH, NICE, WHO, ACC/AHA, …) |
| `MEDICAL_DOCTOR_MAX_TOOL_ROUNDS` | `12` | Upper bound on tool calls per consult |
| `MEDICAL_DOCTOR_SKILLS_DIR` | bundled | Use your own or extended skill library |
| `NCBI_API_KEY`, `OPENFDA_API_KEY` | – | Optional; raise rate limits |

The consult uses the server-side refusal fallback (`fallbacks: "default"`). If a safety classifier declines a legitimate clinical question, the request is retried on Anthropic's recommended fallback model. The whole skill library sits in a prompt-cached system block, so repeat consults pay roughly 10% of the input cost for it.

## How a consult works

```
case ─► red_flag_screen (regex, negation-aware) ─┐
                                                 ▼
        Claude Opus 5.5 + physician charter + 16 skills (cached)
                │  tool loop: calc_* · drug_label · interactions · PubMed · trials · web search (guidelines)
                ▼
        consult note: Urgency → Problem representation → Differential (⚠️ can't-miss)
                      → Evaluation → Management (cited) → What would change it
                      → Patient explanation → Evidence → disclaimer
```

## Extending

- **New skill:** add `skills/<name>/SKILL.md` with `name` (it must match the folder name) and `description` frontmatter. The server, Claude Code, and OpenClaw all pick it up.
- **New calculator:** add a function to `calculators.py`, register it in `CALCULATORS`, and add a test with a published reference value.
- **New data source:** add an async function in `sources.py` and a tool in `server.py`.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Tests cover each calculator against published reference values, the red-flag triage (including negation handling), the MCP surface, the consult tool loop (with a fake Claude client), and the data-source parsers (with mocked HTTP).
