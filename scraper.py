"""
scraper.py — Instagram reel downloader via yt-dlp.

Uses yt-dlp to extract and download reel videos locally.  No third-party
REST APIs are involved — all extraction is handled by the yt-dlp library.

The public ``download_reel`` coroutine offloads the synchronous yt-dlp
call to a worker thread via ``asyncio.to_thread`` so the FastAPI event
loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Final

import yt_dlp

logger = logging.getLogger(__name__)

# ── yt-dlp configuration constants ─────────────────────────────────
_FORMAT: Final[str] = "mp4"
_REQUEST_TIMEOUT: Final[int] = 30  # seconds per network operation


def _build_ytdlp_opts(dest_dir: str) -> dict:
    """Return the yt-dlp option dict that writes a single .mp4 into *dest_dir*.

    The output template is fixed to ``reel.mp4`` so the caller always
    knows the exact path without globbing.
    """
    return {
        # Save as <dest_dir>/reel.mp4
        "outtmpl": os.path.join(dest_dir, "reel.%(ext)s"),
        # Select the best pre-merged mp4 stream — avoids requiring ffmpeg
        # for muxing separate video+audio tracks.  The "b" (best) filter
        # picks the highest quality single-file format available.
        "format": "best[ext=mp4]/best",
        # Silence yt-dlp's own console output — we log through Python.
        "quiet": True,
        "no_warnings": True,
        # Network resilience
        "socket_timeout": _REQUEST_TIMEOUT,
        "retries": 3,
        "fragment_retries": 3,
        # Don't write metadata side-files — we only need the video.
        "writeinfojson": False,
        "writethumbnail": False,
        "writesubtitles": False,
    }


def _sync_download(reel_url: str, dest_dir: str) -> str:
    """Synchronous helper — runs the yt-dlp extraction and download.

    This function is intentionally synchronous because yt-dlp is a
    blocking library with no native async support.  It is called from
    the async layer via ``asyncio.to_thread``.

    Parameters
    ----------
    reel_url:
        Full Instagram reel URL.
    dest_dir:
        Directory to write the downloaded .mp4 into.

    Returns
    -------
    str
        Absolute path to the downloaded ``.mp4`` file.

    Raises
    ------
    RuntimeError
        If yt-dlp fails to extract or download the video.
    """
    opts = _build_ytdlp_opts(dest_dir)

    logger.info("Starting yt-dlp extraction for: %s", reel_url)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(reel_url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(
            f"yt-dlp failed to download reel: {exc}"
        ) from exc

    if info is None:
        raise RuntimeError(
            f"yt-dlp returned no info for URL: {reel_url}"
        )

    # yt-dlp may merge streams into the final path — resolve it from
    # the info dict so we get the actual filename on disk.
    file_path: str = ydl.prepare_filename(info)

    # If yt-dlp merged into mp4, the extension is already correct.
    # Guard against edge cases where prepare_filename returns a
    # pre-merge extension (e.g. .webm) but the merged file is .mp4.
    base, ext = os.path.splitext(file_path)
    if ext != f".{_FORMAT}":
        merged_path = f"{base}.{_FORMAT}"
        if os.path.isfile(merged_path):
            file_path = merged_path

    if not os.path.isfile(file_path):
        raise RuntimeError(
            f"yt-dlp reported success but file not found: {file_path}"
        )

    size_bytes = os.path.getsize(file_path)
    logger.info("Downloaded %d bytes to %s", size_bytes, file_path)

    return file_path


# ── Public async API ────────────────────────────────────────────────

async def download_reel(reel_url: str, dest_dir: str) -> str:
    """Download an Instagram reel to *dest_dir* as a .mp4 file.

    Offloads the blocking yt-dlp call to a worker thread via
    ``asyncio.to_thread`` so the FastAPI event loop stays responsive
    for concurrent webhook processing.

    Parameters
    ----------
    reel_url:
        Full Instagram reel URL (e.g.
        ``https://www.instagram.com/reel/ABC123/``).
    dest_dir:
        Absolute path to the directory where the .mp4 should be written.
        The caller owns this directory's lifecycle (typically via
        ``tempfile.TemporaryDirectory``).

    Returns
    -------
    str
        Absolute path to the downloaded ``.mp4`` file on disk.

    Raises
    ------
    RuntimeError
        If yt-dlp cannot extract or download the video.
    """
    # asyncio.to_thread runs _sync_download in the default executor,
    # freeing the event loop to handle other incoming webhooks while
    # yt-dlp blocks on network I/O and ffmpeg merging.
    return await asyncio.to_thread(_sync_download, reel_url, dest_dir)
