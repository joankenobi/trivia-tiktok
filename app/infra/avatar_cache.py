"""Avatar capture.

TikTok CDN avatar URLs are signed and short-lived: measured samples carried
`x-expires=1790629200` (already past) and a direct fetch returned 403. So the
bytes must be captured while the live is running, and the signed URL is never
persisted. On any failure the caller gets None and the UI falls back to initials.

An avatar must never break vote ingestion, hence the blanket except.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ..config import settings

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_EXT_BY_TYPE = {
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
}


def _safe_name(unique_id: str) -> str:
    keep = [c if (c.isalnum() or c in "-_.") else "_" for c in unique_id]
    return ("".join(keep) or "unknown")[:64]


async def cache_avatar(
    unique_id: str,
    url: str | None,
    out_dir: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Download an avatar to web/avatars and return its served path, or None.

    Idempotent: an already-captured file short-circuits the network call.
    """
    if not unique_id or not url:
        return None

    out_dir = out_dir or settings.avatars_dir
    name = _safe_name(unique_id)

    existing = next(
        (p for p in (out_dir / f"{name}{ext}" for ext in (".webp", ".jpg", ".png")) if p.exists()),
        None,
    )
    if existing is not None:
        return f"/avatars/{existing.name}"

    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=settings.avatar_timeout_s, follow_redirects=True
    )
    try:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        resp = await client.get(url, timeout=settings.avatar_timeout_s, headers={"User-Agent": _UA})
        if resp.status_code != 200 or not resp.content:
            return None

        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = _EXT_BY_TYPE.get(ctype) or _magic_ext(resp.content)
        if ext is None:
            return None

        path = out_dir / f"{name}{ext}"
        path.write_bytes(resp.content)
        return f"/avatars/{path.name}"
    except Exception:
        return None
    finally:
        if owns_client:
            await client.aclose()


def _magic_ext(content: bytes) -> str | None:
    """CDN responses often omit or mislabel content-type; sniff instead."""
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if content[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None
