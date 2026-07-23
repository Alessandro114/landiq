"""
LandIQ AI Provider — free-tier provider chain for the agent's reasoning.

Chain: Groq (llama-3.3-70b) -> Cerebras (gpt-oss-120b) -> Mistral -> rule-based fallback.
All providers have generous free tiers. Zero cost for up to ~1,000 reports/day.

Users can override via env:
    LANDIQ_AI_PROVIDER=groq|cerebras|mistral|openai|ollama
    LANDIQ_AI_KEY=...
    LANDIQ_AI_MODEL=...  (optional, auto-selected per provider)

If no key is set, the agent falls back to rule-based verdicts (still functional,
just no narrative AI summary).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Provider configs: (env_key_name, base_url, default_model)
_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    "cerebras": ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", "llama-3.3-70b"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "ollama": ("OLLAMA_HOST", "http://localhost:11434/v1/chat/completions", "llama3.1"),
}

# Chain order — tried in sequence until one works
_CHAIN_ORDER = ["groq", "cerebras", "mistral"]


def _get_configured_providers() -> list[tuple[str, str, str, str]]:
    """Return list of (provider_name, api_key, base_url, model) for configured providers."""
    # If user explicitly set a provider, use only that
    explicit = os.getenv("LANDIQ_AI_PROVIDER", "").lower().strip()
    explicit_key = os.getenv("LANDIQ_AI_KEY", "").strip()
    explicit_model = os.getenv("LANDIQ_AI_MODEL", "").strip()

    if explicit and explicit in _PROVIDERS:
        env_key, base_url, default_model = _PROVIDERS[explicit]
        key = explicit_key or os.getenv(env_key, "")
        model = explicit_model or default_model
        if key or explicit == "ollama":
            return [(explicit, key, base_url, model)]

    # Auto-chain: try each provider in order
    result = []
    for name in _CHAIN_ORDER:
        env_key, base_url, default_model = _PROVIDERS[name]
        key = os.getenv(env_key, "").strip()
        if key:
            result.append((name, key, base_url, explicit_model or default_model))
    return result


def call_llm(prompt: str, system: str = "", max_tokens: int = 500, temperature: float = 0.3) -> str | None:
    """Call LLM via provider chain. Returns text or None if all fail."""
    providers = _get_configured_providers()
    if not providers:
        logger.info("No AI provider configured — using rule-based fallback")
        return None

    import urllib.request
    import urllib.error

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for name, api_key, base_url, model in providers:
        try:
            body = json.dumps({
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }).encode()

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "LandIQ-Agent/0.3",
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            req = urllib.request.Request(base_url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                text = data["choices"][0]["message"]["content"].strip()
                if text:
                    logger.info("AI provider %s/%s returned %d chars", name, model, len(text))
                    return text
        except Exception as e:
            logger.warning("AI provider %s failed: %s — trying next", name, e)
            continue

    logger.warning("All AI providers failed — using rule-based fallback")
    return None


def estimate_price(city: str, country: str, use_type: str = "residential") -> float | None:
    """Ask the LLM for a quick price estimate. Returns EUR/sqm or None."""
    prompt = (
        f"What is the approximate median {use_type} real estate price per sqm in EUR "
        f"for {city}, {country}? "
        f"Reply with ONLY a number (no units, no text, no explanation). Example: 2400"
    )
    result = call_llm(prompt, max_tokens=20, temperature=0.1)
    if result:
        try:
            # Extract first number from response
            clean = result.strip().replace(",", "").replace("€", "").replace("EUR", "")
            for token in clean.split():
                try:
                    return float(token)
                except ValueError:
                    continue
        except Exception:
            pass
    return None
