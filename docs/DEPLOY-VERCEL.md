# Deploy on Vercel + Supabase

Two services split the work:

- **Vercel** runs the MCP server as one serverless function at `https://<project>.vercel.app/mcp`.
- **Supabase Postgres** stores the sign-in data: app registrations, tokens, and per-user consult counters. This data has to survive across Vercel's short-lived, autoscaled instances.

Consult text is never stored. The database holds hashed tokens, OAuth client registrations and each user's email and name. If `MEDICAL_DOCTOR_DATA_KEYS` is set, it also holds the patient charts users choose to keep. Those are encrypted per patient with AES-256-GCM before they reach Supabase, and only opaque IDs and timestamps are stored in clear.

## 1. Supabase

Use an existing project or create a new one. Pick the region closest to where Vercel will run, e.g. `us-east-1`.

You need the **pooled** connection string. Use either option:

- **Easiest:** in Vercel, add the **Supabase integration** (Vercel Marketplace → Supabase) and link it to this project. It sets `POSTGRES_URL` for you, and the server reads that variable automatically.
- **Manual:** in Supabase, open **Connect → Transaction pooler**. Copy the URI (port `6543`) and add it to Vercel as `POSTGRES_URL`, with your database password filled in.

You don't need to run any migrations. On first start, the server creates its own `medical_doctor` schema with one table (`kv`):

- The table is **not** in `public`, so Supabase's Data API doesn't expose it.
- **Row-level security is on with no policies**, so the anon and authenticated keys can't read it even if someone adds the schema to the API.
- Only the server, connecting as the database owner, can access it.

## 2. Google sign-in

1. In Google Cloud Console, go to **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application**.
2. Add the authorized redirect URI `https://<project>.vercel.app/oauth/callback/google`. Use your **production** domain or custom domain; preview URLs change on every deploy.
3. Copy the client ID and secret.

GitHub, Microsoft, and company SSO work the same way, using `/oauth/callback/github`, `/microsoft`, or `/oidc`. See the README.

## 3. Vercel

1. **Add New → Project → Import** `sstarzh/medical-doctor`. Leave the framework preset on auto-detect. The repo's `app.py` is the entry point, and `vercel.json` sets a 300 s time limit and bundles the skill files.
2. In **Settings → Environment Variables (Production)**, add:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `MEDICAL_DOCTOR_AUTH` | `oauth` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | from step 2 |
| `MEDICAL_DOCTOR_ALLOWED_DOMAINS` | e.g. `axiad.com` (and/or `MEDICAL_DOCTOR_ALLOWED_EMAILS`) |
| `POSTGRES_URL` | set by the Supabase integration, or the pooler URI from step 1 |
| `MEDICAL_DOCTOR_EFFORT` | `medium`, recommended on the Hobby plan (see the time limit below) |
| `MEDICAL_DOCTOR_CONSULT_LIMIT_PER_HOUR` | e.g. `20`, to cap spend per user |
| `MEDICAL_DOCTOR_DATA_KEYS` | from `medical-doctor-mcp gen-data-key k1`; enables the encrypted patient record (stored in the same Supabase database). Keep a copy in your secret manager: without it the records are unreadable. |
| `OPENAI_API_KEY` / `LLAMA_API_KEY` | optional: other model providers and the `second_opinion` panel |
| `MEDICAL_DOCTOR_PUBLIC_URL` | only with a custom domain, e.g. `https://doctor.example.com`; it defaults to your production `*.vercel.app` URL |

3. Deploy. Then confirm:
   - `https://<project>.vercel.app/healthz` returns `{"status":"ok"}`.
   - `https://<project>.vercel.app/.well-known/oauth-protected-resource/mcp` points at your domain.

4. Connect a client. In ChatGPT or Claude, choose **Add custom connector** and enter `https://<project>.vercel.app/mcp`. Sign in with Google, then ask the app to run **`whoami`**. More clients are covered in [CLIENTS.md](CLIENTS.md).

The server won't start in `oauth` mode on Vercel without a database URL. Without one, sign-ins would vanish between instances.

## Things to know

- **Time limit.**
  - The Hobby plan stops a function at **300 s**. Most consults finish well within that, especially at `MEDICAL_DOCTOR_EFFORT=medium`.
  - On Pro, raise `maxDuration` in `vercel.json` to `800`.
  - The server streams progress updates during a consult, so clients see activity while it runs.
- **Region.** Put the function in the same region as the database: **Settings → Functions → Region**, e.g. `iad1` for Supabase `us-east-1`. Every token check reads Postgres, and cross-region round trips add latency.
- **Preview deployments.** Preview URLs change on every deploy and aren't registered with Google, so sign-in only works on production or a stable custom domain. Use `MEDICAL_DOCTOR_AUTH=apikey` for previews if you need them.
- **Supabase free tier** pauses projects after about a week without activity. Use a paid project, or check it before a demo.
- **API keys for agents.** Add `MEDICAL_DOCTOR_API_KEYS`, generated with `medical-doctor-mcp gen-key <label>`. Scripts, n8n, or the OpenAI and Anthropic APIs can then call the server with a Bearer key, alongside Google sign-in.
- **Health data and compliance.** The server stores no clinical text. Consult text still passes through Vercel's function memory and the Anthropic API. Before you use real patient information:
  - Sign a BAA with each provider. Vercel, Supabase, and Anthropic each offer HIPAA BAAs only on specific plans.
  - Review the data flow with your security team.

## Local check before deploying

```bash
pip install -e '.[dev]'
export MEDICAL_DOCTOR_TEST_DATABASE_URL='postgresql://...'   # optional: a throwaway database runs the Postgres tests
pytest
vercel dev        # runs app.py locally the way Vercel does
```
