"""Shared constants for the ClinVar Variant Finder Actor."""

from __future__ import annotations

TOOL_NAME = 'clinvar-variant-finder'
CONTACT_EMAIL = 'actor@apify.local'
EUTILS_BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils'
CLINVAR_VARIATION_URL = 'https://www.ncbi.nlm.nih.gov/clinvar/variation/{clinvar_id}/'
USER_AGENT = f'{TOOL_NAME}/0.1 ({CONTACT_EMAIL})'

# NCBI asks for at most 3 requests/second without an API key and 10 with one.
# A 10% gap keeps clock skew from sitting exactly on that ceiling.
REQUESTS_PER_SECOND_NO_KEY = 3.0
REQUESTS_PER_SECOND_WITH_KEY = 10.0
RATE_SAFETY_FACTOR = 1.1

SUMMARY_BATCH_SIZE = 100
MAX_ATTEMPTS = 5
DEFAULT_MAX_RESULTS = 50
MIN_MAX_RESULTS = 1
MAX_MAX_RESULTS = 500
MAX_LIST_ITEMS = 500
MAX_QUERY_LENGTH = 4000
