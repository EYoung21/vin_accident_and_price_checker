"""Thin, optional AWS Bedrock wrapper (Converse API).

Model-agnostic: works with DeepSeek-V3 (cheap) or a Claude model id via
BEDROCK_MODEL_ID. Every call degrades gracefully — if boto3 is missing, creds are
absent, or the call errors, callers get None and fall back to deterministic logic.
The whole tool runs fine with --no-llm; the LLM only adds fuzzy text reasoning.
"""

from __future__ import annotations

import json

from .config import CONFIG


def available() -> bool:
    try:
        import boto3  # noqa: F401
    except ImportError:
        return False
    return True


def chat_json(
    system: str, messages: list[dict], max_tokens: int = 800
) -> tuple[dict | None, str | None]:
    """Multi-turn Converse call. `messages` is a list of {"role","content"} where
    content is a plain string. Returns (parsed_json, raw_assistant_text). Either
    element is None on failure, so callers can both branch on success and append
    the assistant turn to keep the conversation going."""
    if not available():
        return None, None
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=CONFIG.aws_region)
        resp = client.converse(
            modelId=CONFIG.bedrock_model_id,
            system=[{"text": system}],
            messages=[{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        return _extract_json(text), text
    except Exception:
        return None, None


def complete_json(system: str, user: str, max_tokens: int = 800) -> dict | None:
    """Single-turn convenience wrapper over chat_json."""
    data, _ = chat_json(system, [{"role": "user", "content": user}], max_tokens)
    return data


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # tolerate ```json fences and leading prose
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
