"""Runtime configuration.

Non-secret settings (home location, radius, model, log dir) come from a TOML file
so the open-source repo is configurable: edit `config.toml` (falls back to the
committed `config.example.toml`, then built-in defaults). Secrets (API keys, AWS
creds) stay in environment / `.env` and are never written to the TOML.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # env vars still work without python-dotenv
    pass

_ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "home": {"zip": "37377", "name": "Signal Mountain, TN", "lat": 35.2120, "lon": -85.3436},
    "search": {"radius_miles": 100},
    "model": {"bedrock_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0"},
    "log": {"dir": "car_log"},
    "net": {"http_timeout": 20},
}


def _load_toml() -> dict:
    for name in ("config.toml", "config.example.toml"):
        p = _ROOT / name
        if p.exists():
            try:
                with p.open("rb") as f:
                    return tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError):
                pass
    return {}


_T = _load_toml()


def _cfg(section: str, key: str):
    return (_T.get(section) or {}).get(key, _DEFAULTS[section][key])


@dataclass(frozen=True)
class Config:
    # --- from config.toml (non-secret, open-source-configurable) ---
    home_zip: str = str(_cfg("home", "zip"))
    home_name: str = _cfg("home", "name")
    home_lat: float = float(_cfg("home", "lat"))
    home_lon: float = float(_cfg("home", "lon"))
    search_radius_miles: int = int(_cfg("search", "radius_miles"))
    log_dir: str = _cfg("log", "dir")
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT") or _cfg("net", "http_timeout"))
    # model id: env override wins, else config.toml
    bedrock_model_id: str = os.getenv("BEDROCK_MODEL_ID") or _cfg("model", "bedrock_model_id")

    # --- secrets (env / .env only) ---
    autodev_api_key: str | None = os.getenv("AUTODEV_API_KEY") or None
    marketcheck_api_key: str | None = os.getenv("MARKETCHECK_API_KEY") or None
    serper_api_key: str | None = os.getenv("SERPER_API_KEY") or None
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")

    @property
    def log_path(self) -> Path:
        return _ROOT / self.log_dir


CONFIG = Config()
