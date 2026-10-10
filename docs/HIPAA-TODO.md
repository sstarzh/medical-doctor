# HIPAA review — to do

Status: **not started**. This is a reminder list, not a compliance statement. Supabase is covered by a BAA.
What still needs checking is the patient record itself and every hop that carries patient data.

## Patient record (at rest)
- [ ] Confirm the encryption meets the Security Rule's encryption safeguard (164.312(a)(2)(iv)). The current design uses AES-256-GCM with a per-patient data key and a key-encryption key in `MEDICAL_DOCTOR_DATA_KEYS`.
- [ ] Key management:
  - [ ] Move the key-encryption key from a Vercel env var to a KMS (AWS KMS, GCP KMS or Vault).
  - [ ] Define who can read the key.
  - [ ] Set a rotation schedule (`rewrap_keys`).
- [ ] Audit log:
  - [ ] Retention of 6 years.
  - [ ] Who reviews it.
  - [ ] Whether to export it off the database.
- [ ] Erasure (crypto-shredding): document it in the retention and disposal policy. Check Supabase backup and PITR retention.
- [ ] Minimum necessary: review what `summarize()` sends to the model.

## Data in transit (every hop)
- [ ] **Client → Vercel:** HTTPS only. Check HSTS, the TLS version, and that preview deployments never receive real patient data.
- [ ] **Vercel → Supabase:** today the server forces `sslmode=require`, which encrypts but does not verify the certificate. Switch to `verify-full` with Supabase's CA (`sslrootcert`).
- [ ] **Vercel → model providers:** HTTPS. Each provider needs a BAA (or zero-retention terms):
  - [ ] Anthropic
  - [ ] OpenAI
  - [ ] Meta Model API. Its BAA is unknown and must be confirmed. `*-contributor` models are already refused.
  - [ ] Llama host
- [ ] **Vercel itself:** HIPAA requires a BAA (Enterprise or the HIPAA add-on). Function memory and logs see consult text.
- [ ] **Outbound lookups:**
  - [ ] PubMed, openFDA, RxNorm and ClinicalTrials receive only drug names, search terms and PMIDs. Confirm no identifiers leak into queries.
  - [ ] The model's guideline web search sends model-written queries to trusted publishers. Check that patient details don't end up in those queries.
- [ ] **Logs and traces:** confirm no patient data (tests exist). Check where logs go (Vercel, OTLP backend) and whether those vendors need BAAs.
- [ ] **OAuth providers** (Google and others) receive identity only. Confirm.

## Admin / process
- [ ] Risk analysis document, breach notification process, workforce access policy, and BAA inventory.
