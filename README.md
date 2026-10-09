# Medical Doctor MCP

An MCP server that gives Claude, OpenClaw, or any other MCP-capable agent an **attending-physician-grade clinical consult service**.

It has three layers:

1. **A deterministic safety layer.** A red-flag screen catches emergencies such as stroke, ACS, sepsis, anaphylaxis, suicide risk, obstetric and neonatal emergencies, and cauda equina. It runs before any model reasoning and still works when no LLM is available.
2. **Verified tools.** There are 18 validated clinical calculators, plus live FDA drug labels, label-based interaction screening, RxNorm, PubMed, and ClinicalTrials.gov. The physician is required to compute scores and confirm doses with these tools instead of answering from memory.
3. **The attending.** Claude Opus 5.5 runs as a dual-boarded **internal medicine + pediatrics (Med-Peds)** attending, with depth in pediatric emergency medicine and neonatology. It has a physician charter and a library of 20 specialty **skills**, and for anyone under 18 it switches to pediatric reasoning, norms, decision rules and weight-based dosing. It produces a structured consult note: urgency, problem representation, a ranked differential with can't-miss diagnoses, recommended workup, evidence-cited management, and a plain-language explanation for the patient.

> ⚠️ **Decision support, not a doctor.** This software does not diagnose, prescribe, or replace a licensed clinician who can examine the patient. It is not a medical device and has not been cleared by the FDA or any other regulator. Do not use it for real patient care without clinical oversight, validation, and the regulatory and privacy review that applies to you (HIPAA, GDPR). In an emergency, call your local emergency number.

## Tools

| Tool | Purpose |
|---|---|
| `consult` | Full attending consult note for adults and children (`audience`: `clinician`, `patient`, or `caregiver` for a parent) |
| `red_flag_screen` | Deterministic emergency triage of free text. Call it first. |
| `calc_*` (18) | eGFR (CKD-EPI 2021), Cockcroft-Gault, CHA2DS2-VASc, HAS-BLED, HEART, QTc, MAP, Wells PE, PERC, CURB-65, qSOFA, Centor/McIsaac, MELD-Na, GCS, anion gap, corrected Ca, corrected Na, BMI |
| `calc_*` pediatric (10) | Age-normal vital signs (PALS), weight-based dose → mg and mL with adult caps, Holliday-Segar fluids, bedside Schwartz eGFR, APLS weight estimate, PECARN head trauma, PECARN febrile infant, Westley croup, Clinical Dehydration Scale, Pediatric Appendicitis Score |
| `drug_label` | FDA prescribing information: boxed warning, dosing, contraindications, pregnancy, and more |
| `drug_interaction_check` | Pairwise screen built from each drug's FDA label interaction section |
| `normalize_drug` | Maps brand or misspelled names to RxNorm ingredients |
| `pubmed_search` / `pubmed_abstract` | Literature search, optionally limited to systematic reviews, RCTs, and guidelines |
| `clinical_trials_search` | Recruiting trials from ClinicalTrials.gov |
| `list_skills` / `get_skill` | The specialty playbooks |
| `search` / `fetch` | Search across skills, calculators, and PubMed, then read a result (the convention ChatGPT connectors and deep research use) |
| `whoami` | Shows the signed-in account, to confirm a connector works |

The server also exposes **prompts** (`attending_consult`, `pediatric_consult`, `medication_review`) and **resources** (`skill://{name}`).

### Skills (Agent Skills format: `skills/<name>/SKILL.md`)

**Adult & general:** clinical-reasoning · emergency-triage · evidence-based-medicine · pharmacotherapy · lab-interpretation · cardiology · pulmonology · infectious-disease · endocrinology · neurology · gastroenterology · obstetrics-gynecology · psychiatry · geriatrics · patient-communication

**Pediatrics:** pediatrics (core: history, norms, dosing safety, never-in-children list) · pediatric-emergency (PALS, sepsis, seizures, PECARN, ingestions, abuse recognition) · neonatology (jaundice, febrile neonate/HSV, BRUE, feeding, safe sleep) · pediatric-infectious-disease (fever by age, AOM, strep, UTI, pneumonia, Kawasaki, measles) · well-child-development (growth, milestones, autism/ADHD, screening, immunizations, adolescent health)

