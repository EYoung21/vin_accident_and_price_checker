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


def chat_with_search(system: str, messages: list[dict], search_fn,
                     max_tokens: int = 1000, max_rounds: int = 3) -> str | None:
    """Multi-turn chat where the model can call a web_search tool. `search_fn(query)`
    returns a list of {title, snippet, link}. Returns the final text, or None."""
    if not available():
        return None
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=CONFIG.aws_region)
        tool_config = {"tools": [{"toolSpec": {
            "name": "web_search",
            "description": "Search the web for current/external info — prices, specs, "
                           "reviews, common problems, recalls. Use it whenever fresh or "
                           "outside knowledge would improve the answer.",
            "inputSchema": {"json": {"type": "object",
                                     "properties": {"query": {"type": "string"}},
                                     "required": ["query"]}}}}]}
        bmsgs = [
            {"role": m["role"],
             "content": m["content"] if isinstance(m["content"], list) else [{"text": m["content"]}]}
            for m in messages
        ]
        out = None
        for _ in range(max_rounds):
            resp = client.converse(
                modelId=CONFIG.bedrock_model_id, system=[{"text": system}],
                messages=bmsgs, toolConfig=tool_config,
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.3})
            out = resp["output"]["message"]
            bmsgs.append(out)
            if resp.get("stopReason") != "tool_use":
                break
            results = []
            for block in out["content"]:
                tu = block.get("toolUse")
                if not tu:
                    continue
                hits = search_fn(tu["input"].get("query", "")) or []
                text = "\n".join(f"- {h.get('title','')}: {h.get('snippet','')} "
                                 f"({h.get('link','')})" for h in hits) or "no results"
                results.append({"toolResult": {"toolUseId": tu["toolUseId"],
                                               "content": [{"text": text[:4000]}]}})
            bmsgs.append({"role": "user", "content": results})
        return "\n".join(b["text"] for b in (out or {}).get("content", []) if "text" in b).strip() or None
    except Exception:
        return None


def chat_text(system: str, messages: list[dict], max_tokens: int = 1000) -> str | None:
    """Multi-turn free-text chat (for the post-report Q&A). Returns None on failure."""
    if not available():
        return None
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=CONFIG.aws_region)
        resp = client.converse(
            modelId=CONFIG.bedrock_model_id,
            system=[{"text": system}],
            messages=[{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.3},
        )
        return resp["output"]["message"]["content"][0]["text"]
    except Exception:
        return None


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
