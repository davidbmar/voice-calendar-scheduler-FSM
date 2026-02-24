# Session

Session-ID: S-2026-02-24-0441-csv-pipeline-rag-metadata
Title: CSV Import Pipeline + RAG Metadata Support
Date: 2026-02-24
Author: Claude

## Goal

Make the apartment search tool a complete, working example by:
1. Adding structured record ingestion with metadata to the generic RAG service
2. Building a CSV import pipeline to load real Kaggle apartment data
3. Fixing docker-compose to point at the actual RAG service

## Context

The `search_listings` tool had two problems: (1) only 10 hand-crafted sample listings, and (2) the RAG service didn't support the `/ingest` endpoint or metadata that `listings/ingest.py` expects. The existing ingest script calls `POST /ingest` with `{id, text, metadata}` — but that endpoint didn't exist in the generic RAG.

## Plan

- **Part A (RAG repo)**: Add metadata column to LanceDB schema, add IngestRequest/Response models, add `/ingest` and `/ingest/batch` endpoints, write tests
- **Part B (FSM repo)**: Build CSV import pipeline with configurable column mapping, update ingest.py with batch support, write tests
- **Part C**: Fix docker-compose.yml to point at actual RAG service

## Changes Made

### RAG repo (`2026-feb-voice-optimal-RAG`)
- `vector_store.py` — Added `metadata` string column to schema, schema migration for missing column, metadata JSON parsing in search results
- `document_pipeline.py` — Added `"metadata": ""` to upload records for backward compatibility
- `models.py` — Added `IngestRequest`, `IngestResponse`, `BatchIngestRequest`, `BatchIngestResponse`; extended `QueryResult` with `metadata: dict`
- `app.py` — Added `POST /ingest` (single record, no chunking) and `POST /ingest/batch` (bulk embed+store) endpoints
- `tests/test_ingest.py` — 5 new tests: single ingest with/without metadata, batch ingest, roundtrip query, delete

### FSM repo (`voice-calendar-scheduler-FSM`)
- `listings/import_csv.py` — New: CSV import pipeline with configurable column mapping, amenities parsing, pet-friendly detection, parking/laundry derivation
- `listings/data/column_mappings/kaggle_shashanks1202.json` — New: column mapping for Kaggle apartment rent dataset
- `listings/ingest.py` — Updated: batch support (groups of 50), `--data` CLI arg, `--help` support
- `tests/test_import_csv.py` — 25 new tests covering all parsers, transforms, CSV import, filtering, column mapping
- `docker-compose.yml` — Fixed: points at `../2026-feb-voice-optimal-RAG`, port mapping `8000:8100`, `start_period: 30s`

## Decisions Made

1. **No chunking for structured records**: The `/ingest` endpoint stores each record as a single unit (chunk_index=0) rather than splitting text into chunks. This keeps metadata aligned 1:1 with search results.
2. **Configurable column mapping**: Instead of hardcoding Kaggle column names, mappings are loaded from JSON config files. This makes the pipeline reusable for other CSV datasets.
3. **Derived fields**: `parking` and `laundry` are derived from amenities/description text using keyword matching, since they don't exist in the Kaggle dataset.
4. **Temp dir for tests**: RAG ingest tests use a temporary LanceDB directory to avoid interference between test runs.
5. **Batch size of 50**: Balances memory usage with throughput for the batch ingest endpoint.

## Open Questions

- Kaggle CSV needs to be downloaded manually (requires Kaggle account). The pipeline is ready but `listings/data/austin_apartments.json` doesn't exist yet until someone runs the import.
- Pre-existing test failures in `test_workflow.py` and `test_branching_fsm.py` (6 failures about FSM state count/naming) — unrelated to this work.

## Links

Commits:
- (pending commit)

PRs:
- (none yet)

ADRs:
- (none — extending existing patterns, no new architectural decisions)
