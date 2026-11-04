"""Validate Actor input and build ClinVar Entrez queries.

ClinVar's Entrez database does not honor guessed field tags such as
``[clinsig]`` or ``[hgvs]`` (``einfo`` rewrites them to ``[All Fields]``).
The tags below are the ones ``esearch`` actually keeps: ``[gene]``,
``[vrid]``, ``[vrnm]``, ``[vid]``, ``[prop]``, and ``[rvst]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .constants import DEFAULT_MAX_RESULTS, MAX_LIST_ITEMS, MAX_MAX_RESULTS, MAX_QUERY_LENGTH, MIN_MAX_RESULTS

_GENE_RE = re.compile(r'^[A-Za-z][A-Za-z0-9._-]{0,39}$')
_RS_RE = re.compile(r'^(?:rs)?(\d{1,15})$', re.IGNORECASE)
_VARIATION_RE = re.compile(r'^(?:VCV)?0*(\d{1,12})(?:\.\d+)?$', re.IGNORECASE)
_HGVS_RE = re.compile(
    r'(?:^|:)[cgnpmr]\.|^NM_|^NP_|^NC_|^NG_|^NR_|^XM_|^XP_|^XR_|^ENST|^ENSP|^LRG_',
    re.IGNORECASE,
)
_API_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_WHITESPACE_LIST = re.compile(r'[,\s]+')
_COMMA_LIST = re.compile(r'[,\n]+')

# Property and review-status tokens confirmed against live esearch counts.
_SIGNIFICANCE_TERMS: dict[str, str] = {
    'pathogenic': 'clinsig_pathogenic[prop]',
    'likely pathogenic': 'clinsig_likely_pathogenic[prop]',
    'uncertain significance': 'clinsig_vus[prop]',
    'likely benign': 'clinsig_likely_benign[prop]',
    'benign': 'clinsig_benign[prop]',
    'conflicting classifications of pathogenicity': '"criteria provided, conflicting classifications"[rvst]',
}
_SIGNIFICANCE_ALIASES: dict[str, str] = {
    'vus': 'uncertain significance',
    'vous': 'uncertain significance',
    'uncertain': 'uncertain significance',
    'variant of uncertain significance': 'uncertain significance',
    'conflicting': 'conflicting classifications of pathogenicity',
    'conflicting interpretations': 'conflicting classifications of pathogenicity',
    'conflicting interpretations of pathogenicity': 'conflicting classifications of pathogenicity',
    'conflicting classifications': 'conflicting classifications of pathogenicity',
}
SIGNIFICANCE_LABELS: tuple[str, ...] = (
    'Pathogenic',
    'Likely pathogenic',
    'Uncertain significance',
    'Likely benign',
    'Benign',
    'Conflicting classifications of pathogenicity',
)


@dataclass(frozen=True)
class ActorInput:
    """Validated search plan. ``api_key`` is omitted from ``repr``."""

    terms: tuple[str, ...]
    max_results: int
    api_key: str | None

    def __repr__(self) -> str:
        key_state = 'set' if self.api_key else 'absent'
        return f'ActorInput(terms={self.terms!r}, max_results={self.max_results}, api_key={key_state})'


def parse_actor_input(raw: Any) -> ActorInput:
    """Validate raw Actor input and return one Entrez term per search mode."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError('Actor input must be a JSON object.')

    query = _optional_query(raw.get('query'))
    genes = _cap(_string_list(raw.get('geneSymbols'), 'geneSymbols', split_on_whitespace=True), 'geneSymbols')
    rsids = _cap(_string_list(raw.get('rsids'), 'rsids', split_on_whitespace=True), 'rsids')
    hgvs_values = _cap(_string_list(raw.get('hgvs'), 'hgvs', split_on_whitespace=False), 'hgvs')
    variation_ids = _cap(
        _string_list(raw.get('clinvarIds'), 'clinvarIds', split_on_whitespace=True),
        'clinvarIds',
    )
    significance = _significance_clause(raw.get('clinicalSignificance'))
    max_results = _max_results(raw.get('maxResults'))
    api_key = _api_key(raw.get('ncbiApiKey'))

    terms: list[str] = []
    if query:
        terms.append(_and_filter(query, significance))
    gene_clause = _or_clause(_dedupe_exact([_parse_gene(value) for value in _dedupe_casefold(genes)]))
    if gene_clause:
        terms.append(_and_filter(gene_clause, significance))
    rsid_clause = _or_clause(_dedupe_exact([_parse_rsid(value) for value in rsids]))
    if rsid_clause:
        terms.append(_and_filter(rsid_clause, significance))
    hgvs_clause = _or_clause(_dedupe_exact([_parse_hgvs(value) for value in _dedupe_exact(hgvs_values)]))
    if hgvs_clause:
        terms.append(_and_filter(hgvs_clause, significance))
    id_clause = _or_clause(_dedupe_exact([_parse_variation_id(value) for value in variation_ids]))
    if id_clause:
        terms.append(_and_filter(id_clause, significance))

    if not terms:
        raise ValueError(
            'Provide a query or at least one gene symbol, rsID, HGVS expression, or ClinVar variation ID.'
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            deduped.append(term)
    return ActorInput(terms=tuple(deduped), max_results=max_results, api_key=api_key)


def _optional_query(raw: Any) -> str:
    if raw is None:
        return ''
    if not isinstance(raw, str):
        raise ValueError('query must be a string.')
    text = raw.strip()
    if not text:
        return ''
    if len(text) > MAX_QUERY_LENGTH:
        raise ValueError(f'query is too long (maximum {MAX_QUERY_LENGTH} characters).')
    if any(ord(char) < 32 and char not in '\t\n\r' for char in text):
        raise ValueError('query contains unsupported control characters.')
    if text.count('(') != text.count(')'):
        raise ValueError('query has unbalanced parentheses.')
    return text


def _string_list(raw: Any, field: str, *, split_on_whitespace: bool) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        pattern = _WHITESPACE_LIST if split_on_whitespace else _COMMA_LIST
        parts = pattern.split(raw.strip()) if raw.strip() else []
        return [part.strip() for part in parts if part.strip()]
    if isinstance(raw, list):
        values: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                raise ValueError(f'{field} must be a list of strings.')
            text = item.strip()
            if text:
                values.append(text)
        return values
    raise ValueError(f'{field} must be a list of strings.')


def _cap(values: list[str], field: str) -> list[str]:
    if len(values) > MAX_LIST_ITEMS:
        raise ValueError(f'{field} accepts at most {MAX_LIST_ITEMS} entries.')
    return values


def _dedupe_casefold(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def _dedupe_exact(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _parse_gene(value: str) -> str:
    if not _GENE_RE.fullmatch(value):
        raise ValueError(f'geneSymbols entry {value!r} is not a gene symbol. Example: BRCA1 or HLA-A.')
    return f'"{value}"[gene]'


def _parse_rsid(value: str) -> str:
    match = _RS_RE.fullmatch(value)
    if match is None or set(match.group(1)) == {'0'}:
        raise ValueError(f'rsids entry {value!r} is not an rsID. Example: rs80357906.')
    return f'rs{match.group(1)}[vrid]'


def _parse_hgvs(value: str) -> str:
    if len(value) > 500:
        raise ValueError(f'hgvs entry {value!r} is too long.')
    if any(char in value for char in '"[]{}\n\r\t\\'):
        raise ValueError(f'hgvs entry {value!r} contains characters that cannot be sent in a ClinVar query.')
    if _HGVS_RE.search(value) is None:
        raise ValueError(f'hgvs entry {value!r} does not look like HGVS. Example: NM_007294.4:c.181T>G.')
    return f'"{value}"[vrnm]'


def _parse_variation_id(value: str) -> str:
    match = _VARIATION_RE.fullmatch(value)
    if match is None:
        raise ValueError(
            f'clinvarIds entry {value!r} is not a ClinVar variation ID. '
            'Use digits (17661) or a VCV accession (VCV000017661).'
        )
    number = str(int(match.group(1)))
    if number == '0':
        raise ValueError(f'clinvarIds entry {value!r} is not a valid variation ID.')
    return f'{number}[vid]'


def _significance_clause(raw: Any) -> str | None:
    labels = _string_list(raw, 'clinicalSignificance', split_on_whitespace=False)
    if len(labels) > 20:
        raise ValueError('clinicalSignificance accepts at most 20 values.')
    parts: list[str] = []
    seen: set[str] = set()
    allowed = ', '.join(SIGNIFICANCE_LABELS)
    for label in labels:
        key = re.sub(r'\s+', ' ', label.strip().lower())
        key = _SIGNIFICANCE_ALIASES.get(key, key)
        term = _SIGNIFICANCE_TERMS.get(key)
        if term is None:
            raise ValueError(f'Unknown clinical significance {label!r}. Expected one of: {allowed}.')
        if term not in seen:
            seen.add(term)
            parts.append(term)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return ' OR '.join(parts)


def _and_filter(expression: str, significance: str | None) -> str:
    needs_group = ' OR ' in expression
    if not significance:
        return f'({expression})' if needs_group else expression
    return f'({expression}) AND ({significance})'


def _or_clause(atoms: list[str]) -> str:
    if not atoms:
        return ''
    if len(atoms) == 1:
        return atoms[0]
    return ' OR '.join(atoms)


def _max_results(raw: Any) -> int:
    if raw is None:
        return DEFAULT_MAX_RESULTS
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError('maxResults must be an integer.')
    if raw < MIN_MAX_RESULTS or raw > MAX_MAX_RESULTS:
        raise ValueError(f'maxResults must be between {MIN_MAX_RESULTS} and {MAX_MAX_RESULTS}.')
    return raw


def _api_key(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError('ncbiApiKey must be a string.')
    text = raw.strip()
    if not text:
        return None
    if not _API_KEY_RE.fullmatch(text):
        raise ValueError('ncbiApiKey must be 8 to 128 letters, digits, underscores, or hyphens, with no spaces.')
    return text
