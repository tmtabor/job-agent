"""Unit tests for company self-expansion.

httpx.MockTransport only — never a real probe.
"""

import httpx

from agent.pipeline.self_expansion import candidate_slugs, probe_company_board


def test_candidate_slugs_strips_spaces_and_punctuation():
    assert candidate_slugs("Anthropic") == ["anthropic"]
    assert candidate_slugs("ClickUp") == ["clickup"]


def test_candidate_slugs_includes_hyphenated_variant():
    slugs = candidate_slugs("Inflection AI")
    assert "inflectionai" in slugs
    assert "inflection-ai" in slugs


def test_candidate_slugs_deduplicates_when_variants_match():
    # A single-word name has no distinct hyphenated form
    assert candidate_slugs("Anthropic") == ["anthropic"]


def _handler(greenhouse_response=None, lever_response=None, ashby_response=None):
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "boards-api.greenhouse.io":
            if greenhouse_response is None:
                return httpx.Response(404)
            return httpx.Response(200, json=greenhouse_response)
        if host == "api.lever.co":
            if lever_response is None:
                return httpx.Response(404)
            return httpx.Response(200, json=lever_response)
        if host == "api.ashbyhq.com":
            if ashby_response is None:
                return httpx.Response(404)
            return httpx.Response(200, json=ashby_response)
        raise AssertionError(f"unexpected host: {host}")

    return handler


async def test_probe_finds_confirmed_greenhouse_board():
    handler = _handler(
        greenhouse_response={"jobs": [{"id": 1, "title": "Engineer", "company_name": "Anthropic"}]}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company_board(client, "Anthropic")

    assert result == ("greenhouse", "anthropic")


async def test_probe_rejects_greenhouse_board_with_mismatched_company_name():
    """A slug collision — a board exists at this slug, but for a different
    company than the one we're trying to confirm. Must not be accepted.
    """
    handler = _handler(
        greenhouse_response={
            "jobs": [{"id": 1, "title": "Engineer", "company_name": "Some Other Company"}]
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company_board(client, "Anthropic")

    assert result is None


async def test_probe_finds_lever_board_when_greenhouse_has_no_board():
    handler = _handler(lever_response=[{"id": "abc", "text": "Engineer"}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company_board(client, "SomeCo")

    assert result == ("lever", "someco")


async def test_probe_finds_ashby_board_when_others_have_no_board():
    handler = _handler(ashby_response={"jobs": [{"id": "xyz", "title": "Engineer"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company_board(client, "SomeCo")

    assert result == ("ashby", "someco")


async def test_probe_returns_none_when_no_ats_has_a_board():
    handler = _handler()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company_board(client, "Nonexistent Co")

    assert result is None


async def test_probe_tries_hyphenated_slug_if_first_candidate_fails():
    """ "Inflection AI" tries "inflectionai" first; only the hyphenated
    "inflection-ai" slug actually has a board in this scenario.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            slug = request.url.path.split("/")[-2]  # /v1/boards/{slug}/jobs
            if slug == "inflection-ai":
                return httpx.Response(
                    200,
                    json={
                        "jobs": [{"id": 1, "title": "Engineer", "company_name": "Inflection AI"}]
                    },
                )
            return httpx.Response(404)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_company_board(client, "Inflection AI")

    assert result == ("greenhouse", "inflection-ai")
