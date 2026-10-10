"""Vercel entry point (auto-detected): exposes the MCP server as an ASGI `app`.

Configure with environment variables in the Vercel project (see docs/DEPLOY-VERCEL.md).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from medical_doctor.serverless import create_serverless_app  # noqa: E402

app = create_serverless_app()
