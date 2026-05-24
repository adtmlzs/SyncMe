"""
database.py — Supabase persistence layer for SyncMe.

Inserts processed reel data (user_id, original_url, ai_summary) into the
`reel_summaries` Postgres table via Supabase's PostgREST API.

Uses ``httpx.AsyncClient`` for non-blocking inserts and implements
exponential-backoff retry (max 3 attempts) for transient failures.

Expected table DDL (run once in the Supabase SQL editor):

    CREATE TABLE IF NOT EXISTS reel_summaries (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id     TEXT        NOT NULL,
        original_url TEXT       NOT NULL,
        ai_summary  JSONB       NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX idx_reel_summaries_user ON reel_summaries (user_id);
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Final

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_SUPABASE_URL: str = os.environ["SUPABASE_URL"]
_SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]
_TABLE_NAME: str = "reel_summaries"

# PostgREST endpoint for the target table.
_INSERT_URL: str = f"{_SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE_NAME}"

# Common headers required by every Supabase REST call.
_HEADERS: dict[str, str] = {
    "apikey": _SUPABASE_KEY,
    "Authorization": f"Bearer {_SUPABASE_KEY}",
    "Content-Type": "application/json",
    # Ask PostgREST to return the inserted row as JSON.
    "Prefer": "return=representation",
}

_TIMEOUT: Final[int] = 15  # seconds

# ── Retry configuration ────────────────────────────────────────────
_MAX_RETRIES: Final[int] = 3
_INITIAL_BACKOFF: Final[float] = 1.0  # seconds; doubles each attempt


async def save_reel_summary(
    user_id: str,
    original_url: str,
    ai_summary: dict[str, Any],
) -> dict[str, Any]:
    """Persist a processed reel summary to Supabase with retry.

    Uses ``httpx.AsyncClient`` so the call is fully non-blocking.
    Transient failures (5xx, timeouts, network errors) are retried up to
    ``_MAX_RETRIES`` times with exponential backoff.  Client errors
    (4xx) are raised immediately.

    Parameters
    ----------
    user_id:
        Instagram-scoped user ID of the sender.
    original_url:
        The original Instagram reel URL that was shared.
    ai_summary:
        Structured JSON dict returned by the AI processor (thesis, key
        points, tags).

    Returns
    -------
    dict
        The full row as returned by Supabase after insertion, including
        server-generated fields like ``id`` and ``created_at``.

    Raises
    ------
    RuntimeError
        If the Supabase insert fails after all retry attempts.
    """
    payload = {
        "user_id": user_id,
        "original_url": original_url,
        "ai_summary": ai_summary,
    }

    logger.info(
        "Inserting reel summary for user=%s url=%s",
        user_id,
        original_url,
    )

    last_exc: Exception | None = None
    backoff = _INITIAL_BACKOFF

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.post(
                    _INSERT_URL,
                    headers=_HEADERS,
                    content=json.dumps(payload),
                )
                response.raise_for_status()
                # Success — break out of the retry loop.
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code

                # Client errors (4xx) are not transient — raise immediately
                # instead of wasting retry budget.
                if 400 <= status_code < 500:
                    logger.exception(
                        "Supabase insert returned HTTP %d for user=%s: %s",
                        status_code,
                        user_id,
                        exc.response.text[:500],
                    )
                    raise RuntimeError(
                        f"Supabase insert failed (HTTP {status_code}): "
                        f"{exc.response.text[:200]}"
                    ) from exc

                # 5xx — transient; log and retry.
                logger.warning(
                    "Supabase insert HTTP %d on attempt %d/%d for user=%s",
                    status_code,
                    attempt,
                    _MAX_RETRIES,
                    user_id,
                )
            except httpx.HTTPError as exc:
                # Network-level failure (timeout, DNS, connection reset).
                last_exc = exc
                logger.warning(
                    "Supabase network error on attempt %d/%d for user=%s: %s",
                    attempt,
                    _MAX_RETRIES,
                    user_id,
                    exc,
                )

            if attempt < _MAX_RETRIES:
                logger.info(
                    "Retrying Supabase insert in %.1fs (attempt %d/%d)…",
                    backoff,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                # Non-blocking backoff — yields control to the event loop.
                await asyncio.sleep(backoff)
                backoff *= 2  # Exponential backoff: 1s → 2s → 4s
        else:
            # All retries exhausted.
            logger.exception(
                "Supabase insert failed after %d attempts for user=%s",
                _MAX_RETRIES,
                user_id,
            )
            raise RuntimeError(
                f"Supabase insert failed after {_MAX_RETRIES} attempts"
            ) from last_exc

    rows = response.json()

    if not rows:
        logger.error(
            "Supabase returned empty data after insert for user=%s", user_id
        )
        raise RuntimeError(
            "Supabase insert returned no data — check table schema and RLS policies."
        )

    inserted_row: dict[str, Any] = rows[0]
    logger.info(
        "Saved reel summary id=%s for user=%s",
        inserted_row.get("id"),
        user_id,
    )
    return inserted_row
