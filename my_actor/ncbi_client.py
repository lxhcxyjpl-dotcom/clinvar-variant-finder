"""Rate-limited NCBI E-utilities client for ClinVar.

Requests go to the fixed E-utilities host only. The optional API key is sent
as a form field and is never written into log messages.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .constants import (
    CONTACT_EMAIL,
    EUTILS_BASE,
    MAX_ATTEMPTS,
    RATE_SAFETY_FACTOR,
    REQUESTS_PER_SECOND_NO_KEY,
    REQUESTS_PER_SECOND_WITH_KEY,
    SUMMARY_BATCH_SIZE,
    TOOL_NAME,
    USER_AGENT,
)

_API_KEY_PARAM = re.compile(r'(api_key=)[^&\s]+', re.IGNORECASE)
_RATE_LIMIT_TEXT = re.compile(r'rate limit|too many requests', re.IGNORECASE)


class Logger(Protocol):
    """Subset of ``Actor.log`` used by the client."""

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


class _NullLogger:
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        return None

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        return None


class NcbiRequestError(RuntimeError):
    """NCBI E-utilities failed or rejected the request."""


@dataclass(frozen=True)
class ESearchResult:
    """Identifiers returned by one ClinVar esearch call."""

    ids: list[str]
    count: int


class RateLimiter:
    """Space request starts so the long-run average stays under ``min_interval``."""

    def __init__(
        self,
        min_interval: float,
        *,
        sleep: Any = asyncio.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._min_interval = min_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._next = 0.0
        self._lock = asyncio.Lock()

    @property
    def min_interval(self) -> float:
        return self._min_interval

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = self._monotonic()
            delay = self._next - now
            if delay > 0:
                await self._sleep(delay)
                now = self._monotonic()
            self._next = now + self._min_interval


def redact_secrets(text: str, api_key: str | None = None) -> str:
    """Remove an NCBI API key from text that might be logged or raised."""
    cleaned = _API_KEY_PARAM.sub(r'\1***', text)
    if api_key:
        cleaned = cleaned.replace(api_key, '***')
    return cleaned


class NcbiEutilsClient:
    """POST JSON requests to ClinVar esearch and esummary."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        logger: Logger | None = None,
        http_client: httpx.AsyncClient | None = None,
        min_interval: float | None = None,
        sleep: Any = asyncio.sleep,
        monotonic: Any = time.monotonic,
        summary_batch_size: int = SUMMARY_BATCH_SIZE,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._api_key = api_key or None
        self._logger = logger or _NullLogger()
        self._sleep = sleep
        self._summary_batch_size = summary_batch_size
        self._max_attempts = max_attempts
        rate = REQUESTS_PER_SECOND_WITH_KEY if self._api_key else REQUESTS_PER_SECOND_NO_KEY
        interval = (RATE_SAFETY_FACTOR / rate) if min_interval is None else min_interval
        self._limiter = RateLimiter(interval, sleep=sleep, monotonic=monotonic)
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={'User-Agent': USER_AGENT},
        )

    @property
    def min_interval(self) -> float:
        return self._limiter.min_interval

    async def __aenter__(self) -> NcbiEutilsClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def esearch(self, term: str, *, retmax: int) -> ESearchResult:
        cleaned = term.strip()
        if not cleaned:
            raise ValueError('ClinVar search term is empty.')
        if retmax < 1:
            raise ValueError('retmax must be at least 1.')
        payload = await self._post(
            'esearch.fcgi',
            {
                'db': 'clinvar',
                'term': cleaned,
                'retmax': str(retmax),
                'retstart': '0',
                'retmode': 'json',
            },
        )
        result = payload.get('esearchresult') if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise NcbiRequestError('ClinVar esearch returned an unexpected payload.')
        error = result.get('ERROR')
        if isinstance(error, str) and error.strip():
            safe = redact_secrets(error.strip(), self._api_key)[:300]
            raise NcbiRequestError(f'ClinVar rejected the search: {safe}')
        raw_ids = result.get('idlist') if isinstance(result.get('idlist'), list) else []
        ids: list[str] = []
        seen: set[str] = set()
        for item in raw_ids:
            text = str(item).strip()
            if text.isdigit() and text not in seen:
                seen.add(text)
                ids.append(text)
        try:
            count = int(result.get('count', len(ids)))
        except (TypeError, ValueError):
            count = len(ids)
        self._logger.info(
            f'ClinVar esearch matched {count} records; fetched {len(ids)} ids for {_short(cleaned)}.'
        )
        return ESearchResult(ids=ids, count=count)

    async def esummary(self, ids: list[str]) -> list[dict[str, Any]]:
        ordered = [str(uid) for uid in ids if str(uid).isdigit()]
        documents: list[dict[str, Any]] = []
        for start in range(0, len(ordered), self._summary_batch_size):
            batch = ordered[start : start + self._summary_batch_size]
            payload = await self._post(
                'esummary.fcgi',
                {
                    'db': 'clinvar',
                    'id': ','.join(batch),
                    'retmode': 'json',
                },
            )
            result = payload.get('result') if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                raise NcbiRequestError('ClinVar esummary returned an unexpected payload.')
            for uid in batch:
                document = result.get(uid)
                if isinstance(document, dict):
                    documents.append(document)
        return documents

    async def _post(self, endpoint: str, params: dict[str, str]) -> Any:
        url = f'{EUTILS_BASE}/{endpoint}'
        form = {
            'tool': TOOL_NAME,
            'email': CONTACT_EMAIL,
            **params,
        }
        if self._api_key:
            form['api_key'] = self._api_key
        last_error = 'NCBI E-utilities request failed.'
        for attempt in range(self._max_attempts):
            await self._limiter.wait()
            try:
                response = await self._http.post(url, data=form)
            except httpx.TransportError as exc:
                last_error = redact_secrets(
                    f'Network error talking to NCBI ({exc.__class__.__name__}).',
                    self._api_key,
                )
                if not await self._pause_for_retry(attempt, last_error, None):
                    break
                continue
            status = response.status_code
            if status == 429 or status >= 500:
                last_error = f'NCBI returned HTTP {status}.'
                if not await self._pause_for_retry(attempt, last_error, _retry_after(response)):
                    break
                continue
            if status >= 400:
                snippet = redact_secrets(_snippet(response.text), self._api_key)
                raise NcbiRequestError(f'NCBI rejected the request (HTTP {status}): {snippet}')
            try:
                parsed = response.json()
            except json.JSONDecodeError:
                last_error = f'NCBI returned a non-JSON response (HTTP {status}).'
                if not await self._pause_for_retry(attempt, last_error, None):
                    break
                continue
            error_text = _top_level_error(parsed)
            if error_text and _RATE_LIMIT_TEXT.search(error_text):
                last_error = 'NCBI rate limit response.'
                if not await self._pause_for_retry(attempt, last_error, None):
                    break
                continue
            if error_text:
                safe = redact_secrets(error_text, self._api_key)[:300]
                raise NcbiRequestError(f'NCBI E-utilities error: {safe}')
            return parsed
        raise NcbiRequestError(f'{last_error} Gave up after {self._max_attempts} attempts.')

    async def _pause_for_retry(self, attempt: int, message: str, retry_after: float | None) -> bool:
        """Return False when this was the last attempt."""
        if attempt + 1 >= self._max_attempts:
            self._logger.warning(message)
            return False
        delay = _backoff(attempt, retry_after)
        self._logger.warning(f'{message} Retrying in {delay:.1f}s ({attempt + 1}/{self._max_attempts}).')
        await self._sleep(delay)
        return True


def _backoff(attempt: int, retry_after: float | None) -> float:
    delay = min(30.0, float(2**attempt))
    if retry_after is not None:
        delay = max(delay, retry_after)
    return min(60.0, delay)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get('retry-after')
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _top_level_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get('error')
    if isinstance(error, str) and error.strip():
        return error.strip()
    return None


def _snippet(text: str) -> str:
    compact = ' '.join(text.split())
    return compact[:180]


def _short(term: str) -> str:
    if len(term) <= 180:
        return term
    return term[:177] + '...'
