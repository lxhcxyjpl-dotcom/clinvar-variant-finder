## What does ClinVar Variant Finder do?

**ClinVar Variant Finder** searches [NCBI ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/) through the official [E-utilities API](https://www.ncbi.nlm.nih.gov/books/NBK25497/) (`db=clinvar`) and saves **one flat row per variant**. It is built for clinical interpretation review, gene-panel annotation, and HGVS or rsID lookup.

The Actor calls `esearch` and `esummary` on `eutils.ncbi.nlm.nih.gov`. It does **not** download or parse ClinVar HTML, and it does not need a ClinVar login. An NCBI API key is optional.

## Why use ClinVar Variant Finder?

ClinVar is the public archive of relationships between human variation and clinical interpretations. Teams use it to:

- Pull pathogenic and likely pathogenic variants for a gene panel
- Resolve an HGVS expression, rsID, or ClinVar variation ID to one stable row
- See review status, conflicting submitter classifications, and phenotype names next to coordinates
- Feed a sheet, warehouse, or downstream Actor without writing an E-utilities client

Because the source is the NCBI API, the run does not depend on the ClinVar website layout, a browser, or proxy rotation. You can schedule it, call it from the Apify API, or connect it to Make, Zapier, or n8n. Each saved variant is one dataset item.

## What data can ClinVar Variant Finder extract?

Empty fields are left out of the row. `submitterConflict` is always present.

| Field | What it contains |
| --- | --- |
| `clinvarId` | ClinVar variation ID |
| `name` | ClinVar record title, often the preferred HGVS name |
| `geneSymbol`, `geneId` | Primary gene. `geneSymbols` is added when more than one gene is listed |
| `clinicalSignificance` | Germline classification when ClinVar provides one, otherwise oncogenicity or clinical impact |
| `reviewStatus`, `lastEvaluated` | How the classification was reviewed, and the evaluation date (`YYYY-MM-DD`) |
| `molecularConsequence`, `variationType` | Sequence ontology consequences and the variant type |
| `hgvs` | HGVS-like names from the title, c. change, and aliases. Canonical SPDI is `canonicalSpdi` |
| `rsid` | dbSNP id (`rs…`). Extra ids are listed in `rsids` |
| `phenotypes` | Condition or trait names from the classification |
| `chromosome`, `start`, `stop`, `assembly` | Preferred GRCh38 location when ClinVar has one, otherwise GRCh37 |
| `submitterConflict` | `true` when the classification or review status says submitters disagree |
| `clinvarUrl` | `https://www.ncbi.nlm.nih.gov/clinvar/variation/{id}/` |
| `accession`, `accessionVersion` | VCV accession |

Coordinates and classifications are copied from the E-utilities document summary. The Actor does not recompute them.

## How to search ClinVar variants

1. Open the Actor and choose **Try it** or **Start**.
2. Enter a free-text `query` (gene, disease, or phenotype), or fill **Gene symbols**, **rsIDs**, **HGVS expressions**, or **ClinVar variation IDs**. One of those is required.
3. Optionally restrict **Clinical significance**. Leave it empty to keep every classification.
4. Set **Maximum variants** (1–500, default 50). NCBI chooses the order, usually the most recently indexed matches, and the Actor stops at this cap after duplicates are removed.
5. Optionally paste an **NCBI API key**. Without it the Actor stays near 3 requests per second. With it, about 10. The key is sent only to NCBI.
6. Run the Actor and open the dataset. Download JSON, CSV, Excel, or XML, or call the dataset API.

A gene symbol such as `BRCA1` is sent as `"BRCA1"[gene]`. An rsID is sent as `rs80357906[vrid]`. An HGVS expression is sent as a ClinVar variant-name query. A variation id `17661` or `VCV000017661` is sent as `17661[vid]`. Significance filters use ClinVar property terms (`clinsig_pathogenic[prop]`, `clinsig_vus[prop]`, and the matching benign terms). **Conflicting classifications of pathogenicity** uses the review-status query ClinVar actually indexes. These field tags come from ClinVar's Entrez index, not from HTML filters.

If you set more than one mode (for example a disease query and a gene list), each mode is searched and the rows are merged. The same variation id is saved once.

## How much will it cost to get ClinVar variants?

On the Apify Store this Actor is priced as pay per result: about **$0.003 per variant** written to the dataset. A run that saves 50 variants is about $0.15 in result charges, before any platform plan discounts. Rows that are not saved are not result rows. Raising `maxResults` raises the maximum number of charged rows.

The price is not hard-coded in the Actor. Store billing uses the normal dataset-item event. An NCBI API key does not change the per-result price. It only lets the run talk to NCBI a bit faster. You can create a key in your NCBI account settings.

## Input

See the **Input** tab for every field, the default of 50 variants, and example values.

| Field | Required | Notes |
| --- | --- | --- |
| `query` | One of the search fields | Free text or an Entrez expression. Example: `breast cancer` or `BRCA1[gene]` |
| `geneSymbols` | One of the search fields | List of symbols, for example `BRCA1`, `TP53`, `HLA-A` |
| `rsids` | One of the search fields | `rs80357906`. The `rs` prefix is optional |
| `hgvs` | One of the search fields | `NM_007294.4:c.181T>G` |
| `clinvarIds` | One of the search fields | `17661` or `VCV000017661` |
| `clinicalSignificance` | No | Pathogenic, Likely pathogenic, Uncertain significance, Likely benign, Benign, Conflicting classifications of pathogenicity. `VUS` is accepted as an alias of Uncertain significance |
| `maxResults` | No | Integer from 1 to 500. Default 50 |
| `ncbiApiKey` | No | Secret. Never stored in the dataset or the run overview |

`query` is required only when the identifier lists are empty. A significance filter by itself is not a search.

## Output

You can download the dataset as JSON, CSV, Excel, XML, or RSS. The key-value record `OUTPUT` counts how many rows were saved and what each ClinVar search reported. It does not contain the API key.

```json
{
  "clinvarId": "17661",
  "name": "NM_007294.4(BRCA1):c.181T>G (p.Cys61Gly)",
  "geneSymbol": "BRCA1",
  "geneId": "672",
  "clinicalSignificance": "Pathogenic",
  "reviewStatus": "reviewed by expert panel",
  "lastEvaluated": "2015-08-10",
  "molecularConsequence": ["missense variant", "non-coding transcript variant"],
  "variationType": "single nucleotide variant",
  "hgvs": ["NM_007294.4(BRCA1):c.181T>G (p.Cys61Gly)", "c.181T>G"],
  "rsid": "rs28897672",
  "phenotypes": ["Breast-ovarian cancer, familial, susceptibility to, 1"],
  "chromosome": "17",
  "start": 43106487,
  "stop": 43106487,
  "assembly": "GRCh38",
  "submitterConflict": false,
  "clinvarUrl": "https://www.ncbi.nlm.nih.gov/clinvar/variation/17661/",
  "accession": "VCV000017661",
  "accessionVersion": "VCV000017661.165"
}
```

The overview table in the output tab shows the review fields, coordinates, and the ClinVar link. The details view adds HGVS, consequences, phenotypes, and accessions. A real `hgvs` list also includes matching aliases, such as protein accessions. The example above keeps the two primary strings so it is easy to read.

## Tips

- Start with `maxResults` at 10 while you confirm the gene list or HGVS strings, then raise it.
- Prefer gene symbols, rsIDs, HGVS, or variation IDs when you need a specific variant. Use `query` for a disease or phenotype phrase.
- Quote nothing yourself for gene symbols. The Actor quotes symbols such as `HLA-A` so the hyphen is not read as an Entrez operator.
- Add an NCBI API key for larger panels. The Actor still waits between calls and retries HTTP 429 and 5xx with backoff.
- Every request sends `tool=clinvar-variant-finder` and `email=actor@apify.local`, as NCBI asks callers to identify themselves.
- A run with no matches finishes successfully and writes `variantCount: 0`. Check the terms in `OUTPUT` if a symbol or HGVS string was rejected by ClinVar.

## How this differs from an HTML scraper

HTML scrapers read the ClinVar website. This Actor does not. It uses the documented E-utilities JSON interface described in [ClinVar programmatic access](https://www.ncbi.nlm.nih.gov/clinvar/docs/programmatic_access/) and the [E-utilities book](https://www.ncbi.nlm.nih.gov/books/NBK25497/). That keeps variation IDs, accessions, and classifications stable when the website changes, and it follows NCBI's request-rate rules instead of crawling pages.

## FAQ, disclaimers, and support

### Is this medical advice?

No. ClinVar data is for **research and education**. Classifications change, submitters disagree, and a row in this dataset is not a diagnosis, a treatment recommendation, or a substitute for a clinical laboratory report.

### Does the Actor collect private patient data?

No. It reads public ClinVar variant records through NCBI's API. It does not ask for patient names, medical record numbers, or clinical notes. Do not put patient identifiers in the search box. Your results can still name genes, phenotypes, and submitter-level review text that ClinVar publishes. Personal data is protected by the GDPR in the European Union and by other laws elsewhere. Use the output only for a legitimate purpose.

### What if ClinVar is busy?

The Actor retries rate-limit and server errors, then fails with a short status message if NCBI keeps failing. It does not scrape a backup HTML page.

### Where do I report a problem?

Use the **Issues** tab on the Actor page. For programmatic runs, use the **API** tab and the dataset items endpoint from the run.

## Local development

```bash
pip install -r requirements-dev.txt
pytest
```

`storage/key_value_stores/default/INPUT.json` is a small sample for `apify run`. Tests use fixtures and a mock HTTP client. They do not call NCBI.
