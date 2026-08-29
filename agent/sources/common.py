"""Shared helpers for source fetch/normalize modules."""

from __future__ import annotations

import html
import re

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"[ \t]+")


def html_to_text(raw_html: str | None) -> str:
    """Unescape HTML entities and strip tags to plain text.

    Greenhouse's `content` field is HTML-escaped; Ashby and
    Lever sometimes only provide an HTML description too. Good enough for
    Tier 1 keyword matching and LLM context — not meant to preserve layout.
    """
    if not raw_html:
        return ""
    unescaped = html.unescape(raw_html)
    text = _TAG_PATTERN.sub(" ", unescaped)
    text = _WHITESPACE_PATTERN.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def looks_remote(*texts: str | None) -> bool:
    """True if any of the given text fields mention "remote"."""
    return any(text and "remote" in text.lower() for text in texts)
