"""The human-facing setup page served at "/" to browsers (API clients still get JSON)."""

from __future__ import annotations

import html
import secrets
from urllib.parse import urlencode

from starlette.responses import HTMLResponse

# Opens Claude's "Add custom connector" dialog. The prefill parameters are undocumented; if Claude
# ignores them the link still lands on the connectors page and the user pastes the copied address.
CLAUDE_CONNECTORS = "https://claude.ai/settings/connectors"


def claude_add_link(mcp_url: str, name: str = "Medical Doctor") -> str:
    return f"{CLAUDE_CONNECTORS}?" + urlencode({"modal": "add-custom-connector", "mcpName": name, "mcpServerUrl": mcp_url})

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Medical Doctor</title>
<meta name="description" content="Add a personal AI doctor to Claude or ChatGPT in about a minute.">
<style>
:root{{color-scheme:light dark;--bg:#f5f7fa;--card:#fff;--fg:#17202a;--muted:#5a6573;--line:#dde2e8;
--accent:#0b6bcb;--accent-fg:#fff;--soft:#e8f1fb;--warn-fg:#9a3412;--ok:#1a7f4b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1217;--card:#161c23;--fg:#e8edf2;--muted:#9aa6b4;
--line:#26303b;--accent:#5aa9ff;--accent-fg:#06121f;--soft:#13243a;--warn-fg:#f3c58a;--ok:#4cc38a}}}}
*{{box-sizing:border-box}}
body{{margin:0;font:17px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:560px;margin:0 auto;padding:40px 16px 48px}}
h1{{font-size:clamp(1.6rem,5vw,2rem);line-height:1.2;margin:0 0 8px}}
.lead{{color:var(--muted);margin:0 0 24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin:0 0 16px}}
.btn{{display:block;text-align:center;text-decoration:none;font:600 1.1rem system-ui,sans-serif;border:0;border-radius:12px;
padding:14px 18px;cursor:pointer;background:var(--accent);color:var(--accent-fg);width:100%}}
.btn:focus-visible,button:focus-visible{{outline:3px solid var(--fg);outline-offset:2px}}
.url{{display:flex;gap:8px;margin:16px 0 0}}
.url code{{flex:1;min-width:0;font:600 .95rem ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);
border:1px solid var(--line);border-radius:10px;padding:10px 12px;overflow-wrap:anywhere}}
.url button{{font:600 .95rem system-ui,sans-serif;border:1px solid var(--line);border-radius:10px;padding:10px 14px;
cursor:pointer;background:transparent;color:var(--fg)}}
.copied{{color:var(--ok);font-weight:600;min-height:1.5em;margin:6px 0 0;font-size:.95rem}}
ol{{margin:12px 0 0;padding-left:1.3em}} li{{margin:0 0 6px}}
details{{margin:0 0 16px}} summary{{cursor:pointer;font-weight:600}}
.muted{{color:var(--muted);font-size:.92rem}}
.warn{{color:var(--warn-fg);font-size:.95rem;margin:24px 0 0}}
a{{color:var(--accent)}}
</style>
</head>
<body>
<main>
<h1>Your personal AI doctor</h1>
<p class="lead">Add it to Claude once. Then just ask about symptoms, medicines or test results, and it remembers your history.</p>

<section class="card">
  <a class="btn" id="add-claude" href="{claude_link}" target="_blank" rel="noopener">Add to Claude</a>
  <div class="url">
    <code id="mcp-url">{mcp_url}</code>
    <button id="copy" type="button">Copy</button>
  </div>
  <p class="copied" id="copied" aria-live="polite"></p>
  <ol>
    <li>Click <strong>Add to Claude</strong>. If the form is empty, paste the address above.</li>
    <li>Click <strong>Add</strong>, then <strong>Connect</strong>, and sign in with Google.</li>
    <li>Start a new chat and ask your question.</li>
  </ol>
</section>

<details>
  <summary>Using ChatGPT instead?</summary>
  <ol>
    <li>Open <a href="https://chatgpt.com/#settings/Connectors" target="_blank" rel="noopener">ChatGPT connector settings</a>. If there is no Create button, turn on <strong>Developer mode</strong> under Advanced.</li>
    <li>Click <strong>Create</strong>, paste the address above, choose OAuth, and sign in with Google.</li>
  </ol>
</details>

<p class="muted">Your record is encrypted, private to your Google account, and you can erase it anytime. Prescriptions and referrals are drafts for your own doctor.</p>
<p class="warn"><strong>Emergency?</strong> Call 911 (US), 112 (EU) or 999 (UK) now. This is guidance, not a replacement for a doctor.</p>
<p class="muted">v{version} · <a href="https://github.com/sstarzh/medical-doctor" rel="noopener">Project</a></p>
</main>
<script nonce="{nonce}">
(function () {{
  var url = document.getElementById("mcp-url").textContent.trim(), out = document.getElementById("copied");
  function copy(msg) {{
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(url).then(function () {{ out.textContent = msg; }}, function () {{}});
    }}
  }}
  document.getElementById("copy").addEventListener("click", function () {{ copy("Copied!"); }});
  document.getElementById("add-claude").addEventListener("click", function () {{ copy("Address copied too, in case you need to paste it."); }});
}})();
</script>
</body>
</html>
"""

def landing_page(mcp_url: str, version: str) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    body = _PAGE.format(mcp_url=html.escape(mcp_url), claude_link=html.escape(claude_add_link(mcp_url)),
                        version=html.escape(version), nonce=nonce)
    return HTMLResponse(body, headers={
        "Content-Security-Policy": (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
                                    "img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=300",
    })
