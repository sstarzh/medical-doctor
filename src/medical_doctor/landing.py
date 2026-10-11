"""The human-facing setup page served at "/" to browsers (API clients still get JSON)."""

from __future__ import annotations

import html
from urllib.parse import urlencode, urlparse

from starlette.responses import HTMLResponse

from .config import Settings
from .web import page_headers

# Opens Claude's "Add custom connector" dialog (connectors moved from Settings to Customize). The prefill
# parameters are undocumented, so both known spellings are sent; if Claude ignores them the link still lands
# on the connectors page and the user pastes the copied address.
CLAUDE_CONNECTORS = "https://claude.ai/customize/connectors"


def claude_add_link(mcp_url: str, name: str = "Medical Doctor") -> str:
    return f"{CLAUDE_CONNECTORS}?" + urlencode({
        "modal": "add-custom-connector", "connectorName": name, "connectorUrl": mcp_url,
        "mcpName": name, "mcpServerUrl": mcp_url,
    })


# Static, so the CSP can pin it by hash and the page stays cacheable. With no public address the
# server leaves the URL blank and the browser fills in its own origin (never the request's Host header).
_SCRIPT = """
(function () {
  var el = document.getElementById("mcp-url"), out = document.getElementById("copied");
  if (!el.textContent.trim()) { el.textContent = location.origin + "/mcp"; }
  function manual() {
    var r = document.createRange(); r.selectNodeContents(el);
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    out.textContent = "Couldn't copy automatically. The address is selected: press Ctrl+C (or \u2318C on a Mac).";
  }
  function copy(msg) {
    if (!(navigator.clipboard && window.isSecureContext)) { manual(); return; }
    navigator.clipboard.writeText(el.textContent.trim()).then(function () { out.textContent = msg; }, manual);
  }
  document.getElementById("copy").addEventListener("click", function () { copy("Copied!"); });
  var add = document.getElementById("add-claude");
  if (add) { add.addEventListener("click", function () { copy("Address copied too, in case you need to paste it."); }); }
})();
"""

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
<p class="lead">{lead}</p>

<section class="card">
  {button}
  <div class="url">
    <code id="mcp-url">{mcp_url}</code>
    <button id="copy" type="button">Copy</button>
  </div>
  <p class="copied" id="copied" aria-live="polite"></p>
  <ol>{steps}</ol>
</section>
{chatgpt}
<p class="muted">{privacy}</p>
<p class="warn"><strong>Emergency?</strong> Call 911 (US), 112 (EU) or 999 (UK) now. This is guidance, not a replacement for a doctor.</p>
<p class="muted">v{version} · <a href="https://github.com/sstarzh/medical-doctor" rel="noopener">Project</a></p>
</main>
<script>{script}</script>
</body>
</html>
"""


def _sign_in(settings: Settings) -> str:
    if settings.auth_mode == "oauth":
        names = list(dict.fromkeys(i.display_name for i in settings.idps)) or ["your account"]
        return "sign in with " + " or ".join(html.escape(n) for n in names)
    if settings.auth_mode == "apikey":
        return "enter the access key you were given"
    return ""


def landing_page(settings: Settings, version: str) -> HTMLResponse:
    public = urlparse(settings.public_url).scheme == "https"
    sign_in = _sign_in(settings)
    connect = f"Click <strong>Add</strong>, then <strong>Connect</strong>, and {sign_in}." if sign_in else "Click <strong>Add</strong>."
    if public:
        mcp_url = settings.resource_url
        lead = "Add it to Claude once. Then just ask about symptoms, medicines or test results, and it remembers your history."
        button = (f'<a class="btn" id="add-claude" href="{html.escape(claude_add_link(mcp_url))}" '
                  'target="_blank" rel="noopener">Add to Claude</a>')
        steps = ("<li>Click <strong>Add to Claude</strong>. If the form is empty, paste the address above.</li>"
                 f"<li>{connect}</li><li>Start a new chat and ask your question.</li>")
        chatgpt = ('<details>\n  <summary>Using ChatGPT instead?</summary>\n  <ol>\n'
                   '    <li>Open <a href="https://chatgpt.com/#settings/Connectors" target="_blank" rel="noopener">ChatGPT connector '
                   'settings</a>. If there is no Create button, turn on <strong>Developer mode</strong> under Advanced.</li>\n'
                   f"    <li>Click <strong>Create</strong>, paste the address above{', choose OAuth, and ' + sign_in if sign_in else ''}.</li>\n"
                   "  </ol>\n</details>\n")
        cache = "private, max-age=300"  # per-browser only: "/" is content-negotiated
    else:
        # Local or plain-http server: claude.ai and ChatGPT connect from the cloud and need public https.
        mcp_url = settings.resource_url if settings.public_url else ""
        lead = ("This server has no public https address. Claude and ChatGPT connectors are reached from the cloud "
                "(Claude Desktop's too), so they can't use it. Connect from this computer instead.")
        button = ""
        steps = ("<li>Claude Code: <code>claude mcp add --transport http medical-doctor &lt;address&gt;</code></li>"
                 "<li>Claude Desktop: run the server as a local app instead, with the command "
                 "<code>medical-doctor-mcp</code> in <code>claude_desktop_config.json</code> (see the README).</li>"
                 "<li>To use it from claude.ai, ChatGPT or your phone, deploy it with a public https address.</li>")
        chatgpt = ""
        cache = "no-store"
    privacy = ("Your record is encrypted" + (", private to your account," if sign_in else "")
               + " and you can erase it anytime. Prescriptions and referrals are drafts for your own doctor.")
    body = _PAGE.format(lead=lead, button=button, mcp_url=html.escape(mcp_url), steps=steps, chatgpt=chatgpt,
                        privacy=privacy, version=html.escape(version), script=_SCRIPT)
    return HTMLResponse(body, headers=page_headers(scripts=(_SCRIPT,), cache=cache, Vary="Accept"))
