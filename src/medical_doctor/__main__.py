"""Entry point.

    medical-doctor-mcp                         # stdio (local clients)
    medical-doctor-mcp --transport http        # streamable HTTP on :8000/mcp (remote clients)
    medical-doctor-mcp gen-key [label]         # generate an API key
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys


def main() -> None:
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
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.transport == "stdio":
        from .server import mcp

        mcp.run("stdio")
        return

    import uvicorn

    from .config import load_settings
    from .http import create_app

    settings = load_settings()
    if settings.auth_mode == "none" and args.host not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("Serving on %s WITHOUT authentication. Set MEDICAL_DOCTOR_AUTH for any shared deployment.", args.host)
    app = create_app(settings, host=args.host, stateless=args.stateless, json_response=args.json_response)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
