"""
main.py — FastAPI server for SyncMe.

Exposes two webhook endpoints consumed by the Meta / Instagram platform:

  GET  /webhook  → Verification challenge (one-time setup).
  POST /webhook  → Receives incoming DM events, returns 200 immediately,
                   then processes the reel in the background.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import re
import time
import sys
import tempfile
from contextlib import asynccontextmanager
from typing import Any

import sentry_sdk
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

load_dotenv()

from ai_processor import summarise_reel
from database import save_reel_summary
from messenger import send_ig_message
from scraper import download_reel

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("syncme")

# ── Config ──────────────────────────────────────────────────────────
META_VERIFY_TOKEN: str = os.environ["META_VERIFY_TOKEN"]
META_APP_SECRET: str | None = os.getenv("META_APP_SECRET")

# Regex that matches Instagram reel / post URLs.
_IG_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|p|reels|tv)/[\w-]+",
    re.IGNORECASE,
)


# ── Sentry — global error tracking ──────────────────────────────────
sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    traces_sample_rate=1.0,
)

# ── In-memory rate limiting ─────────────────────────────────────────
# Maps user_id → list of request timestamps (sliding window).
USER_COOLDOWNS: dict[str, list[float]] = {}

_RATE_LIMIT_MAX_REQUESTS: int = 2
_RATE_LIMIT_WINDOW_SECONDS: float = 60.0


def is_rate_limited(user_id: str) -> bool:
    """Return ``True`` if *user_id* has exceeded 2 requests in the last 60 s.

    Stale timestamps older than the window are pruned on every call to
    prevent unbounded memory growth.
    """
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS

    # Retrieve (or create) the timestamp list for this user.
    timestamps = USER_COOLDOWNS.get(user_id, [])

    # Prune stale entries that have fallen outside the window.
    timestamps = [t for t in timestamps if t > cutoff]

    if len(timestamps) >= _RATE_LIMIT_MAX_REQUESTS:
        # Still over the limit after cleanup — reject.
        USER_COOLDOWNS[user_id] = timestamps
        return True

    # Under the limit — record this request.
    timestamps.append(now)
    USER_COOLDOWNS[user_id] = timestamps
    return False


# ── App ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ANN001, ARG001
    logger.info("SyncMe is starting up 🚀")

    # ── Materialise Instagram cookies from Base64 env var ───────────
    # In ephemeral deploy environments (Railway, Fly, etc.) there is no
    # persistent filesystem.  We store the Netscape cookie data as a
    # Base64-encoded string in IG_COOKIE_BASE64 and decode it to disk
    # at startup so yt-dlp can read it via IG_COOKIES_PATH.
    ig_cookie_b64 = os.environ.get("IG_COOKIE_BASE64")
    cookies_dest = os.environ.get("IG_COOKIES_PATH", "/tmp/ig_cookies.txt")

    if ig_cookie_b64:
        try:
            cookie_bytes = base64.b64decode(ig_cookie_b64)
            with open(cookies_dest, "wb") as f:
                f.write(cookie_bytes)
            logger.info(
                "Instagram cookie file written to %s (%d bytes)",
                cookies_dest,
                len(cookie_bytes),
            )
        except Exception:
            logger.exception(
                "Failed to decode IG_COOKIE_BASE64 — yt-dlp will likely "
                "fail with 'login required'."
            )
    else:
        logger.warning(
            "IG_COOKIE_BASE64 is not set — yt-dlp may fail to download "
            "reels that require authentication."
        )

    yield
    logger.info("SyncMe is shutting down 🛑")


app = FastAPI(
    title="SyncMe",
    summary="Async Instagram DM bot — downloads reels, summarises them with "
    "Gemini, and stores the results in Supabase.",
    version="1.0.0",
    lifespan=_lifespan,
)


# ── Webhook signature verification ──────────────────────────────────
def _verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Validate the X-Hub-Signature-256 header sent by Meta.

    If ``META_APP_SECRET`` is not configured the check is skipped (useful
    during local development).
    """
    if not META_APP_SECRET:
        logger.warning(
            "META_APP_SECRET is not set — skipping signature verification. "
            "Set it in production!"
        )
        return True

    if not signature_header:
        return False

    # Header format: "sha256=<hex_digest>"
    parts = signature_header.split("=", 1)
    if len(parts) != 2 or parts[0] != "sha256":
        return False

    expected = hmac.new(
        META_APP_SECRET.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, parts[1])


