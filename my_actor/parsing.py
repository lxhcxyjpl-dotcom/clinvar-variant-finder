"""Flatten ClinVar esummary documents into dataset rows."""

from __future__ import annotations

import re
from typing import Any

from .constants import CLINVAR_VARIATION_URL

_DATE_RE = re.compile(r'^(\d{1,4})/(\d{1,2})/(\d{1,2})')
_HGVS_PREFIXES = ('NM_', 'NP_', 'NC_', 'NG_', 'NR_', 'XM_', 'XP_', 'XR_', 'ENST', 'ENSP', 'LRG_')
_HGVS_CHANGE_RE = re.compile(r'(?:^|:)[cgnpmr]\.', re.IGNORECASE)
_SPDI_RE = re.compile(r'^NC_\d+\.\d+:\d+:[^:]*:[^:]*$', re.IGNORECASE)
_RS_RE = re.compile(r'^rs(\d+)$', re.IGNORECASE)
_CLASSIFICATION_KEYS = (
    'germline_classification',
    'clinical_significance',
    'oncogenicity_classification',
    'clinical_impact_classification',
)


def parse_esummary_payload(payload: Any) -> list[dict[str, Any]]:
    """Parse an esummary JSON object into flat rows, skipping missing UIDs."""
    if not isinstance(payload, dict):
        raise ValueError('ClinVar esummary payload must be a JSON object.')
    result = payload.get('result')
    if not isinstance(result, dict):
        raise ValueError('ClinVar esummary payload is missing result.')
    uids = result.get('uids')
    if not isinstance(uids, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for uid in uids:
        document = result.get(str(uid))
        row = parse_summary_document(document if isinstance(document, dict) else None)
        if row is None:
            continue
        clinvar_id = str(row['clinvarId'])
        if clinvar_id in seen:
            continue
        seen.add(clinvar_id)
        rows.append(row)
    return rows


def parse_summary_document(document: Any) -> dict[str, Any] | None:
    """Flatten one ClinVar document summary. Returns None when NCBI reports no document."""
    if not isinstance(document, dict):
        return None
    error = document.get('error')
    if isinstance(error, str) and error.strip():
        return None
    clinvar_id = _digits(document.get('uid'))
    if not clinvar_id:
        return None

    genes = _genes(document)
    primary_symbol, primary_gene_id = _primary_gene(document, genes)
    classification = _classification(document)
    location = _select_location(document.get('variation_set'))
    hgvs = _hgvs_values(document)
    rsids = _rsids(document)
    phenotypes = _phenotypes(classification, document)
    consequences = _string_list(document.get('molecular_consequence_list'))

    row: dict[str, Any] = {
        'clinvarId': clinvar_id,
        'name': _text(document.get('title')),
        'geneSymbol': primary_symbol,
        'geneId': primary_gene_id,
        'geneSymbols': [symbol for symbol, _gene_id in genes if symbol] if len(genes) > 1 else None,
        'clinicalSignificance': _text(classification.get('description')) if classification else None,
        'reviewStatus': _text(classification.get('review_status')) if classification else None,
        'lastEvaluated': _normalize_date(classification.get('last_evaluated')) if classification else None,
        'molecularConsequence': consequences or None,
        'variationType': _variation_type(document),
        'hgvs': hgvs or None,
        'rsid': rsids[0] if rsids else None,
        'rsids': rsids if len(rsids) > 1 else None,
        'phenotypes': phenotypes or None,
        'proteinChange': _text(document.get('protein_change')),
        'canonicalSpdi': _canonical_spdi(document),
        'chromosome': _text(location.get('chr')) if location else None,
        'start': _coordinate(location, 'start', 'display_start', 'outer_start'),
        'stop': _coordinate(location, 'stop', 'display_stop', 'outer_stop'),
        'assembly': _text(location.get('assembly_name')) if location else None,
        'submitterConflict': _submitter_conflict(document),
        'clinvarUrl': CLINVAR_VARIATION_URL.format(clinvar_id=clinvar_id),
        'accession': _text(document.get('accession')),
        'accessionVersion': _text(document.get('accession_version')),
    }
    return _compact(row)


def _genes(document: dict[str, Any]) -> list[tuple[str | None, str | None]]:
    raw_genes = document.get('genes')
    if not isinstance(raw_genes, list):
        return []
    genes: list[tuple[str | None, str | None]] = []
    for gene in raw_genes:
        if not isinstance(gene, dict):
            continue
        symbol = _text(gene.get('symbol'))
        gene_id = _digits(gene.get('geneid'))
        if symbol or gene_id:
            genes.append((symbol, gene_id))
    return genes


def _primary_gene(
    document: dict[str, Any],
    genes: list[tuple[str | None, str | None]],
) -> tuple[str | None, str | None]:
    sort_symbol = _text(document.get('gene_sort'))
    if sort_symbol:
        for symbol, gene_id in genes:
            if symbol and symbol.casefold() == sort_symbol.casefold():
                return symbol, gene_id
    if genes:
        return genes[0]
    if sort_symbol:
        return sort_symbol, None
    return None, None


def _classification(document: dict[str, Any]) -> dict[str, Any] | None:
    for key in _CLASSIFICATION_KEYS:
        block = document.get(key)
        if isinstance(block, dict) and _text(block.get('description')):
            return block
    for key in _CLASSIFICATION_KEYS:
        block = document.get(key)
        if isinstance(block, dict) and (_text(block.get('review_status')) or _phenotypes_from_block(block)):
            return block
    return None


def _variation_blocks(document: dict[str, Any]) -> list[dict[str, Any]]:
    raw = document.get('variation_set')
    if not isinstance(raw, list):
        return []
    return [block for block in raw if isinstance(block, dict)]


def _variation_type(document: dict[str, Any]) -> str | None:
    for block in _variation_blocks(document):
        variant_type = _text(block.get('variant_type'))
        if variant_type:
            return variant_type
    return _text(document.get('obj_type'))


def _canonical_spdi(document: dict[str, Any]) -> str | None:
    for block in _variation_blocks(document):
        spdi = _text(block.get('canonical_spdi'))
        if spdi:
            return spdi
    return None


def _hgvs_values(document: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    title = _text(document.get('title'))
    if title:
        candidates.append(title)
    for block in _variation_blocks(document):
        for key in ('variation_name', 'cdna_change'):
            text = _text(block.get(key))
            if text:
                candidates.append(text)
        aliases = block.get('aliases')
        if isinstance(aliases, list):
            for alias in aliases:
                text = _text(alias)
                if text:
                    candidates.append(text)
    values: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not _is_hgvs(candidate):
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(candidate)
    return values


def _is_hgvs(value: str) -> bool:
    text = value.strip()
    if len(text) < 3 or len(text) > 300 or _SPDI_RE.fullmatch(text):
        return False
    upper = text.upper()
    if upper.startswith(_HGVS_PREFIXES):
        return True
    return _HGVS_CHANGE_RE.search(text) is not None


def _rsids(document: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(rsid: str | None) -> None:
        if not rsid:
            return
        key = rsid.casefold()
        if key in seen:
            return
        seen.add(key)
        found.append(rsid)

    for block in _variation_blocks(document):
        xrefs = block.get('variation_xrefs')
        if isinstance(xrefs, list):
            for xref in xrefs:
                if isinstance(xref, dict):
                    add(_rsid_from_xref(xref))
        aliases = block.get('aliases')
        if isinstance(aliases, list):
            for alias in aliases:
                text = _text(alias)
                if text and _RS_RE.fullmatch(text):
                    add(f'rs{text[2:]}')
    return found


def _rsid_from_xref(xref: dict[str, Any]) -> str | None:
    source = (_text(xref.get('db_source')) or '').casefold().replace(' ', '')
    if source not in {'dbsnp', 'snp'}:
        return None
    db_id = _text(xref.get('db_id'))
    if not db_id:
        return None
    if db_id.casefold().startswith('rs'):
        digits = db_id[2:]
    else:
        digits = db_id
    if not digits.isdigit() or set(digits) == {'0'}:
        return None
    return f'rs{digits}'


def _select_location(variation_set: Any) -> dict[str, Any] | None:
    if not isinstance(variation_set, list):
        return None
    locations: list[dict[str, Any]] = []
    for block in variation_set:
        if not isinstance(block, dict):
            continue
        raw_locations = block.get('variation_loc')
        if isinstance(raw_locations, list):
            locations.extend(location for location in raw_locations if isinstance(location, dict))
    if not locations:
        return None

    def rank(location: dict[str, Any]) -> tuple[int, int]:
        assembly = (_text(location.get('assembly_name')) or '').upper()
        status = (_text(location.get('status')) or '').lower()
        if assembly == 'GRCH38' and status == 'current':
            group = 0
        elif assembly == 'GRCH38':
            group = 1
        elif assembly == 'GRCH37' and status == 'current':
            group = 2
        elif status == 'current':
            group = 3
        else:
            group = 4
        return group, 0 if _coordinate(location, 'start', 'display_start', 'outer_start') is not None else 1

    return min(locations, key=rank)


def _coordinate(location: dict[str, Any] | None, *keys: str) -> int | None:
    if location is None:
        return None
    chosen = keys or ('start', 'display_start', 'outer_start')
    for key in chosen:
        text = _text(location.get(key))
        if text and _is_int(text):
            return int(text)
    return None


def _phenotypes(classification: dict[str, Any] | None, document: dict[str, Any]) -> list[str]:
    names = _phenotypes_from_block(classification) if classification else []
    if names:
        return names
    merged: list[str] = []
    seen: set[str] = set()
    for key in _CLASSIFICATION_KEYS:
        block = document.get(key)
        if not isinstance(block, dict):
            continue
        for name in _phenotypes_from_block(block):
            marker = name.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(name)
    return merged


def _phenotypes_from_block(block: dict[str, Any] | None) -> list[str]:
    if not isinstance(block, dict):
        return []
    traits = block.get('trait_set')
    if not isinstance(traits, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for trait in traits:
        if not isinstance(trait, dict):
            continue
        name = _text(trait.get('trait_name'))
        if not name:
            continue
        marker = name.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        names.append(name)
    return names


def _submitter_conflict(document: dict[str, Any]) -> bool:
    for key in _CLASSIFICATION_KEYS:
        block = document.get(key)
        if not isinstance(block, dict):
            continue
        if _has_conflict(_text(block.get('review_status')) or '') or _has_conflict(_text(block.get('description')) or ''):
            return True
    return False


def _has_conflict(text: str) -> bool:
    lowered = text.lower()
    if 'conflicting' in lowered:
        return True
    if 'no conflict' in lowered:
        return False
    return bool(re.search(r'\bconflicts?\b', lowered))


def _normalize_date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = _DATE_RE.match(text)
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    if year < 1900 or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f'{year:04d}-{month:02d}-{day:02d}'


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text(item)
        if not text:
            continue
        marker = text.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        items.append(text)
    return items


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in row.items():
        if value is None or value == '' or value == []:
            continue
        compact[key] = value
    return compact


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not value.is_integer():
            return None
        text = str(int(value))
    else:
        text = str(value).strip()
    return text or None


def _digits(value: Any) -> str | None:
    text = _text(value)
    if text and text.isdigit():
        return text
    return None


def _is_int(value: str) -> bool:
    if value[0] in '+-' and len(value) > 1:
        return value[1:].isdigit()
    return value.isdigit()
