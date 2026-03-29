"""AI-powered broadcast script generator for FPREN.

Uses the shared LiteLLM client to transform raw NWS alert text and weather
data into natural, broadcast-ready radio scripts before they are sent to TTS.

If the LiteLLM key is not configured or the API call fails, all functions
return the original text unchanged so the station keeps running.
"""

import logging

logger = logging.getLogger("ai_broadcast")

_REWRITE_SYSTEM = (
    "You are a professional emergency broadcast radio announcer for FPREN, "
    "the Florida Public Radio Emergency Network. "
    "Rewrite the provided NWS alert text as a concise, clear, spoken radio script. "
    "Rules:\n"
    "- Write for the ear, not the eye — spell out abbreviations, use plain language.\n"
    "- Start directly with the alert (no 'Certainly!' or preamble).\n"
    "- Keep it under 60 seconds of spoken audio (~120 words).\n"
    "- Do not add information that is not in the source text.\n"
    "- End with a clear call-to-action appropriate for the alert type.\n"
    "- Output plain text only — no markdown, no bullet points."
)

_BROADCAST_SYSTEM = (
    "You are a professional weather radio announcer for FPREN, "
    "the Florida Public Radio Emergency Network, covering Gainesville and North Florida. "
    "Generate a concise, spoken broadcast summary from the provided weather and alert data. "
    "Rules:\n"
    "- Write for the ear — spell out abbreviations, use plain language.\n"
    "- Lead with any active alerts, then current conditions, then a brief outlook.\n"
    "- Keep the total under 90 seconds of audio (~180 words).\n"
    "- Output plain text only — no markdown, no bullet points."
)


def rewrite_alert(headline: str, area: str, description: str) -> str:
    """Rewrite NWS alert text into a broadcast-ready radio script.

    Returns the rewritten script, or the original assembled text on failure.
    """
    raw = _assemble_alert_text(headline, area, description)
    try:
        from services.ai_client import chat, is_configured
        if not is_configured():
            return raw
        prompt = (
            f"NWS Alert:\nHeadline: {headline}\nAffected areas: {area}\n"
            f"Description: {description[:800]}"
        )
        result = chat(prompt, system=_REWRITE_SYSTEM, max_tokens=300)
        logger.info("AI rewrote alert: %d chars → %d chars", len(raw), len(result))
        return result
    except Exception as exc:
        logger.warning("AI rewrite failed, using raw text: %s", exc)
        return raw


def generate_broadcast(weather_data: list, alerts: list) -> str:
    """Generate a full weather broadcast script from current data.

    weather_data: list of dicts with keys icaoId, temp_f, wspd, visib, rawOb
    alerts:       list of dicts with keys event, headline, area_desc, severity

    Returns a broadcast script string, or empty string on failure.
    """
    try:
        from services.ai_client import chat, is_configured
        if not is_configured():
            return ""

        alert_lines = "\n".join(
            f"- [{a.get('severity','').upper()}] {a.get('event','')}: {a.get('headline','')}"
            for a in alerts[:5]
        ) or "None"

        obs_lines = "\n".join(
            f"- {w.get('icaoId','')}: {w.get('temp_f','')}°F, wind {w.get('wspd','')}kt, "
            f"visibility {w.get('visib','')}sm"
            for w in weather_data[:5]
        ) or "No observations available"

        prompt = (
            f"Active NWS Alerts:\n{alert_lines}\n\n"
            f"Current Observations:\n{obs_lines}"
        )
        result = chat(prompt, system=_BROADCAST_SYSTEM, max_tokens=400)
        logger.info("AI generated broadcast script (%d chars)", len(result))
        return result
    except Exception as exc:
        logger.warning("AI broadcast generation failed: %s", exc)
        return ""


def _assemble_alert_text(headline: str, area: str, description: str) -> str:
    text = f"{headline}."
    if area:
        text += f" Affected areas: {area}."
    if description:
        short = description[:500].rsplit(".", 1)[0] + "."
        text += f" {short}"
    return text
