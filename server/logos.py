"""Bounded raster logo uploads; no filenames or executable SVGs are stored."""

from __future__ import annotations

MAX_LOGO_BYTES = 5 * 1024 * 1024


def logo_media_type(content: bytes) -> str | None:
    """Identify supported raster formats from their headers, not upload metadata.

    Images are served unchanged and decoded only by the browser, never on the host.
    """
    if len(content) >= 33 and content.startswith(b"\x89PNG\r\n\x1a\n") and content[12:16] == b"IHDR":
        return "image/png"
    if len(content) >= 4 and content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if (len(content) >= 20 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
            and content[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
            and int.from_bytes(content[4:8], "little") + 8 == len(content)):
        return "image/webp"
    return None
