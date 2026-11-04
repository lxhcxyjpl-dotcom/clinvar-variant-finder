"""Run ClinVar searches and merge them into one variant list."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .ncbi_client import Logger, NcbiEutilsClient
from .parsing import parse_summary_document


@dataclass(frozen=True)
class SearchStat:
    """How many ClinVar records one Entrez term reported."""

    term: str
    total_count: int
    fetched_ids: int


@dataclass(frozen=True)
class CollectResult:
    """Flat rows plus per-term counts for the run overview."""

    rows: list[dict[str, Any]]
    stats: list[SearchStat]


async def collect_variants(
    client: NcbiEutilsClient,
    terms: Sequence[str],
    *,
    max_results: int,
    logger: Logger,
    on_batch: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
) -> CollectResult:
    """Search each term, dedupe variation ids, and flatten esummary documents."""
    ordered_ids: list[str] = []
    seen: set[str] = set()
    stats: list[SearchStat] = []
    for term in terms:
        if len(ordered_ids) >= max_results:
            break
        remaining = max_results - len(ordered_ids)
        page = await client.esearch(term, retmax=remaining)
        fresh: list[str] = []
        for variation_id in page.ids:
            if variation_id in seen:
                continue
            seen.add(variation_id)
            fresh.append(variation_id)
            ordered_ids.append(variation_id)
            if len(ordered_ids) >= max_results:
                break
        stats.append(SearchStat(term=term, total_count=page.count, fetched_ids=len(fresh)))

    rows: list[dict[str, Any]] = []
    for start in range(0, len(ordered_ids), 100):
        chunk = ordered_ids[start : start + 100]
        documents = await client.esummary(chunk)
        by_uid = {str(document.get('uid')): document for document in documents if isinstance(document, dict)}
        batch: list[dict[str, Any]] = []
        for variation_id in chunk:
            document = by_uid.get(variation_id)
            if document is None:
                logger.warning(f'ClinVar returned no summary for variation {variation_id}.')
                continue
            if isinstance(document.get('error'), str) and document.get('error', '').strip():
                logger.warning(f'ClinVar summary for variation {variation_id} was unavailable.')
                continue
            row = parse_summary_document(document)
            if row is None:
                logger.warning(f'ClinVar summary for variation {variation_id} could not be flattened.')
                continue
            rows.append(row)
            batch.append(row)
        if on_batch is not None and batch:
            await on_batch(batch)
    return CollectResult(rows=rows, stats=stats)
