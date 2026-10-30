"""Thin LLM client used only for the fuzzy text judgements.

Supported providers (set env vars; nothing is needed to run offline):
    LLM_PROVIDER=gemini     GEMINI_API_KEY=...     (free tier works)
    LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=...
    LLM_MODEL=<optional model override>

If no provider/key is set, `available()` is False and callers fall back to
rule-based checks. Every LLM answer is cached on disk, so re-running the
backtest does not pay twice for the same address.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .config import ROOT

CACHE_PATH = ROOT / "data" / "llm_cache.json"
DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "anthropic": "claude-haiku-4-5"}
_cache: dict | None = None


def provider() -> str | None:
    p = os.getenv("LLM_PROVIDER", "").lower().strip()
    key = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(p)
    return p if key and os.getenv(key) else None


def available() -> bool:
    return provider() is not None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    return _cache


def _save_cache() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_load_cache(), ensure_ascii=False, indent=1))


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def ask_json(system: str, user: str) -> dict | None:
    """Send a prompt, expect a JSON object back. Returns None on any failure."""
    p = provider()
    if not p:
        return None
    model = os.getenv("LLM_MODEL") or DEFAULT_MODELS[p]
    key = hashlib.sha256(f"{p}|{model}|{system}|{user}".encode()).hexdigest()[:24]
    cache = _load_cache()
    if key in cache:
        return cache[key]
    try:
        if p == "gemini":
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                f"?key={os.environ['GEMINI_API_KEY']}"
            )
            out = _post(
                url,
                {
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                },
                {},
            )
            text = out["candidates"][0]["content"]["parts"][0]["text"]
        else:  # anthropic
            out = _post(
                "https://api.anthropic.com/v1/messages",
                {
                    "model": model,
                    "max_tokens": 400,
                    "temperature": 0,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
                {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
            )
            text = out["content"][0]["text"]
        result = _extract_json(text)
    except Exception as e:  # network, quota, bad JSON -> fall back to rules
        print(f"[llm] {p} call failed, using rules instead: {e}")
        return None
    cache[key] = result
    _save_cache()
    return result
