"""Query building does not call NCBI."""

from __future__ import annotations

import pytest

from my_actor.query_builder import parse_actor_input


def test_gene_rsid_hgvs_and_variation_id_become_separate_terms() -> None:
    plan = parse_actor_input(
        {
            'query': 'breast cancer',
            'geneSymbols': ['BRCA1', 'brca1', 'TP53'],
            'rsids': ['RS80357906', '80357906'],
            'hgvs': ['NM_007294.4:c.181T>G'],
            'clinvarIds': ['VCV000017661.165', '9'],
            'clinicalSignificance': ['Pathogenic', 'Likely pathogenic', 'VUS'],
            'maxResults': 25,
        }
    )
    significance = 'clinsig_pathogenic[prop] OR clinsig_likely_pathogenic[prop] OR clinsig_vus[prop]'
    assert plan.max_results == 25
    assert plan.api_key is None
    assert plan.terms == (
        f'(breast cancer) AND ({significance})',
        f'("BRCA1"[gene] OR "TP53"[gene]) AND ({significance})',
        f'(rs80357906[vrid]) AND ({significance})',
        f'("NM_007294.4:c.181T>G"[vrnm]) AND ({significance})',
        f'(17661[vid] OR 9[vid]) AND ({significance})',
    )


def test_duplicate_rsids_collapse_to_one_atom() -> None:
    plan = parse_actor_input({'rsids': ['rs80357906', 'RS80357906']})
    assert plan.terms == ('rs80357906[vrid]',)
    assert plan.max_results == 50


def test_hyphenated_gene_is_quoted() -> None:
    plan = parse_actor_input({'geneSymbols': 'HLA-A, C9orf72'})
    assert plan.terms == ('("HLA-A"[gene] OR "C9orf72"[gene])',)


def test_blank_query_is_ignored_when_an_identifier_is_present() -> None:
    plan = parse_actor_input({'query': '   ', 'clinvarIds': ['17661']})
    assert plan.terms == ('17661[vid]',)


def test_significance_string_with_commas_is_split_on_commas_only() -> None:
    plan = parse_actor_input(
        {
            'geneSymbols': ['TP53'],
            'clinicalSignificance': 'Likely benign, Benign',
        }
    )
    assert plan.terms == (
        '("TP53"[gene]) AND (clinsig_likely_benign[prop] OR clinsig_benign[prop])',
    )


def test_conflicting_alias_uses_review_status_field() -> None:
    plan = parse_actor_input(
        {
            'geneSymbols': ['BRCA1'],
            'clinicalSignificance': ['Conflicting interpretations of pathogenicity'],
        }
    )
    assert plan.terms == (
        '("BRCA1"[gene]) AND ("criteria provided, conflicting classifications"[rvst])',
    )


def test_api_key_is_kept_but_hidden_from_repr() -> None:
    plan = parse_actor_input({'query': 'BRCA1', 'ncbiApiKey': '  TESTNCBIKEY1234  '})
    assert plan.api_key == 'TESTNCBIKEY1234'
    assert 'TESTNCBIKEY1234' not in repr(plan)
    assert 'api_key=set' in repr(plan)


@pytest.mark.parametrize(
    ('raw', 'message'),
    [
        ({}, 'Provide a query'),
        ({'clinicalSignificance': ['Pathogenic']}, 'Provide a query'),
        ({'query': 'breast (cancer'}, 'unbalanced parentheses'),
        ({'geneSymbols': ['BRCA1[gene]']}, 'not a gene symbol'),
        ({'rsids': ['rsabc']}, 'not an rsID'),
        ({'hgvs': ['not a variant']}, 'does not look like HGVS'),
        ({'hgvs': ['NM_007294.4:c.181T>G"']}, 'cannot be sent'),
        ({'clinvarIds': ['RCV000019229']}, 'not a ClinVar variation ID'),
        ({'clinicalSignificance': ['Definitely pathogenic']}, 'Unknown clinical significance'),
        ({'maxResults': 0}, 'between 1 and 500'),
        ({'maxResults': 501, 'query': 'BRCA1'}, 'between 1 and 500'),
        ({'maxResults': True, 'query': 'BRCA1'}, 'must be an integer'),
        ({'maxResults': '50', 'query': 'BRCA1'}, 'must be an integer'),
        ({'ncbiApiKey': 'short', 'query': 'BRCA1'}, 'ncbiApiKey'),
        ({'query': 'a\x00b'}, 'control characters'),
    ],
)
def test_invalid_input_raises_value_error(raw: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_actor_input(raw)
