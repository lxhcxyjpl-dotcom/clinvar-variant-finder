"""Apify Actor entry point for ClinVar Variant Finder."""

from __future__ import annotations

from typing import Any

from apify import Actor, Event

from .ncbi_client import NcbiEutilsClient, NcbiRequestError
from .query_builder import parse_actor_input
from .search import collect_variants


async def _on_abort(_event_data: Any) -> None:
    Actor.log.warning('Aborting. Variants already written to the dataset are kept.')
    await Actor.exit()


async def main() -> None:
    """Search ClinVar through NCBI E-utilities and push one dataset row per variant."""
    async with Actor:
        Actor.on(Event.ABORTING, _on_abort)
        raw_input = await Actor.get_input()
        try:
            actor_input = parse_actor_input(raw_input)
        except ValueError as exc:
            message = str(exc)
            Actor.log.error(message)
            await Actor.fail(status_message=message, exception=exc)
            return

        Actor.log.info(
            'Starting ClinVar search via NCBI E-utilities.',
            extra={
                'searches': len(actor_input.terms),
                'maxResults': actor_input.max_results,
                'ncbiApiKey': bool(actor_input.api_key),
            },
        )
        await Actor.set_status_message(
            f'Searching ClinVar ({len(actor_input.terms)} '
            f'search{"es" if len(actor_input.terms) != 1 else ""}).'
        )

        async def push_batch(rows: list[dict[str, Any]]) -> None:
            if rows:
                await Actor.push_data(rows)

        try:
            async with NcbiEutilsClient(api_key=actor_input.api_key, logger=Actor.log) as client:
                result = await collect_variants(
                    client,
                    actor_input.terms,
                    max_results=actor_input.max_results,
                    logger=Actor.log,
                    on_batch=push_batch,
                )
        except (NcbiRequestError, ValueError) as exc:
            message = str(exc)
            Actor.log.error(message)
            await Actor.fail(status_message=message, exception=exc)
            return

        overview = {
            'variantCount': len(result.rows),
            'source': 'NCBI ClinVar E-utilities',
            'database': 'clinvar',
            'searches': [
                {
                    'term': stat.term,
                    'totalCount': stat.total_count,
                    'fetchedIds': stat.fetched_ids,
                }
                for stat in result.stats
            ],
        }
        await Actor.set_value('OUTPUT', overview)
        if result.rows:
            final_message = f'Saved {len(result.rows)} ClinVar variant{"s" if len(result.rows) != 1 else ""}.'
        else:
            final_message = 'No ClinVar variants matched this search.'
        Actor.status_message = final_message
        Actor.log.info(final_message)
