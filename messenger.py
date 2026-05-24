"""
messenger.py — Instagram DM reply layer for SyncMe.

Sends the AI-generated summary back to the user as an Instagram DM via
the Meta Graph API.  Uses ``httpx.AsyncClient`` for non-blocking HTTP.

A messaging failure is logged as a warning but never raises — the
background pipeline must not crash because a reply couldn't be delivered.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_META_ACCESS_TOKEN: Final[str] = os.environ["META_ACCESS_TOKEN"]
_GRAPH_API_URL: Final[str] = (
    "https://graph.facebook.com/v18.0/me/messages"
)
_TIMEOUT: Final[int] = 15  # seconds


def _format_summary(summary: dict[str, Any]) -> str:
    """Format a ReelSummary dict into a human-readable DM message.

    Layout:
        🎯 <thesis>

        📌 Key Points:
        • point 1
        • point 2
        ...

        🏷️ #tag1 #tag2 #tag3
    """
    thesis = summary.get("thesis", "")
    key_points = summary.get("key_points", [])
    tags = summary.get("tags", [])

    lines: list[str] = []

    # Header — thesis
    lines.append(f"🎯 {thesis}")
    lines.append("")

    # Bullet points
    if key_points:
        lines.append("📌 Key Points:")
        for point in key_points:
            lines.append(f"  • {point}")
        lines.append("")

    # Tags
    if tags:
        tag_str = " ".join(f"#{t}" for t in tags)
        lines.append(f"🏷️ {tag_str}")

    return "\n".join(lines)


async def send_ig_message(
    recipient_id: str,
    summary: dict[str, Any],
) -> None:
    """Send the formatted AI summary back to the user via Instagram DM.

    Uses the Meta Graph API ``/me/messages`` endpoint.  A non-200
    response is logged as a warning but does **not** raise — this keeps
    the background pipeline resilient to transient messaging failures.

    Parameters
    ----------
    recipient_id:
        Instagram-scoped user ID to reply to.
    summary:
        The ReelSummary dict (thesis, key_points, tags).
    """
    formatted_text = _format_summary(summary)

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": formatted_text},
    }

    headers = {
        "Authorization": f"Bearer {_META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _GRAPH_API_URL,
                json=payload,
                headers=headers,
            )

        if resp.status_code == 200:
            logger.info(
                "✉️  DM sent to user=%s (status=%d)",
                recipient_id,
                resp.status_code,
            )
        else:
            # Log the failure but do NOT raise — the pipeline should
            # continue even if the reply can't be delivered.
            logger.warning(
                "⚠️  DM to user=%s returned HTTP %d: %s",
                recipient_id,
                resp.status_code,
                resp.text[:500],
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "⚠️  DM to user=%s failed with network error: %s",
            recipient_id,
            exc,
        )
