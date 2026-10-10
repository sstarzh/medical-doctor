"""Entry point.

    medical-doctor-mcp                         # stdio (local clients)
    medical-doctor-mcp --transport http        # streamable HTTP on :8000/mcp (remote clients)
    medical-doctor-mcp gen-key [label]         # generate an API key
    medical-doctor-mcp check-freshness         # skill/guideline review status (exit 1 if overdue)
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys


def check_freshness() -> int:
    import json

    from .skills import freshness_report

    report = freshness_report()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        for r in report["skills"]:
            old = ", ".join(f"{g['guideline']} {g['year']}" for g in r["older_guidelines"])
            print(f"{r['status']:<15} {r['last_reviewed'] or '-':<11} clinical_review={r['clinical_review']:<8} {r['skill']}"
                  + (f"  [older editions cited: {old}]" if old else ""))
        print(f"\n{len(report['review_due'])} overdue, {len(report['pending_clinical_review'])} awaiting clinician sign-off, "
              f"{report['older_guideline_citations']} citations of guideline editions over 5 years old")
    return 1 if report["review_due"] else 0


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "check-freshness":
        sys.exit(check_freshness())
    if len(sys.argv) > 1 and sys.argv[1] == "gen-data-key":
        from .records.crypto import generate_key

        kid = sys.argv[2] if len(sys.argv) > 2 else "k1"
        print(f"{kid}:{generate_key()}")
        print("Set MEDICAL_DOCTOR_DATA_KEYS to this value (prepend new keys to rotate; keep old ones until re-wrapped).", file=sys.stderr)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "gen-key":
        label = sys.argv[2] if len(sys.argv) > 2 else "agent"
        print(f"{label}:mdk_{secrets.token_urlsafe(32)}")
        print("Add to MEDICAL_DOCTOR_API_KEYS (comma-separated); clients send the part after the colon as a Bearer token.", file=sys.stderr)
        return

    parser = argparse.ArgumentParser(prog="medical-doctor-mcp", description="Medical Doctor MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default=os.environ.get("MEDICAL_DOCTOR_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--stateless", action="store_true", default=os.environ.get("MEDICAL_DOCTOR_STATELESS") == "1",
                        help="no server-side sessions (for load-balanced/serverless deployments)")
    parser.add_argument("--json-response", action="store_true", help="plain JSON responses instead of SSE streams")
    args = parser.parse_args()
    from .observability import configure

    configure()

    if args.transport == "stdio":
        from .config import load_settings
        from .server import build_server

        # Single local user: records live encrypted in ~/.medical-doctor with a local key file.
        build_server(load_settings(), local_mode=True).run("stdio")
        return

    import uvicorn

    from .config import assert_safe_exposure, load_settings
    from .http import create_app

    settings = load_settings()
    assert_safe_exposure(settings, args.host)
    app = create_app(settings, host=args.host, stateless=args.stateless, json_response=args.json_response)
    # Trust X-Forwarded-* only from known proxies (uvicorn default: 127.0.0.1). Behind a load
    # balancer that overwrites these headers (Cloud Run, ALB, ingress), set FORWARDED_ALLOW_IPS
    # to its address range; never "*" when clients can reach the container directly.
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
