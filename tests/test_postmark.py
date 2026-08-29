"""Unit tests for Postmark delivery. httpx.MockTransport only — never a real
send."""

import json

import httpx
import pytest

from agent.delivery.postmark import send_digest


async def test_send_digest_posts_expected_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"MessageID": "abc-123", "ErrorCode": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await send_digest(
            client,
            server_token="fake-token",
            from_email="from@example.com",
            to_email="to@example.com",
            subject="Job Digest",
            html_body="<html><body>hi</body></html>",
        )

    assert captured["url"] == "https://api.postmarkapp.com/email"
    assert captured["headers"]["x-postmark-server-token"] == "fake-token"
    assert captured["json"]["From"] == "from@example.com"
    assert captured["json"]["To"] == "to@example.com"
    assert captured["json"]["Subject"] == "Job Digest"
    assert captured["json"]["HtmlBody"] == "<html><body>hi</body></html>"
    assert result["MessageID"] == "abc-123"


async def test_send_digest_raises_on_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"ErrorCode": 300, "Message": "Invalid email"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await send_digest(
                client,
                server_token="fake-token",
                from_email="from@example.com",
                to_email="to@example.com",
                subject="Job Digest",
                html_body="<html></html>",
            )
