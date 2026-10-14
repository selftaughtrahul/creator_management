import json
import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger("creators.anthropic")

MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class AnthropicError(Exception):
    pass


def anthropic_configured():
    return bool(getattr(settings, "ANTHROPIC_API_KEY", ""))


def complete_json(prompt, *, max_tokens=1024):
    """Ask Claude for a JSON object. Never logs the API key."""
    key = getattr(settings, "ANTHROPIC_API_KEY", "")
    if not key:
        raise AnthropicError("ANTHROPIC_API_KEY is not set.")
    model = getattr(settings, "ANTHROPIC_MODEL", "") or "claude-sonnet-4-5"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        response = requests.post(MESSAGES_URL, headers=headers, json=payload, timeout=40)
    except requests.RequestException as exc:
        logger.warning("anthropic_request_failed error=%s", exc.__class__.__name__)
        raise AnthropicError("Could not reach Anthropic.") from exc
    if response.status_code >= 400:
        logger.warning("anthropic_http_error status=%s", response.status_code)
        raise AnthropicError(f"Anthropic returned HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise AnthropicError("Anthropic returned a non-JSON response.") from exc
    text = _content_text(data)
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        raise AnthropicError("Anthropic did not return a JSON object.")
    return parsed


def _content_text(data):
    blocks = data.get("content") or []
    parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(parts).strip()


def _extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
