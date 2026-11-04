"""Parsing uses saved esummary documents, not the network."""

from __future__ import annotations

import json
from pathlib import Path

from my_actor.parsing import parse_esummary_payload, parse_summary_document

FIXTURES = Path(__file__).parent / 'fixtures'


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_real_brca1_summary_flattens_to_one_row() -> None:
    rows = parse_esummary_payload(_load('esummary_brca1_17661.json'))
    assert len(rows) == 1
    row = rows[0]
    assert row['clinvarId'] == '17661'
    assert row['name'] == 'NM_007294.4(BRCA1):c.181T>G (p.Cys61Gly)'
    assert row['geneSymbol'] == 'BRCA1'
    assert row['geneId'] == '672'
    assert 'geneSymbols' not in row
    assert row['clinicalSignificance'] == 'Pathogenic'
    assert row['reviewStatus'] == 'reviewed by expert panel'
    assert row['lastEvaluated'] == '2015-08-10'
    assert row['molecularConsequence'] == ['missense variant', 'non-coding transcript variant']
    assert row['variationType'] == 'single nucleotide variant'
    assert 'NM_007294.4(BRCA1):c.181T>G (p.Cys61Gly)' in row['hgvs']
    assert 'c.181T>G' in row['hgvs']
    assert 'NP_009225.1:p.Cys61Gly' in row['hgvs']
    assert '300T>G' not in row['hgvs']
    assert row['canonicalSpdi'] not in row['hgvs']
    assert row['rsid'] == 'rs28897672'
    assert row['phenotypes'] == ['Breast-ovarian cancer, familial, susceptibility to, 1']
    assert row['chromosome'] == '17'
    assert row['start'] == 43106487
    assert row['stop'] == 43106487
    assert row['assembly'] == 'GRCh38'
    assert row['submitterConflict'] is False
    assert row['clinvarUrl'] == 'https://www.ncbi.nlm.nih.gov/clinvar/variation/17661/'
    assert row['accession'] == 'VCV000017661'
    assert row['accessionVersion'] == 'VCV000017661.165'
    assert row['proteinChange'] == 'C61G, C14G'
    assert row['canonicalSpdi'] == 'NC_000017.11:43106486:A:C'
    assert 'supporting_submissions' not in row
    assert 'allele_freq_set' not in row
    assert all(value is not None for value in row.values())


def test_conflicting_record_prefers_grch38_and_flags_submitters() -> None:
    row = parse_esummary_payload(_load('esummary_conflicting.json'))[0]
    assert row['submitterConflict'] is True
    assert row['clinicalSignificance'] == 'Conflicting classifications of pathogenicity'
    assert row['assembly'] == 'GRCh38'
    assert row['chromosome'] == '8'
    assert row['start'] == 60825999
    assert row['stop'] == 60826000
    assert row['geneSymbol'] == 'CHD7'
    assert row['geneSymbols'] == ['CHD7', 'LOC105379']
    assert row['rsid'] == 'rs1555532857'
    assert row['phenotypes'] == ['CHARGE syndrome', 'not provided']
    assert row['lastEvaluated'] == '2024-03-04'


def test_missing_document_is_skipped() -> None:
    assert parse_esummary_payload(_load('esummary_missing.json')) == []
    assert parse_summary_document({'uid': '9', 'error': 'cannot get document summary'}) is None


def test_no_conflicts_review_status_is_not_a_conflict() -> None:
    row = parse_summary_document(
        {
            'uid': '42',
            'title': 'NM_000546.6(TP53):c.215C>G (p.Pro72Arg)',
            'obj_type': 'single nucleotide variant',
            'genes': [{'symbol': 'TP53', 'geneid': '7157'}],
            'germline_classification': {
                'description': 'Benign',
                'last_evaluated': '1/01/01 00:00',
                'review_status': 'criteria provided, multiple submitters, no conflicts',
                'trait_set': [],
            },
            'variation_set': [
                {
                    'variation_loc': [
                        {
                            'status': 'current',
                            'assembly_name': 'GRCh37',
                            'chr': '17',
                            'start': '7579472',
                            'stop': '7579472',
                        }
                    ]
                }
            ],
        }
    )
    assert row is not None
    assert row['submitterConflict'] is False
    assert 'lastEvaluated' not in row
    assert row['assembly'] == 'GRCh37'
    assert row['clinicalSignificance'] == 'Benign'


def test_empty_germline_description_falls_back_to_oncogenicity() -> None:
    row = parse_summary_document(
        {
            'uid': 7,
            'title': 'a record',
            'germline_classification': {'description': '', 'review_status': '', 'trait_set': []},
            'oncogenicity_classification': {
                'description': 'Oncogenic',
                'review_status': 'criteria provided, single submitter',
                'last_evaluated': '2022/12/01 00:00',
                'trait_set': [{'trait_name': 'Neoplasm'}],
            },
        }
    )
    assert row is not None
    assert row['clinvarId'] == '7'
    assert row['clinicalSignificance'] == 'Oncogenic'
    assert row['phenotypes'] == ['Neoplasm']
    assert row['lastEvaluated'] == '2022-12-01'
    assert row['name'] == 'a record'
    assert 'hgvs' not in row
