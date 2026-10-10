# Medical Doctor MCP

An MCP server that gives Claude, OpenClaw, or any other MCP-capable agent an **attending-physician-grade clinical consult service**.

It has five layers:

1. **A deterministic safety layer.** A red-flag screen catches emergencies such as cardiac arrest, stroke, ACS, sepsis, anaphylaxis, overdose, suicide risk, severe hypo- and hyperglycemia, obstetric and neonatal emergencies, and cauda equina. It handles negation and history ("no history of heart disease and now has chest pain" is still flagged). It runs before any model reasoning, works without an LLM, and is locked by a regression corpus of real phrasings.
2. **Verified tools.** There are 29 validated clinical calculators (including MELD 3.0 and pediatric decision rules), plus live FDA drug labels, label-based interaction screening, RxNorm, PubMed, and ClinicalTrials.gov. The physician must compute scores and confirm doses with these tools instead of answering from memory.
3. **The attending.** A dual-boarded **internal medicine + pediatrics (Med-Peds)** attending with a physician charter and 22 specialty **skills**. It produces a structured consult note: urgency, a ranked differential with can't-miss diagnoses, workup, evidence-cited management, and a plain-language explanation. It runs on **Claude (default), OpenAI, Meta Muse Spark, or open-weight Llama**, and optionally on a **multi-model panel** that reconciles disagreements.
4. **A personal health record.** Each account holds an encrypted chart for the user and family members: history, medicines, allergies, results, and notes. The attending reads it, follows up on open items, and suggests preventive care.
5. **Calls to action, never orders.** Prescription and referral **drafts** (FHIR `MedicationRequest` / `ServiceRequest` with `status=draft`) are checked in code against the chart (allergy cross-reactivity, interactions, pregnancy, pediatric age and weight, Beers criteria, kidney function). Controlled substances are refused. A licensed clinician reviews and issues anything real.

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
| `verify_citations` | Checks every PMID in a text against PubMed (verified, mismatch, not found, unchecked) and flags links outside recognised guideline and journal publishers |
| `clinical_trials_search` | Recruiting trials from ClinicalTrials.gov |
| `list_skills` / `get_skill` | The specialty playbooks, with review freshness and outdated guideline editions |
| `search` / `fetch` | Search across skills, calculators, and PubMed, then read a result (the convention ChatGPT connectors and deep research use) |
| `whoami` | Shows the signed-in account, to confirm a connector works |
| `patient_create` / `patient_list` / `patient_chart` / `patient_update` / `patient_delete_item` | The personal-doctor chart for you and family members (encrypted, private to the account) |
| `patient_import_fhir` | Imports records from a hospital, clinic or health-app FHIR R4 export (conditions, medications, allergies, labs, vitals, immunizations, procedures, family history). Re-importing updates entries instead of duplicating them. |
| `patient_export` / `patient_erase` / `patient_access_log` | FHIR R4 export, permanent erasure (crypto-shredding), and a tamper-evident access log |
| `preventive_care_due` | Screenings, vaccines, and counseling due by age, sex, and risk (USPSTF A/B, CDC/ACIP, AAP Bright Futures) |
| `draft_prescription` / `draft_referral` | Drafts for a licensed clinician, with deterministic safety checks and patient- and clinician-facing calls to action |
| `care_plan_update` | Track drafts and follow-ups (accepted, completed, declined) |

`consult` takes optional `patient_id` (include the chart), `provider` (`anthropic` | `openai` | `meta` | `llama`), and `second_opinion` (run the case past every configured provider and reconcile the notes).

The server also exposes **prompts** (`personal_doctor_visit`, `patient_intake`, `attending_consult`, `pediatric_consult`, `medication_review`) and **resources** (`skill://{name}`).

### What `consult` returns

