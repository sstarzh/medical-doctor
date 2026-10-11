# CLAUDE.md

Guidance for Claude (and people) working in this repository. The user-facing overview is in [README.md](README.md).

## What this is

Medical Doctor is a remote MCP server (Python, MCP SDK v2 + Starlette) that gives Claude, ChatGPT and other MCP
clients a personal AI physician. It provides:
- a deterministic red-flag screen;
- an evidence-based consult engine (Claude, OpenAI, Meta Muse Spark or Llama);
- second opinions;
- clinical calculators;
- PubMed, openFDA and RxNorm lookups;
- an encrypted per-user health record that updates automatically;
- SMART on FHIR import (MyChart/Epic).

Production runs on Vercel with Supabase Postgres and Google OAuth: https://medical-doctor-tau.vercel.app (MCP endpoint `/mcp`).

## Layout

| Path | What lives there |
|---|---|
| `src/medical_doctor/server.py` | MCP tools, routes, and wiring (`build_server`) |
| `src/medical_doctor/physician.py` | Consult engine: charter, depth profiles, `make_engine`, second-opinion reviewer selection, panel synthesis, self-check revision |
| `src/medical_doctor/llm/` | Provider engines: `anthropic_engine.py` and `openai_compat.py` (OpenAI, Meta, Llama) |
| `src/medical_doctor/safety.py` | Deterministic, negation-aware red-flag screen |
| `src/medical_doctor/quality.py` | Post-consult self-check (can't-miss coverage, allergy conflicts, pediatric dosing, citations) |
| `src/medical_doctor/consult_note.py` | Parsing of the note plus its machine-readable summary |
| `src/medical_doctor/citations.py` | PubMed verification of cited PMIDs |
| `src/medical_doctor/records/`, `records_tools.py` | Encrypted records (AES-GCM envelope, audit chain), AutoChart |
| `src/medical_doctor/ehr.py` | SMART on FHIR standalone launch |
| `src/medical_doctor/auth/` | OAuth 2.1 broker (Google, GitHub, Microsoft, OIDC), API keys, JWT |
| `src/medical_doctor/landing.py`, `web.py` | Setup page at `/` and the shared security headers |
| `src/medical_doctor/config.py` | All `MEDICAL_DOCTOR_*` settings and startup validation |
| `skills/<name>/SKILL.md` | Clinical skills in Agent Skills format |
| `evals/cases.yaml` | 35 clinical eval cases |
| `docs/` | Deployment, client setup, HIPAA to-do list |

## Commands

```bash
pip install -e '.[dev]'
pytest -q                                   # SQLite only; Postgres tests are skipped
MEDICAL_DOCTOR_TEST_DATABASE_URL=postgresql://... pytest -q   # also runs the Postgres tests
medical-doctor-mcp eval                     # red-flag screen evals (offline)
medical-doctor-mcp eval --consult --provider meta --only adult-acs   # live consult evals (needs keys)
ruff check <changed files>                  # the repo has pre-existing lint debt; keep new code clean
```

Full consult evals also run on demand through the GitHub workflow `evals.yml` (`workflow_dispatch`, with a `provider` input).

## Rules that must not be broken

- **Patient data is PHI.**
  - Never log or trace tool arguments, results or clinical text. Tests check this.
  - Never send patient data to Meta `-contributor` models. `config.validate` and `make_engine` refuse them; keep it that way for every new model setting.
- **Prescriptions and referrals are drafts only.** They are calls to action for a licensed clinician, never real orders.
- **The red-flag screen is the floor.** A model may raise urgency but never lower it below the deterministic screen.
- **Security headers:**
  - HTML pages use the headers in `web.py`.
  - Inline scripts are pinned by CSP hash.
  - Never build URLs from the request `Host` header; use `MEDICAL_DOCTOR_PUBLIC_URL`.
- **No secrets in the repo.** Keys live in Vercel and GitHub secrets.
- **HIPAA work is tracked in `docs/HIPAA-TODO.md`.** Update it when a change affects data at rest or in transit.

## Conventions

- **Performance matters on every change.**
  - Consults must fit Vercel's 300 s function limit (`consult_timeout` 270 s).
  - Prefer parallel calls, prompt caching and cheap deterministic checks over extra model calls.
- **Match the surrounding style.** Dense, typed Python; comments only where the "why" isn't obvious.
- **Every behavior change gets a test.** Clinical rules need a test that cites a reference value or case.
- **Any provider-specific setting must route to the right provider.** A model name for one provider must never reach another.
- **Second-opinion reviewers:**
  - `MEDICAL_DOCTOR_SECOND_OPINION` is an ordered list of `provider[:model]`.
  - When nothing listed is usable, `automatic_reviewer` applies. Results flag `same_model` honestly.
- **Docs.** Update `README.md` (feature text and the configuration table) whenever settings or behavior change.
- **Pull requests.** Open one only when asked. CI runs tests with Postgres on Python 3.11–3.13.
