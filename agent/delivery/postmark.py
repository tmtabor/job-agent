"""Postmark email delivery.

Raises on a non-2xx response — retried/skipped uniformly by the
orchestrator's retry wrapper (agent/pipeline/retry.py), not internally here,
same convention as the source fetchers in agent/sources/.
"""

from __future__ import annotations

import httpx

POSTMARK_URL = "https://api.postmarkapp.com/email"


async def send_digest(
    client: httpx.AsyncClient,
    server_token: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
) -> dict:
    """Send the digest email via Postmark's single-email API."""
    response = await client.post(
        POSTMARK_URL,
        headers={
            "X-Postmark-Server-Token": server_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "From": from_email,
            "To": to_email,
            "Subject": subject,
            "HtmlBody": html_body,
            "MessageStream": "outbound",
        },
    )
    response.raise_for_status()
    return response.json()
