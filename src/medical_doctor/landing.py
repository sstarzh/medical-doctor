"""The human-facing setup page served at "/" to browsers (API clients still get JSON)."""

from __future__ import annotations

import html
import secrets

from starlette.responses import HTMLResponse

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Medical Doctor — add your personal doctor to Claude or ChatGPT</title>
<meta name="description" content="Add a personal AI doctor to Claude or ChatGPT in about a minute.">
<style>
:root{{color-scheme:light dark;--bg:#f5f7fa;--card:#fff;--fg:#17202a;--muted:#5a6573;--line:#dde2e8;
--accent:#0b6bcb;--accent-fg:#fff;--soft:#e8f1fb;--warn-bg:#fff4e5;--warn-fg:#7a4100;--ok:#1a7f4b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1217;--card:#161c23;--fg:#e8edf2;--muted:#9aa6b4;
--line:#26303b;--accent:#5aa9ff;--accent-fg:#06121f;--soft:#13243a;--warn-bg:#2a1d0c;--warn-fg:#f3c58a;--ok:#4cc38a}}}}
*{{box-sizing:border-box}}
body{{margin:0;font:17px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:760px;margin:0 auto;padding:32px 16px 56px}}
header h1{{font-size:clamp(1.6rem,4vw,2.2rem);line-height:1.2;margin:0 0 8px}}
header p{{color:var(--muted);margin:0 0 24px;font-size:1.05rem}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin:0 0 18px}}
h2{{font-size:1.2rem;margin:0 0 12px}}
.url{{display:flex;gap:8px;flex-wrap:wrap;align-items:stretch}}
.url code{{flex:1 1 260px;font:600 1rem ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);
border:1px solid var(--line);border-radius:10px;padding:12px 14px;overflow-wrap:anywhere}}
button{{font:600 1rem system-ui,sans-serif;border:0;border-radius:10px;padding:12px 18px;cursor:pointer;
background:var(--accent);color:var(--accent-fg);min-width:120px}}
button:focus-visible{{outline:3px solid var(--fg);outline-offset:2px}}
.copied{{color:var(--ok);font-weight:600;min-height:1.5em;margin:8px 0 0}}
.tabs{{display:flex;gap:8px;margin:0 0 14px;flex-wrap:wrap}}
.tab{{background:transparent;color:var(--fg);border:1px solid var(--line)}}
.tab[aria-selected="true"]{{background:var(--accent);color:var(--accent-fg);border-color:var(--accent)}}
ol{{margin:0;padding-left:1.4em}} ol li{{margin:0 0 10px}}
.muted{{color:var(--muted);font-size:.95rem}}
.warn{{background:var(--warn-bg);color:var(--warn-fg);border-radius:12px;padding:14px 16px;margin:0 0 18px}}
ul{{margin:0;padding-left:1.2em}} ul li{{margin:0 0 8px}}
kbd{{font:600 .9em ui-monospace,monospace;background:var(--soft);border:1px solid var(--line);border-radius:6px;padding:1px 6px}}
blockquote{{margin:10px 0 0;padding:12px 14px;border-left:4px solid var(--accent);background:var(--soft);border-radius:0 10px 10px 0}}
footer{{color:var(--muted);font-size:.9rem;margin-top:28px}}
a{{color:var(--accent)}}
</style>
</head>
<body>
<main>
<header>
  <h1>Your personal AI doctor, inside Claude or ChatGPT</h1>
  <p>Ask about symptoms, medicines, test results or your child's health, and get a careful, evidence-based answer that remembers your history.</p>
</header>

<div class="warn" role="note"><strong>In an emergency, call your local emergency number now</strong> (911 in the US, 112 in Europe, 999 in the UK). This service gives guidance; it does not replace a doctor who can examine you.</div>

<section class="card" aria-labelledby="s1">
  <h2 id="s1">Step 1 — Copy this address</h2>
  <div class="url">
    <code id="mcp-url">{mcp_url}</code>
    <button id="copy" type="button">Copy</button>
  </div>
  <p class="copied" id="copied" aria-live="polite"></p>
</section>

