"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv not installed; env vars still work
    pass


@dataclass(frozen=True)
class Config:
    home_zip: str = os.getenv("HOME_ZIP", "37377")
    search_radius_miles: int = int(os.getenv("SEARCH_RADIUS_MILES", "100"))
    # Auto.dev: 1,000 free calls/mo, no credit card. Preferred comps source.
    autodev_api_key: str | None = os.getenv("AUTODEV_API_KEY") or None
    marketcheck_api_key: str | None = os.getenv("MARKETCHECK_API_KEY") or None
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    # Claude Haiku 4.5 on Bedrock (verified in us-east-1; needs the us. inference
    # prefix). Cheap + capable for parsing/negotiation. Swap via BEDROCK_MODEL_ID
    # (e.g. a Sonnet/Opus id for sharper negotiation, or deepseek.v3.2).
    bedrock_model_id: str = os.getenv(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

    # Network defaults
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "20"))


CONFIG = Config()
