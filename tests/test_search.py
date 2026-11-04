"""Merging search modes dedupes ClinVar variation ids."""

from __future__ import annotations

from my_actor.ncbi_client import ESearchResult
from my_actor.search import collect_variants


class FakeClient:
    def __init__(self, pages: dict[str, ESearchResult]) -> None:
        self.pages = pages
        self.summary_calls: list[list[str]] = []
        self.searched: list[str] = []

    async def esearch(self, term: str, *, retmax: int) -> ESearchResult:
        self.searched.append(term)
        page = self.pages[term]
        return ESearchResult(ids=page.ids[:retmax], count=page.count)

    async def esummary(self, ids: list[str]) -> list[dict]:
        self.summary_calls.append(list(ids))
        return [_document(variation_id) for variation_id in ids]


class ListLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        self.warnings.append(msg)

    def debug(self, msg: str, *args: object, **kwargs: object) -> None:
        return None


def _document(variation_id: str) -> dict:
    if variation_id == '404':
        return {'uid': variation_id, 'error': 'cannot get document summary'}
    return {
        'uid': variation_id,
        'title': f'NM_000000.1:c.{variation_id}A>T',
        'obj_type': 'single nucleotide variant',
        'genes': [{'symbol': 'BRCA1', 'geneid': '672'}],
        'germline_classification': {
            'description': 'Pathogenic',
            'review_status': 'reviewed by expert panel',
            'last_evaluated': '2020/01/02 00:00',
            'trait_set': [{'trait_name': 'Example condition'}],
        },
    }


async def test_modes_are_merged_and_duplicate_ids_fetched_once() -> None:
    client = FakeClient(
        {
            'genes': ESearchResult(ids=['1', '2', '2'], count=10),
            'rsids': ESearchResult(ids=['2', '3'], count=4),
        }
    )
    pushed: list[list[dict]] = []

    async def on_batch(rows: list[dict]) -> None:
        pushed.append(rows)

    result = await collect_variants(
        client,  # type: ignore[arg-type]
        ['genes', 'rsids'],
        max_results=10,
        logger=ListLogger(),
        on_batch=on_batch,
    )
    assert [row['clinvarId'] for row in result.rows] == ['1', '2', '3']
    assert client.summary_calls == [['1', '2', '3']]
    assert [row['clinvarId'] for batch in pushed for row in batch] == ['1', '2', '3']
    assert result.stats[0].fetched_ids == 2
    assert result.stats[1].fetched_ids == 1
    assert result.stats[0].total_count == 10


async def test_stops_searching_once_max_results_is_filled() -> None:
    client = FakeClient(
        {
            'first': ESearchResult(ids=['1', '2'], count=50),
            'second': ESearchResult(ids=['3'], count=5),
        }
    )
    result = await collect_variants(
        client,  # type: ignore[arg-type]
        ['first', 'second'],
        max_results=2,
        logger=ListLogger(),
    )
    assert client.searched == ['first']
    assert [row['clinvarId'] for row in result.rows] == ['1', '2']


async def test_unavailable_summary_is_skipped() -> None:
    logger = ListLogger()
    client = FakeClient({'only': ESearchResult(ids=['8', '404'], count=2)})
    result = await collect_variants(
        client,  # type: ignore[arg-type]
        ['only'],
        max_results=5,
        logger=logger,
    )
    assert [row['clinvarId'] for row in result.rows] == ['8']
    assert any('404' in message for message in logger.warnings)