The red-flag screen also catches pediatric danger signs: infant respiratory distress, a floppy or lethargic baby, bilious vomiting or intussusception, button-battery and other dangerous ingestions (with Poison Control), dehydration in infants, prolonged fever (Kawasaki), and newborn jaundice.

Claude Code and OpenClaw both use the Agent Skills standard (a folder containing `SKILL.md` with `name` and `description` frontmatter), so the same folders work in this server, in Claude Code, and in OpenClaw.

## Install

```bash
git clone https://github.com/sstarzh/medical-doctor && cd medical-doctor
pip install -e .            # or: uv pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...   # powers the `consult` tool
```

Without an API key, every tool except `consult` works. In that case `consult` falls back to **protocol mode**: it returns the red-flag screen and the physician charter, and the calling agent (for example Claude Desktop) runs the consult itself using the server's tools and skills.

## Works with any AI tool

The server speaks standard MCP over **stdio** (local) and **Streamable HTTP** (remote, at `/mcp`). It works with ChatGPT, Claude (web, desktop, and Code), Microsoft Copilot Studio, Cursor, VS Code/Copilot, Windsurf, Gemini CLI, OpenAI Codex, OpenClaw, the OpenAI Responses API and Agents SDK, the Anthropic MCP connector, LangChain, n8n, and others. Clients that only support stdio can connect through `mcp-remote`.

**→ Per-client setup: [docs/CLIENTS.md](docs/CLIENTS.md)**

Quick starts:

```bash
# Local, for one person (no auth needed)
claude mcp add medical-doctor -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY -- medical-doctor-mcp

# Remote: ChatGPT / claude.ai → Add custom connector → https://<your-host>/mcp → sign in with Google
claude mcp add --transport http medical-doctor https://<your-host>/mcp
```

## Authentication (sign in with Google, GitHub, Microsoft, or SSO)

Remote deployments should always use authentication. Set `MEDICAL_DOCTOR_AUTH` to one of these:

| Mode | What it does | Use when |
|---|---|---|
| `oauth` | **Built-in OAuth 2.1 authorization server.** Users sign in with Google, GitHub, Microsoft (Entra ID), or any OIDC provider (Okta, Auth0, Keycloak, Ping, Axiad, …). Supports discovery (RFC 9728/8414), Dynamic Client Registration (RFC 7591), PKCE, resource-bound tokens (RFC 8707), refresh rotation, and revocation. | ChatGPT, Claude, and IDEs connect with just a URL |
| `jwt` | Accepts access tokens from **your existing authorization server**, validated against its JWKS (signature, `iss`, `aud`, `exp`). | You already run Auth0, Okta, Entra, Keycloak, WorkOS, Descope, or Stytch with DCR enabled |
| `apikey` | Static bearer keys only. | Server-to-server agents and automation |
| `none` | No authentication. | Local stdio or a localhost-only setup |

API keys (`MEDICAL_DOCTOR_API_KEYS`) also work alongside `oauth` and `jwt`, for headless agents.

### Set up Google sign-in (5 minutes)

1. In Google Cloud Console, go to **APIs & Services → Credentials → Create OAuth client ID → Web application**.
2. Add the authorized redirect URI `https://<your-host>/oauth/callback/google`.
3. Run:
   ```bash
   export MEDICAL_DOCTOR_AUTH=oauth
   export MEDICAL_DOCTOR_PUBLIC_URL=https://<your-host>
   export GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...
   export MEDICAL_DOCTOR_ALLOWED_DOMAINS=yourcompany.com     # and/or MEDICAL_DOCTOR_ALLOWED_EMAILS=a@gmail.com,b@x.org
   medical-doctor-mcp --transport http --host 0.0.0.0
   ```