- **`note`**: the Markdown consult note.
- **`structured`**: the same note as validated data (`ConsultSummary`).
  - Fields: urgency, problem representation, differential (likelihood, %, can't-miss flag), evaluation, management, drafts, missing information, return precautions, citations.
  - `urgency` is never lower than the deterministic red-flag screen; the model's own call stays in `model_urgency`.
  - If the model omits the block, `source` reads `markdown_fallback`.
- **`citation_check`**: every PMID in the note looked up in PubMed and marked `verified`, `mismatch` (a real article on a different topic), `not_found`, or `unchecked`. PubMed being unreachable is never counted as verified.
- **Also returned:** `red_flag_screen`, `tool_calls`, `usage`, and, for a second opinion, `panel`.

## Personal doctor: the patient record

- **What it holds.** One account can hold up to 20 profiles (yourself, children, parents). Each has demographics, pregnancy and lactation, weight, allergies with reactions, medications, conditions, immunizations, procedures, family and social history, observations (vitals and labs), visit notes, and a care plan.
- **Owner.** The owner is the signed-in identity, a hash of issuer plus subject, and never a tool argument. Shared API keys can't own charts; only keys labelled `person:<name>` can.
- **Encryption.** Each patient has its own AES-256-GCM data key, wrapped by the key-encryption key in `MEDICAL_DOCTOR_DATA_KEYS`. Every record is bound to its row (owner, patient, section, item), so a copied ciphertext won't decrypt elsewhere. Only opaque IDs and timestamps are stored in clear.
- **Key rotation.** Generate a new key with `medical-doctor-mcp gen-data-key k2` and prepend it. Old keys stay listed until patient keys are re-wrapped (`RecordRepo.rewrap_keys`).
- **Erasure.** Destroying the patient's data key makes every copy, including backups, unreadable.
- **Audit.** Every read and write goes to a hash-chained audit table; `patient_access_log` reports whether the chain is intact. Clinical text is never logged.
- **Where it's stored.** Locally (stdio), the chart lives in `~/.medical-doctor/records.db` with an auto-created key file. On servers it lives in Postgres (Supabase), in the `medical_doctor` schema with RLS on.
- **Importing from hospitals.** Download records as FHIR (patient portals' "download my records", Apple Health / Health Connect exports, or a SMART on FHIR `Patient/$everything`) and call `patient_import_fhir`. Imported demographics only fill blanks; entries the user entered are never overwritten. A bundle with more than one patient is refused. Live SMART on FHIR sign-in to an EHR is on the roadmap.
- **Sharing with a model.** A chart is sent to a model only when you pass `patient_id` to `consult`, and only as a minimal clinical summary.

## Model providers: Claude, OpenAI, Meta Muse Spark, Llama

| Provider | Set | Default model | Notes |
|---|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | Prompt-cached skill library, guideline web search (trusted publishers only), server-side refusal fallback |
| `openai` | `OPENAI_API_KEY` | `gpt-6-sol` | Chat Completions function calling; optional `MEDICAL_DOCTOR_OPENAI_REASONING_EFFORT` |
| `meta` | `MODEL_API_KEY` (or `META_API_KEY`) | `muse-spark-1.3` | Meta Model API (`https://api.meta.ai/v1`, OpenAI-compatible). Also `muse-spark-1.2`, or `muse-spark-1.1` for 1M-token context. **`*-contributor` models are refused**: Meta uses their traffic to improve its products, so they must never see patient data. |
| `llama` | `LLAMA_API_KEY` + `MEDICAL_DOCTOR_LLAMA_BASE_URL` | `meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8` | Open-weight Llama on any OpenAI-compatible host: Together, Groq, Fireworks, vLLM, Bedrock. (Meta's own Llama API was retired in July 2026.) |

- **Choosing a provider.** Pick the server default with `MEDICAL_DOCTOR_PROVIDER`. Override models per provider with `MEDICAL_DOCTOR_ANTHROPIC_MODEL`, `MEDICAL_DOCTOR_OPENAI_MODEL`, `MEDICAL_DOCTOR_META_MODEL`, or `MEDICAL_DOCTOR_LLAMA_MODEL`. Model IDs differ between Llama hosts.
- **Same safety on every provider.** All providers use the same charter, tools, red-flag screen, deterministic checks, and time budget.
- **Smaller context windows.** Models without large cached contexts get the most relevant skills instead of all 22.
- **Second opinion.** Set `MEDICAL_DOCTOR_SECOND_OPINION=anthropic,openai,meta` and call `consult(..., second_opinion=true)`. Every configured model writes a note in parallel; the primary model then writes one reconciled note with an explicit **Points of disagreement** section, and never drops a can't-miss diagnosis that any model raised.

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
- **Consent screen, enforced.** Users always see a page naming the requesting app and its redirect host before going to Google. This follows the MCP security best practices for proxy-style servers and blocks confused-deputy attacks through maliciously registered clients. The "Continue" step is a POST that only works with a SameSite cookie the consent page sets, and the provider callback checks the same cookie. So no link can skip the page, and a sign-in can't be finished in another browser (login CSRF). The page cannot be framed and sends a strict CSP.
- **No upstream tokens reach clients.** Google, GitHub, and Microsoft tokens are never passed through. Clients get short-lived tokens bound to this server: 1 h access tokens, plus 30-day refresh tokens that rotate on every use.
- **Stolen refresh tokens are caught.** Replaying a refresh token that has already been rotated revokes the whole session. Every sign-in also has an absolute lifetime (`MEDICAL_DOCTOR_MAX_SESSION_DAYS`, default 90) that rotation can't extend.
- **Bounded storage.** Client registrations that never complete a sign-in expire after 24 h. Expired rows are purged automatically, and unauthenticated endpoints have row caps, so a registration flood can't fill the disk.
- **Hashed, single-use secrets.** Codes and tokens are stored only as SHA-256 hashes, in SQLite with `0600` permissions (`MEDICAL_DOCTOR_DB`). Authorization codes work once and expire in 5 minutes.
- **No PHI in logs.** Audit logs record who ran a consult and the urgency level, never the clinical text.
- **Per-user limit.** `MEDICAL_DOCTOR_CONSULT_LIMIT_PER_HOUR` caps consults per user to protect model spend. Counters live in the SQLite database, so the limit holds across worker processes and restarts. Replicas on different hosts need a shared volume or a per-replica limit.
- **Proxy headers.** `X-Forwarded-*` headers are trusted only from `FORWARDED_ALLOW_IPS` (default `127.0.0.1`). Set it to your load balancer's address range.
- **External JWTs.** In `jwt` mode, the email allowlist requires `email_verified: true`. Set `MEDICAL_DOCTOR_JWT_TRUST_UNVERIFIED_EMAIL=1` only if your authorization server never lets users set their own email.

### Deploy

```bash
docker build -t medical-doctor-mcp .
docker run -p 8000:8000 -v md-data:/data --env-file .env medical-doctor-mcp     # → https://<your-host>/mcp
```

- **Vercel + Supabase (serverless):** see **[docs/DEPLOY-VERCEL.md](docs/DEPLOY-VERCEL.md)**. `app.py` is the Vercel entry point. Sign-in data lives in Supabase Postgres (set `POSTGRES_URL`), so it survives across serverless instances.
- Put it behind HTTPS (Cloud Run, Fly.io, Render, ECS, or Kubernetes with an ingress).
- Network deployments must set `MEDICAL_DOCTOR_AUTH`. The server refuses to start publicly without it, because anyone could run paid consults and records are tied to a signed-in identity.
- Without a database URL, the built-in OAuth store is SQLite, so run **one instance** with a persistent `/data` volume. With `POSTGRES_URL` or `MEDICAL_DOCTOR_DATABASE_URL` (Supabase, Neon, RDS, …), you can run any number of instances.
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
| `MEDICAL_DOCTOR_PROVIDER` | `anthropic` | `anthropic` / `openai` / `meta` / `llama` (see Model providers) |
| `MEDICAL_DOCTOR_MODEL` | provider default | Model for the default provider |
| `OPENAI_API_KEY`, `MODEL_API_KEY`, `LLAMA_API_KEY` | – | Credentials for the OpenAI, Meta and Llama providers |
| `MEDICAL_DOCTOR_SECOND_OPINION` | – | Providers on the second-opinion panel, e.g. `anthropic,openai,meta` |
| `MEDICAL_DOCTOR_CONSULT_TIMEOUT` | `270` | Overall time budget per consult in seconds, under Vercel's 300 s limit |
| `MEDICAL_DOCTOR_DATA_KEYS` | – | Key-encryption key(s) for patient records, `kid:base64,…` (`medical-doctor-mcp gen-data-key`). Required for records on servers; created automatically for local stdio. |
| `MEDICAL_DOCTOR_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max` (use `max` for the hardest cases) |
| `MEDICAL_DOCTOR_WEB_SEARCH` | `1` | Lets the attending use Claude web search, restricted to guideline publishers (CDC, FDA, NICE, WHO, USPSTF, ACC/AHA, …); sites with user comments are excluded |
| `MEDICAL_DOCTOR_VERIFY_CITATIONS` | `1` | Check every PMID in consult notes against PubMed |
| `LOG_FORMAT` | `text` (`json` on Vercel) | `json` for one JSON object per log line |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | – | Export traces over OTLP (needs `pip install '.[otel]'`) |
| `MODEL_API_KEY`, `MEDICAL_DOCTOR_META_MODEL` | – / `muse-spark-1.3` | Meta Model API key and model; `*-contributor` models are refused |
| `MEDICAL_DOCTOR_LLAMA_BASE_URL` | – | Required for the `llama` provider (OpenAI-compatible Llama host) |
| `MEDICAL_DOCTOR_MAX_TOOL_ROUNDS` | `12` | Upper bound on tool calls per consult |
| `MEDICAL_DOCTOR_SKILLS_DIR` | bundled | Use your own or extended skill library |
| `NCBI_API_KEY`, `OPENFDA_API_KEY` | – | Optional; raise rate limits |
| `MEDICAL_DOCTOR_AUTH` | `none` | `none` / `apikey` / `oauth` / `jwt` (see Authentication) |
| `MEDICAL_DOCTOR_PUBLIC_URL` | – | Public base URL; required when auth is on |
| `MEDICAL_DOCTOR_CORS_ORIGINS` | `*` | Origins allowed for browser-based clients |
| `MEDICAL_DOCTOR_STATELESS` | `0` | `1` for load-balanced or serverless deployments (always on in `app.py`) |
| `POSTGRES_URL` / `MEDICAL_DOCTOR_DATABASE_URL` | – | Postgres for sign-in data (Supabase pooled URI); replaces the SQLite file |
| `MEDICAL_DOCTOR_CONSULT_LIMIT_PER_HOUR` | `20` | Per-user consult cap (`0` = unlimited) |
| `MEDICAL_DOCTOR_ALLOW_UNAUTHENTICATED` | `0` | The server refuses to listen on a non-loopback address, or run on Vercel, without auth unless this is `1` |

The consult uses the server-side refusal fallback (`fallbacks: "default"`). If a safety classifier declines a legitimate clinical question, the request is retried on Anthropic's recommended fallback model. The whole skill library sits in a prompt-cached system block, so repeat consults pay roughly 10% of the input cost for it.

## How a consult works

```
case ─► red_flag_screen (regex, negation-aware) ─┐
                                                 ▼
        Claude / OpenAI / Muse Spark / Llama (Med-Peds attending) + charter + 22 skills
                │  tool loop: calc_* (adult + pediatric) · drug_label · interactions · PubMed · trials · web search (guidelines)
                ▼
        consult note: Urgency → Problem representation → Differential (⚠️ can't-miss)
                      → Evaluation → Management (cited) → What would change it
                      → Patient explanation → Evidence → disclaimer + JSON summary
                │
                ▼
        structured summary (validated; urgency floor = red-flag screen) + PubMed citation check
```

## Quality: evals, citations, guideline freshness, observability

- **Clinical evals.** [`evals/cases.yaml`](evals/cases.yaml) holds 24 adult and pediatric vignettes (ACS, stroke, SAH, PE, sepsis, ectopic, febrile neonate, Kawasaki, croup, and others).
  - Each vignette sets expected screen urgency, urgency bounds, diagnoses to consider, can't-miss diagnoses that must be flagged, required tools, and unsafe phrases.
  - `medical-doctor-mcp eval` runs the deterministic screen on all of them (in CI on every push).
  - `medical-doctor-mcp eval --consult --provider openai` runs real consults and scores the structured output. A case fails outright on under-triage, an unflagged can't-miss diagnosis, or a fabricated citation.
  - The **Clinical evals** workflow runs this on demand per provider and uploads the report.
- **Guideline freshness.** Every skill carries `metadata.last_reviewed`, `review_interval_days`, and `clinical_review`. Overdue skills are flagged to the model inside the consult prompt.
  - `medical-doctor-mcp check-freshness` lists review status and cited guideline editions more than 5 years old.
  - A monthly workflow opens an issue when skills fall overdue.
  - All skills are `clinical_review: pending` until a licensed clinician signs them off.
- **Observability.**
  - **Logs:** JSON on Vercel or with `LOG_FORMAT=json`.
  - **Request IDs:** every request gets one (`X-Request-Id`, echoed back and stamped on every log line).
  - **Per-tool timing and outcome:** logged for every tool call.
  - **Consult metrics:** provider, model, urgency, tokens, rounds, citation results, duration.
  - **Tracing:** the MCP SDK's OpenTelemetry spans plus a `consult` span. Install `.[otel]` and set `OTEL_EXPORTER_OTLP_ENDPOINT` to export them to Honeycomb, Grafana, Datadog, Langfuse and others.
  - **No PHI:** logs and spans never contain tool arguments, results, or clinical text.

## Extending

- **New skill:** add `skills/<name>/SKILL.md` with `name` (it must match the folder name), `description` and `metadata.last_reviewed` frontmatter. The server, Claude Code, and OpenClaw all pick it up.
- **New calculator:** add a function to `calculators.py`, register it in `CALCULATORS`, and add a test with a published reference value.
- **New data source:** add an async function in `sources.py` and a tool in `server.py`.

## CI and code review

- **`.github/workflows/ci.yml`** runs the test suite (with Postgres) and the red-flag screen evals on Python 3.11, 3.12, and 3.13 for every pull request and every push to `main`.
- **`.github/workflows/evals.yml`** (manual) runs full consult evals on a chosen provider. **`guideline-freshness.yml`** (monthly) checks skill review dates.
- **`.github/workflows/claude-code-review.yml`**: Claude reviews every pull request and posts inline comments.
- **`.github/workflows/claude.yml`**: mention `@claude` in an issue or PR comment to ask questions or request changes.

The two Claude workflows need one-time setup:
1. Install the [Claude GitHub App](https://github.com/apps/claude) on the repository.
2. Add an `ANTHROPIC_API_KEY` repository secret. Alternatively, add `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` and switch the input as noted in the workflow file.

Running `/install-github-app` in Claude Code does both steps for you.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Tests cover the full OAuth flow as ChatGPT and Claude run it (discovery, DCR, consent, Google sign-in, PKCE token exchange, refresh rotation, revocation, allowlist denial), JWT validation, API keys, each adult and pediatric calculator against published reference values, the red-flag triage (including negation handling), the MCP surface, the consult tool loop (with a fake Claude client), and the data-source parsers (with mocked HTTP).