<section class="card" aria-labelledby="s2">
  <h2 id="s2">Step 2 — Add it to your AI app</h2>
  <div class="tabs" role="tablist">
    <button class="tab" role="tab" id="t-claude" aria-controls="p-claude" aria-selected="true">Claude</button>
    <button class="tab" role="tab" id="t-chatgpt" aria-controls="p-chatgpt" aria-selected="false">ChatGPT</button>
  </div>
  <div id="p-claude" role="tabpanel" aria-labelledby="t-claude">
    <ol>
      <li>Open <a href="https://claude.ai" rel="noopener">claude.ai</a> (or the Claude app) and go to <strong>Settings → Connectors</strong>.</li>
      <li>Choose <strong>Add custom connector</strong>.</li>
      <li>Name it <strong>Medical Doctor</strong>, paste the address from Step 1, and click <strong>Add</strong>.</li>
      <li>Click <strong>Connect</strong> and sign in with your Google account.</li>
      <li>When Claude asks permission to use the doctor's tools, choose <strong>Always allow</strong> so it can remember what you tell it.</li>
    </ol>
  </div>
  <div id="p-chatgpt" role="tabpanel" aria-labelledby="t-chatgpt" hidden>
    <ol>
      <li>Open <a href="https://chatgpt.com" rel="noopener">chatgpt.com</a> and go to <strong>Settings → Apps &amp; Connectors</strong>.</li>
      <li>Choose to add or create a <strong>custom connector</strong>. If you don't see that option, turn on <strong>Developer mode</strong> under <strong>Advanced settings</strong> on the same page.</li>
      <li>Name it <strong>Medical Doctor</strong>, paste the address from Step 1, and choose <strong>OAuth</strong> sign-in if asked.</li>
      <li>Click <strong>Connect</strong> and sign in with your Google account.</li>
      <li>In a new chat, make sure Medical Doctor is switched on for that conversation.</li>
    </ol>
  </div>
  <p class="muted">Menus move around between app versions. If a step looks different, look for "Connectors" or "Apps" in Settings.</p>
</section>

<section class="card" aria-labelledby="s3">
  <h2 id="s3">Step 3 — Make it your default doctor (recommended)</h2>
  <p>Add this sentence to your Claude <em>personal preferences</em> (Settings → Profile) or ChatGPT <em>custom instructions</em>, so the doctor is used automatically:</p>
  <blockquote id="pref">For anything about my health, symptoms, medicines, test results, or my family's health, always use the Medical Doctor connector and keep my chart up to date.</blockquote>
  <p><button id="copy-pref" type="button">Copy sentence</button></p>
  <p class="copied" id="copied-pref" aria-live="polite"></p>
</section>

<section class="card" aria-labelledby="s4">
  <h2 id="s4">Try it</h2>
  <ul>
    <li>"I've had a sore throat and fever for two days. What should I do?"</li>
    <li>"I take lisinopril and I'm allergic to penicillin. Please remember that."</li>
    <li>"My 3-year-old has a rash and a fever."</li>
    <li>"Can I get a second opinion on what my doctor told me?"</li>
  </ul>
</section>

<section class="card" aria-labelledby="s5">
  <h2 id="s5">Your privacy</h2>
  <ul>
    <li>Your health record is <strong>encrypted</strong> and private to your Google account. Nobody else can read it.</li>
    <li>You can export it or <strong>permanently erase</strong> it at any time. Just ask in the chat.</li>
    <li>Prescriptions and referrals are <strong>drafts for your own doctor</strong> to review, never real prescriptions.</li>
  </ul>
</section>

<footer>
  <p>Medical Doctor is clinical decision support, not a diagnosis or a substitute for a licensed clinician. Version {version}. <a href="https://github.com/sstarzh/medical-doctor" rel="noopener">Project</a></p>
</footer>
</main>
<script nonce="{nonce}">
(function () {{
  function copy(text, out) {{
    var done = function () {{ out.textContent = "Copied! Now follow Step 2."; }};
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(text).then(done, function () {{ out.textContent = "Select the text and copy it manually."; }});
    }} else {{
      out.textContent = "Select the text and copy it manually.";
    }}
  }}
  document.getElementById("copy").addEventListener("click", function () {{
    copy(document.getElementById("mcp-url").textContent.trim(), document.getElementById("copied"));
  }});
  document.getElementById("copy-pref").addEventListener("click", function () {{
    copy(document.getElementById("pref").textContent.trim(), document.getElementById("copied-pref"));
  }});
  var tabs = document.querySelectorAll(".tab");
  tabs.forEach(function (tab) {{
    tab.addEventListener("click", function () {{
      tabs.forEach(function (t) {{
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      }});
    }});
  }});
  if (/chatgpt/i.test(location.hash)) {{ document.getElementById("t-chatgpt").click(); }}
}})();
</script>
</body>
</html>
"""


def landing_page(mcp_url: str, version: str) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    body = _PAGE.format(mcp_url=html.escape(mcp_url), version=html.escape(version), nonce=nonce)
    return HTMLResponse(body, headers={
        "Content-Security-Policy": (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
                                    "img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=300",
    })