# ── GET /webhook — Meta verification challenge ─────────────────────
@app.get("/webhook", summary="Meta webhook verification")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    """Respond to Meta's one-time verification GET request.

    Meta sends ``hub.mode=subscribe``, ``hub.verify_token=<your token>``,
    and ``hub.challenge=<random string>``.  We echo back the challenge
    if the token matches.
    """
    if hub_mode == "subscribe" and hub_verify_token == META_VERIFY_TOKEN:
        logger.info("Webhook verified successfully ✅")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(
        "Webhook verification failed — mode=%s token=%s",
        hub_mode,
        hub_verify_token,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch.",
    )


# ── POST /webhook — Incoming DM events ─────────────────────────────
@app.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Receive Instagram DM events",
)
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    """Accept incoming Instagram messaging webhook events.

    Returns ``200 OK`` immediately to satisfy Meta's timeout window,
    then spawns a background task to process any reel URLs found in the
    messages.
    """
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        logger.warning("Invalid webhook signature — rejecting request.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signature.",
        )

    payload: dict[str, Any] = await request.json()
    logger.info("Webhook payload received: %s", _truncate(payload))

    # Parse every messaging event in the payload.
    for entry in payload.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            sender_id: str | None = messaging_event.get("sender", {}).get("id")
            message_body: dict[str, Any] = messaging_event.get("message", {})
            message_text: str = message_body.get("text", "")

            if not sender_id:
                continue

            # Check if the message (or any attachment) contains an IG reel URL.
            reel_url = _extract_reel_url(message_text, message_body)

            if reel_url:
                logger.info(
                    "Reel URL detected from user=%s — scheduling processing.",
                    sender_id,
                )

                # ── Rate-limit gate ──────────────────────────────────
                if is_rate_limited(sender_id):
                    logger.warning(
                        "Rate limit hit for user=%s — rejecting.",
                        sender_id,
                    )
                    background_tasks.add_task(
                        send_ig_message,
                        sender_id,
                        {
                            "thesis": "⏳ Rate limit exceeded.",
                            "key_points": [
                                "You can only process 2 Reels per minute to prevent spam.",
                                "Please wait 60 seconds and try again.",
                            ],
                            "tags": ["ratelimit"],
                        },
                    )
                    continue  # Skip to next messaging event.

                # ── Immediate UX acknowledgment (non-blocking) ──────
                background_tasks.add_task(
                    send_ig_message,
                    sender_id,
                    {
                        "thesis": "🤖 Analyzing your Reel...",
                        "key_points": [
                            "Downloading video.",
                            "Transcribing audio.",
                            "Generating insights.",
                        ],
                        "tags": ["processing"],
                    },
                )

                # _process_reel is now a coroutine — BackgroundTasks natively
                # awaits coroutines on the running event loop, so no thread
                # overhead and no loop-blocking.
                background_tasks.add_task(
                    _process_reel, sender_id, reel_url
                )
            else:
                logger.debug(
                    "No reel URL in message from user=%s — ignoring.",
                    sender_id,
                )

    return {"status": "ok"}


# ── Helpers ─────────────────────────────────────────────────────────
def _extract_reel_url(
    text: str, message_body: dict[str, Any]
) -> str | None:
    """Try to find an Instagram reel URL in the message text or attachments.

    Returns the first match or ``None``.
    """
    # 1. Check plain text
    match = _IG_URL_RE.search(text)
    if match:
        return match.group(0)

    # 2. Check share / attachment payloads — IG sometimes wraps shared
    #    reels in an attachment with a url field.
    for attachment in message_body.get("attachments", []):
        url: str = attachment.get("payload", {}).get("url", "")
        match = _IG_URL_RE.search(url)
        if match:
            return match.group(0)

    return None


