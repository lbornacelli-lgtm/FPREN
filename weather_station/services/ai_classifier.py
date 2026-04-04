#!/usr/bin/env python3
# weather_station/services/ai_classifier.py
"""
ai_classifier.py
----------------
Uses LiteLLM to:
  1. Classify alert severity (routine / elevated / critical)
  2. Rewrite raw NWS alert text into broadcast-ready radio copy
  3. Decide TTS voice routing (gTTS for routine, ElevenLabs for elevated/critical)

Used by zone_alert_tts.py before generating audio.
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from weather_station.services.ai_client import chat, is_configured

logger = logging.getLogger("ai_classifier")

# ── Severity levels ───────────────────────────────────────────────────────────

SEVERITY_ROUTINE  = "routine"
SEVERITY_ELEVATED = "elevated"
SEVERITY_CRITICAL = "critical"

# NWS severity → baseline level (AI can override upward)
NWS_SEVERITY_MAP = {
    "extreme":  SEVERITY_CRITICAL,
    "severe":   SEVERITY_CRITICAL,
    "moderate": SEVERITY_ELEVATED,
    "minor":    SEVERITY_ROUTINE,
    "unknown":  SEVERITY_ROUTINE,
}

# Events that are always critical regardless of NWS severity field
ALWAYS_CRITICAL = {
    "tornado emergency", "tornado warning",
    "flash flood emergency",
    "hurricane warning", "storm surge warning",
    "extreme wind warning",
}

# ── TTS voice routing ─────────────────────────────────────────────────────────

TTS_ENGINE_PIPER      = "piper"
TTS_ENGINE_ELEVENLABS = "elevenlabs"


def route_tts_engine(severity_level: str) -> str:
    """Return TTS engine name based on severity level."""
    if severity_level in (SEVERITY_ELEVATED, SEVERITY_CRITICAL):
        return TTS_ENGINE_ELEVENLABS
    return TTS_ENGINE_PIPER


# ── System prompts ────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """You are an emergency alert classifier for a Florida weather radio station.
Given an NWS alert, classify its severity as exactly one of: routine, elevated, critical.
- critical: immediate threat to life or property (tornado, hurricane, flash flood emergency, storm surge)
- elevated: significant hazard requiring attention (flood warning, severe thunderstorm, tropical storm)
- routine: informational or minor (advisories, statements, watches with low immediate risk)
Respond with ONLY one word: routine, elevated, or critical."""

_REWRITE_SYSTEM = """You are a professional weather radio announcer for FPREN, Florida Public Radio Emergency Network.
Rewrite the NWS alert into a concise, clear, broadcast-ready radio script.
- Lead with the alert type and affected area
- Use plain spoken English, no jargon or codes
- Keep it under 60 words
- End with "Stay safe and monitor local conditions."
- Output plain text only, no quotes or formatting"""


# ── Core functions ────────────────────────────────────────────────────────────

def classify_alert(alert: dict) -> str:
    """
    Classify alert severity using AI with NWS severity as baseline.
    Returns one of: routine, elevated, critical.
    Falls back to NWS-based classification if AI is unavailable.
    """
    event    = (alert.get("event") or "").lower().strip()
    severity = (alert.get("severity") or "").lower().strip()

    # Hard-coded critical events — no AI needed
    if event in ALWAYS_CRITICAL:
        return SEVERITY_CRITICAL

    # Baseline from NWS severity field
    baseline = NWS_SEVERITY_MAP.get(severity, SEVERITY_ROUTINE)

    if not is_configured():
        logger.debug("AI not configured — using NWS baseline: %s", baseline)
        return baseline

    # Build prompt
    headline = (alert.get("headline") or "").strip()
    area     = (alert.get("area_desc") or "").strip()[:100]
    prompt   = f"Event: {alert.get('event','')}\nSeverity: {severity}\nHeadline: {headline}\nArea: {area}"

    try:
        result = chat(prompt, system=_CLASSIFY_SYSTEM, max_tokens=10).lower().strip()
        if result in (SEVERITY_ROUTINE, SEVERITY_ELEVATED, SEVERITY_CRITICAL):
            if result != baseline:
                logger.info("AI reclassified '%s' from %s → %s", event, baseline, result)
            return result
        else:
            logger.warning("AI returned unexpected classification '%s' — using baseline", result)
            return baseline
    except Exception as e:
        logger.warning("AI classification failed: %s — using baseline", e)
        return baseline


def _validate_rewrite(text: str, alert: dict) -> tuple[bool, str]:
    """
    Validate that LLM-rewritten broadcast text is safe to air.

    Checks:
    - Not empty or whitespace-only
    - Word count is reasonable (5–100 words)
    - Does not contain the literal word "Error" or truncation markers
    - Contains the event type keyword (guards against hallucinated topic drift)

    Returns (is_valid, reason_if_invalid).
    """
    if not text or not text.strip():
        return False, "empty output"

    words = text.split()
    if len(words) < 5:
        return False, f"too short ({len(words)} words)"
    if len(words) > 100:
        return False, f"too long ({len(words)} words)"

    lower = text.lower()
    if "error" in lower or "[truncated]" in lower or "..." in text:
        return False, "contains truncation or error marker"

    # The event name should appear somewhere in the output (loose check —
    # just first keyword, e.g. "tornado" from "Tornado Warning").
    event_keyword = (alert.get("event") or "").lower().split()[0] if alert.get("event") else ""
    if event_keyword and len(event_keyword) > 3 and event_keyword not in lower:
        return False, f"missing event keyword '{event_keyword}'"

    return True, ""


def rewrite_alert(alert: dict) -> str:
    """
    Rewrite alert text into broadcast-ready radio copy using AI.
    Validates the LLM output and retries once before falling back.
    Falls back to rule-based text if AI is unavailable or both attempts fail.
    """
    if not is_configured():
        return _fallback_text(alert)

    event    = alert.get("event", "Weather Alert")
    area     = alert.get("area_desc", "")
    headline = (alert.get("headline") or "").strip()
    desc     = (alert.get("description") or "")[:500]

    prompt = (
        f"Alert type: {event}\n"
        f"Affected area: {area}\n"
        f"Headline: {headline}\n"
        f"Details: {desc}"
    )

    for attempt in range(2):
        try:
            result = chat(prompt, system=_REWRITE_SYSTEM, max_tokens=120)
            valid, reason = _validate_rewrite(result, alert)
            if valid:
                if attempt > 0:
                    logger.info("AI rewrite succeeded on retry for: %s", event)
                else:
                    logger.info("AI rewrote alert: %s", event)
                return result
            else:
                logger.warning(
                    "AI rewrite validation failed (attempt %d/2) for '%s': %s — %s",
                    attempt + 1, event, reason,
                    "retrying" if attempt == 0 else "using fallback",
                )
        except Exception as e:
            logger.warning(
                "AI rewrite error (attempt %d/2) for '%s': %s — %s",
                attempt + 1, event, e,
                "retrying" if attempt == 0 else "using fallback",
            )

    return _fallback_text(alert)


def _fallback_text(alert: dict) -> str:
    """Simple fallback text builder when AI is unavailable."""
    event    = alert.get("event", "Weather Alert")
    area     = alert.get("area_desc", "")
    headline = (alert.get("headline") or "").strip().rstrip(".")
    parts    = [f"A {event} has been issued for {area}." if area else f"A {event} has been issued."]
    if headline:
        parts.append(headline + ".")
    parts.append("Stay safe and monitor local conditions.")
    return " ".join(parts)


def process_alert(alert: dict) -> dict:
    """
    Full AI processing pipeline for a single alert.
    Returns enriched dict with:
      - ai_severity: routine / elevated / critical
      - ai_text:     broadcast-ready script
      - tts_engine:  gtts / elevenlabs
    """
    severity   = classify_alert(alert)
    text       = rewrite_alert(alert)
    tts_engine = route_tts_engine(severity)

    return {
        "ai_severity": severity,
        "ai_text":     text,
        "tts_engine":  tts_engine,
    }
