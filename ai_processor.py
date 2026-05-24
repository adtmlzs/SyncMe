"""
ai_processor.py — Groq-powered video summarisation for SyncMe.

Two-stage pipeline:
  1. Transcribe the .mp4 audio track via Groq's Whisper API.
  2. Summarise the transcript via Groq's Llama model.

Returns the same strictly-typed ReelSummary dict the rest of the
pipeline expects.  Fully async — safe to ``await`` from any FastAPI
handler or background coroutine.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Final, TypedDict

from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

logger = logging.getLogger(__name__)

_GROQ_API_KEY: Final[str] = os.environ["GROQ_API_KEY"]
_WHISPER_MODEL: Final[str] = "whisper-large-v3"
_LLM_MODEL: Final[str] = "llama-3.3-70b-versatile"

# AsyncGroq client — natively async, no thread offloading needed.
_client = AsyncGroq(api_key=_GROQ_API_KEY)

# ── Strict return type ──────────────────────────────────────────────
class ReelSummary(TypedDict):
    """Contract for the structured summary returned by the AI processor."""

    thesis: str
    key_points: list[str]
    tags: list[str]


# ── Prompts ─────────────────────────────────────────────────────────
_SYSTEM_INSTRUCTION: Final[str] = (
    "You are a concise content analyst. You receive transcripts of "
    "short-form video content (Instagram Reels). Your job is to produce "
    "a structured summary. Always respond with valid JSON and nothing else."
)

_USER_PROMPT_TEMPLATE: Final[str] = """\
Analyse the following transcript from an Instagram Reel and return a JSON \
object with exactly these keys:

{{
  "thesis": "<one-sentence summary of the reel's main message>",
  "key_points": ["<actionable point 1>", "<actionable point 2>", ...],
  "tags": ["<conceptual tag 1>", "<conceptual tag 2>", ...]
}}

Rules:
- "thesis" must be a single sentence (≤ 30 words).
- "key_points" must contain 3-7 concrete, actionable bullet points.
- "tags" must contain 3-10 lowercase conceptual tags (no hashtag symbol).
- Output ONLY the JSON object — no markdown fences, no commentary.

TRANSCRIPT:
{transcript}
"""


# ── Stage 1: Transcription ─────────────────────────────────────────

async def _transcribe_video(file_path: str) -> str:
    """Transcribe the audio track of a video using Groq Whisper.

    Groq's Whisper API accepts .mp4 files directly (up to 25 MB) and
    extracts the audio server-side — no local ffmpeg needed.

    Parameters
    ----------
    file_path:
        Absolute path to the local ``.mp4`` file.

    Returns
    -------
    str
        The full transcript text.
    """
    logger.info("Transcribing audio from %s via Groq Whisper…", file_path)

    with open(file_path, "rb") as f:
        transcription = await _client.audio.transcriptions.create(
            file=(os.path.basename(file_path), f),
            model=_WHISPER_MODEL,
            response_format="text",
        )

    text = transcription.strip() if isinstance(transcription, str) else str(transcription).strip()
    logger.info("Transcription complete — %d characters.", len(text))
    return text


# ── Helpers ─────────────────────────────────────────────────────────

def _clean_json_response(raw: str) -> str:
    """Strip optional markdown fences the model might emit despite instructions."""
    text = raw.strip()
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


# ── Public API ──────────────────────────────────────────────────────

async def summarise_reel(file_path: str) -> ReelSummary:
    """Transcribe and summarise a reel video.

    Fully async — both the Whisper transcription and the Llama
    summarisation use the ``AsyncGroq`` client so the event loop
    is never blocked.

    Parameters
    ----------
    file_path:
        Absolute local path to the ``.mp4`` file to analyse.

    Returns
    -------
    ReelSummary
        A ``TypedDict`` with keys ``thesis``, ``key_points``, and ``tags``.

    Raises
    ------
    RuntimeError
        If transcription fails or returns empty audio.
    ValueError
        If the model returns unparseable or structurally invalid JSON.
    """
    # ── Step 1: Transcribe audio ────────────────────────────────────
    transcript = await _transcribe_video(file_path)

    if not transcript:
        raise RuntimeError(
            "Whisper returned an empty transcript — the video may have "
            "no audible speech."
        )

    # ── Step 2: Summarise transcript with Llama ─────────────────────
    logger.info("Requesting summary from %s…", _LLM_MODEL)

    user_prompt = _USER_PROMPT_TEMPLATE.format(transcript=transcript)

    chat_completion = await _client.chat.completions.create(
        model=_LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=1024,
    )

    raw_text = chat_completion.choices[0].message.content
    logger.debug("Raw model response:\n%s", raw_text)

    # ── Parse & validate ────────────────────────────────────────────
    cleaned = _clean_json_response(raw_text)

    try:
        parsed: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Model returned invalid JSON: %s", cleaned[:500])
        raise ValueError(
            f"LLM returned non-JSON output: {cleaned[:200]}"
        ) from exc

    # Validate required keys
    missing = {"thesis", "key_points", "tags"} - parsed.keys()
    if missing:
        raise ValueError(
            f"Model response is missing required keys: {missing}"
        )

    # Coerce into the strict type
    summary: ReelSummary = {
        "thesis": str(parsed["thesis"]),
        "key_points": [str(p) for p in parsed["key_points"]],
        "tags": [str(t).lower() for t in parsed["tags"]],
    }

    logger.info(
        "Summary generated — thesis length=%d, points=%d, tags=%d",
        len(summary["thesis"]),
        len(summary["key_points"]),
        len(summary["tags"]),
    )

    return summary