def _truncate(obj: Any, length: int = 500) -> str:
    """Produce a truncated string repr for logging."""
    s = str(obj)
    return s[:length] + "…" if len(s) > length else s


# ── Background pipeline ────────────────────────────────────────────
async def _process_reel(user_id: str, reel_url: str) -> None:
    """Execute the full scrape → summarise → store pipeline.

    Now fully async.  Uses ``tempfile.TemporaryDirectory`` as a
    bulletproof cleanup mechanism — the directory and all files inside
    it are wiped when the context manager exits, whether the pipeline
    succeeds, raises, or the task is cancelled.

    Each stage is wrapped in its own try/except so a failure at any
    point is logged but never crashes the server.
    """
    # tempfile.TemporaryDirectory guarantees that the .mp4 (and any other
    # scratch files) are purged from disk even if the pipeline crashes.
    # We create it on a thread because TemporaryDirectory.__init__ can
    # trigger synchronous I/O on some OSes.
    tmp_dir = await asyncio.to_thread(
        tempfile.TemporaryDirectory, prefix="syncme_"
    )

    async with _async_tempdir(tmp_dir) as dir_path:
        # ── Stage 1: Download ───────────────────────────────────────
        try:
            logger.info("[%s] Stage 1/4 — Downloading reel: %s", user_id, reel_url)
            file_path = await download_reel(reel_url, dir_path)
            logger.info("[%s] Download complete: %s", user_id, file_path)
        except Exception:
            logger.exception(
                "[%s] ❌ Stage 1 FAILED — could not download reel: %s",
                user_id,
                reel_url,
            )
            return  # Nothing else to do without the video file.

        # ── Stage 2: Summarise ──────────────────────────────────────
        try:
            logger.info("[%s] Stage 2/4 — Summarising with Groq…", user_id)
            summary = await summarise_reel(file_path)
            logger.info("[%s] Summary generated: %s", user_id, summary)
        except Exception:
            logger.exception(
                "[%s] ❌ Stage 2 FAILED — summarisation error for: %s",
                user_id,
                reel_url,
            )
            return

        # ── Stage 3: Persist ────────────────────────────────────────
        try:
            logger.info("[%s] Stage 3/4 — Saving to Supabase…", user_id)
            row = await save_reel_summary(
                user_id=user_id,
                original_url=reel_url,
                ai_summary=summary,
            )
            logger.info("[%s] Supabase row id=%s saved.", user_id, row.get("id"))
        except Exception:
            logger.exception(
                "[%s] ❌ Stage 3 FAILED — Supabase insert error for: %s",
                user_id,
                reel_url,
            )

        # ── Stage 4: Reply to user ──────────────────────────────────
        try:
            logger.info("[%s] Stage 4/4 — Sending DM reply…", user_id)
            await send_ig_message(user_id, summary)
            logger.info("[%s] ✅ Pipeline complete.", user_id)
        except Exception:
            logger.exception(
                "[%s] ❌ Stage 4 FAILED — could not send DM for: %s",
                user_id,
                reel_url,
            )

    # TemporaryDirectory context manager has exited — all temp files are
    # guaranteed to be deleted from disk at this point.


@asynccontextmanager
async def _async_tempdir(tmp_dir: tempfile.TemporaryDirectory):
    """Async context manager wrapper around ``tempfile.TemporaryDirectory``.

    ``TemporaryDirectory.cleanup()`` involves synchronous filesystem
    calls, so we offload it to a thread to keep the event loop clean.
    """
    try:
        yield tmp_dir.name
    finally:
        # Offload synchronous rmtree-based cleanup to a thread so we
        # don't block the event loop during recursive directory deletion.
        await asyncio.to_thread(tmp_dir.cleanup)