Other providers work the same way, each with redirect URI `https://<your-host>/oauth/callback/<provider>`:
- **GitHub:** OAuth App, using `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`.
- **Microsoft:** a single-tenant app registration, using `MICROSOFT_CLIENT_ID` / `_SECRET` / `MICROSOFT_TENANT_ID`.
- **Any OIDC provider:** `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`.

When more than one is configured, the sign-in page offers a button for each. All variables are listed in [`.env.example`](.env.example).

**Security design**
- **Closed by default.** The server refuses to start in `oauth` mode unless you set an allowlist (domains or emails), or explicitly opt in with `MEDICAL_DOCTOR_ALLOW_ANY_USER=1`. Every consult spends your Anthropic credits.
- **Verified emails only.** The allowlist only accepts verified emails. Microsoft requires a specific tenant ID, because multi-tenant Entra apps return an unverified, user-editable `email` claim (the "nOAuth" issue).
- **Consent screen.** Users always see a page naming the requesting app and its redirect host before going to Google. This follows the MCP security best practices for proxy-style servers and blocks confused-deputy attacks through maliciously registered clients. The page cannot be framed and sends a strict CSP.
- **No upstream tokens reach clients.** Google, GitHub, and Microsoft tokens are never passed through. Clients get short-lived tokens bound to this server: 1 h access tokens, plus 30-day refresh tokens that rotate on every use.
- **Hashed, single-use secrets.** Codes and tokens are stored only as SHA-256 hashes, in SQLite with `0600` permissions (`MEDICAL_DOCTOR_DB`). Authorization codes work once and expire in 5 minutes.
- **No PHI in logs.** Audit logs record who ran a consult and the urgency level, never the clinical text.
- **Per-user limit.** `MEDICAL_DOCTOR_CONSULT_LIMIT_PER_HOUR` caps consults per user to protect model spend.

### Deploy

```bash
docker build -t medical-doctor-mcp .
docker run -p 8000:8000 -v md-data:/data --env-file .env medical-doctor-mcp     # → https://<your-host>/mcp
```

- Put it behind HTTPS (Cloud Run, Fly.io, Render, ECS, or Kubernetes with an ingress).
- The built-in OAuth store is SQLite, so run **one instance** with a persistent `/data` volume.
- To scale out horizontally, use `MEDICAL_DOCTOR_AUTH=jwt` with an external authorization server, plus `MEDICAL_DOCTOR_STATELESS=1`.
- For a quick test without deploying, run `cloudflared tunnel --url http://localhost:8000`.

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
| `MEDICAL_DOCTOR_AUTH` | `none` | `none` / `apikey` / `oauth` / `jwt` (see Authentication) |
| `MEDICAL_DOCTOR_PUBLIC_URL` | – | Public base URL; required when auth is on |
| `MEDICAL_DOCTOR_CORS_ORIGINS` | `*` | Origins allowed for browser-based clients |
| `MEDICAL_DOCTOR_STATELESS` | `0` | `1` for load-balanced or serverless deployments |
| `MEDICAL_DOCTOR_CONSULT_LIMIT_PER_HOUR` | `0` (unlimited) | Per-user consult cap |

The consult uses the server-side refusal fallback (`fallbacks: "default"`). If a safety classifier declines a legitimate clinical question, the request is retried on Anthropic's recommended fallback model. The whole skill library sits in a prompt-cached system block, so repeat consults pay roughly 10% of the input cost for it.

## How a consult works

```
case ─► red_flag_screen (regex, negation-aware) ─┐
                                                 ▼
        Claude Opus 5.5 (Med-Peds attending) + charter + 20 skills (cached)
                │  tool loop: calc_* (adult + pediatric) · drug_label · interactions · PubMed · trials · web search (guidelines)
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

Tests cover the full OAuth flow as ChatGPT and Claude run it (discovery, DCR, consent, Google sign-in, PKCE token exchange, refresh rotation, revocation, allowlist denial), JWT validation, API keys, each adult and pediatric calculator against published reference values, the red-flag triage (including negation handling), the MCP surface, the consult tool loop (with a fake Claude client), and the data-source parsers (with mocked HTTP).
