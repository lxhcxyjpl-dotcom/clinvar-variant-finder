"""HTTP behaviour is covered with a mock transport."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from my_actor.constants import RATE_SAFETY_FACTOR, REQUESTS_PER_SECOND_NO_KEY, REQUESTS_PER_SECOND_WITH_KEY
from my_actor.ncbi_client import ESearchResult, NcbiEutilsClient, NcbiRequestError, RateLimiter, redact_secrets


class ListLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        self.infos.append(msg)

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        self.warnings.append(msg)

    def debug(self, msg: str, *args: object, **kwargs: object) -> None:
        return None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


async def test_rate_limiter_spaces_request_starts() -> None:
    clock = FakeClock()
    limiter = RateLimiter(0.5, sleep=clock.sleep, monotonic=clock.monotonic)
    await limiter.wait()
    await limiter.wait()
    assert clock.slept == [0.5]


async def test_default_intervals_stay_under_ncbi_ceilings() -> None:
    async with NcbiEutilsClient() as without_key, NcbiEutilsClient(api_key='TESTNCBIKEY1234') as with_key:
        assert without_key.min_interval == pytest.approx(RATE_SAFETY_FACTOR / REQUESTS_PER_SECOND_NO_KEY)
        assert with_key.min_interval == pytest.approx(RATE_SAFETY_FACTOR / REQUESTS_PER_SECOND_WITH_KEY)


def test_redact_secrets_removes_key_from_urls_and_text() -> None:
    raw = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?api_key=TESTNCBIKEY1234&db=clinvar'
    assert 'TESTNCBIKEY1234' not in redact_secrets(raw, 'TESTNCBIKEY1234')
    assert 'api_key=***' in redact_secrets(raw)


async def test_esearch_posts_tool_email_and_optional_key() -> None:
    seen: list[tuple[str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), _form(request)))
        return httpx.Response(200, json={'esearchresult': {'count': '2', 'idlist': ['9', '9', '17661']}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with NcbiEutilsClient(api_key='TESTNCBIKEY1234', http_client=http, min_interval=0) as client:
            page = await client.esearch('BRCA1', retmax=2)
    assert page == ESearchResult(ids=['9', '17661'], count=2)
    url, form = seen[0]
    assert 'api_key' not in url
    assert form['db'] == ['clinvar']
    assert form['term'] == ['BRCA1']
    assert form['retmode'] == ['json']
    assert form['tool'] == ['clinvar-variant-finder']
    assert form['email'] == ['actor@apify.local']
    assert form['api_key'] == ['TESTNCBIKEY1234']


async def test_esearch_omits_key_when_absent() -> None:
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_form(request))
        return httpx.Response(200, json={'esearchresult': {'count': '0', 'idlist': []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with NcbiEutilsClient(http_client=http, min_interval=0) as client:
            page = await client.esearch('"BRCA1"[gene]', retmax=1)
    assert page.ids == []
    assert 'api_key' not in seen[0]


async def test_retries_429_then_succeeds_without_logging_response_body() -> None:
    calls = {'n': 0}
    logger = ListLogger()
    clock = FakeClock()

    def handler(_request: httpx.Request) -> httpx.Response:
        calls['n'] += 1
        if calls['n'] < 3:
            return httpx.Response(
                429,
                headers={'Retry-After': '0'},
                text='rate limit for api_key=TESTNCBIKEY1234',
            )
        return httpx.Response(200, json={'esearchresult': {'count': '1', 'idlist': ['17661']}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with NcbiEutilsClient(
            api_key='TESTNCBIKEY1234',
            http_client=http,
            min_interval=0,
            sleep=clock.sleep,
            logger=logger,
        ) as client:
            page = await client.esearch('17661[vid]', retmax=1)
    assert page.ids == ['17661']
    assert calls['n'] == 3
    assert clock.slept == [1.0, 2.0]
    logged = ' '.join(logger.warnings + logger.infos)
    assert 'TESTNCBIKEY1234' not in logged
    assert 'HTTP 429' in logged


async def test_server_error_gives_up_with_safe_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text='upstream failed TESTNCBIKEY1234')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with NcbiEutilsClient(
            api_key='TESTNCBIKEY1234',
            http_client=http,
            min_interval=0,
            sleep=_no_sleep,
            max_attempts=2,
        ) as client:
            with pytest.raises(NcbiRequestError, match='HTTP 503') as caught:
                await client.esearch('BRCA1', retmax=1)
    assert 'TESTNCBIKEY1234' not in str(caught.value)


async def test_rejected_request_redacts_key_in_the_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='bad key TESTNCBIKEY1234 rejected')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with NcbiEutilsClient(api_key='TESTNCBIKEY1234', http_client=http, min_interval=0) as client:
            with pytest.raises(NcbiRequestError, match='HTTP 400') as caught:
                await client.esearch('BRCA1', retmax=1)
    assert 'TESTNCBIKEY1234' not in str(caught.value)
    assert 'bad key' in str(caught.value)


async def test_esummary_batches_ids_and_keeps_order() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = _form(request)['id'][0]
        seen.append(ids)
        payload = {'result': {'uids': ids.split(',')}}
        for uid in ids.split(','):
            payload['result'][uid] = {'uid': uid, 'title': uid}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with NcbiEutilsClient(http_client=http, min_interval=0, summary_batch_size=2) as client:
            documents = await client.esummary(['1', '2', '3'])
    assert seen == ['1,2', '3']
    assert [document['uid'] for document in documents] == ['1', '2', '3']
    json.dumps(documents)


async def _no_sleep(_seconds: float) -> None:
    return None
