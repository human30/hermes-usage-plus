# usage-plus official pricing catalog

This module maintains official-source prices for the Usage+ plugin. `worker.py` integrates the catalog; `scripts/install-schedule.ps1` installs the Windows daily refresh.

## Interface

- `refresh_prices(home: pathlib.Path, *, sources=None) -> dict`: explicitly fetch official sources and atomically replace `home/cache/usage-plus/prices.json`.
- `lookup_price(home, provider, model)`: cache-only exact lookup, returning a namespace with Decimal-or-None input/output/cache-read/cache-write USD/1M-token fields and request_cost USD/request, official source/conditions, fetched-date pricing_version, and optional tier_threshold_tokens. Missing pairs return None. No network.
- `status(home)`: cache-only updated_at, sources, price_count, healthy_sources, error_sources and stale. Source checked_at is the last successful validation, attempted_at the latest attempt. Stale means checked_at is absent or more than 24 hours old. updated_at records refresh attempts, NOT price freshness.

Only explicit provider alias: openai-codex -> openai. Its source label says "OpenAI API equivalent reference, not billed". Unknown/custom routes, Infron, Hive and OpenCode Go do not borrow native vendor or Console tariffs. No model suffix stripping, dated snapshot inference, fuzzy matching or cross-provider fallback.

For deterministic offline tests, sources is a mapping of registered source IDs to raw UTF-8 bytes/str or Exception values. Explicit {} performs no network. Arbitrary endpoints and callable loaders are not accepted. Injection is a trusted testing seam, not proof that a supplied fixture was fetched officially; do not use it to load unverified production prices.

## Official sources and deterministic parsers

1. Nous: https://inference-api.nousresearch.com/v1/models
2. OpenRouter: https://openrouter.ai/api/v1/models
   Parse data[].id and pricing.prompt/completion/input_cache_read/input_cache_write as USD/token; multiply by 1,000,000 using Decimal. request, if explicitly published, stays USD/request. Missing/null rates remain None; explicit zero stays zero. Alternative 1h cache-write prices are not substituted for the default write rate. Tool/media charges are excluded and labelled. Recognized min_prompt_tokens overrides retain the published base tier and threshold, never select a tier using aggregate usage. Time-of-day and unknown override conditions are skipped. OpenRouter -1/-1 dynamic-router sentinel rows stay unknown. Unknown pricing schemas, duplicates, malformed/negative/nonfinite rates and incomplete catalogs fail closed.
3. OpenAI: https://developers.openai.com/api/docs/pricing
   Strictly parse the official TextTokenPricingTables Astro serialized JSON in mainContent/content-switcher-latest-pricing's standard pane, with the nearby "Prices per 1M tokens." unit declaration. Typed scalar/array tags and four/five-column layouts are validated. Standard input/cached-input/output and explicit 30m cache writes are supported; 12h writes are not derived. Explicit (<NK context length) labels retain a base-tier condition and threshold. Batch/Flex/Fast/Ultrafast and unrelated audio/image/fine-tuning/specialized tables are not used. Initial coverage is the main text table, not every OpenAI model.
4. Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
   Require USD currency, model-pricing section and exact Name/Input/Output/5m writes/1h writes/Hits and refreshes headers. Strict $.../MTok cells; explicitly expressed "for prompts up to N tokens" is a labelled base tier. Cache reads map to Hits and refreshes; writes use the published 5m rate, not 1h. Eleven explicit display-name/API-ID mappings were checked against https://platform.claude.com/docs/en/models/overview. Unmapped names and continuation rows are skipped; dated snapshots are not invented. Regional premiums/tools are excluded and labelled.

No models.dev, third-party prices, model-by-model crawling, AI/LLM parsing, imports from Hermes's price snapshots, credential reads, installs or inference calls.

## Safety and failure behavior

- stdlib only. Fixed HTTPS endpoints; no Authorization/cookies/environment proxies. Redirects must stay on the exact official host, HTTPS port 443, without userinfo/query/fragment; at most three redirects.
- Four isolated, hidden, killable Python fetch subprocesses start together. Socket timeout 8s, supervised communicate limit 12s/source, shared fetch deadline 40s, total processing budget 55s with parsing deadline checks. Processes are killed/reaped on timeout. Parent's 90s process cap remains defense in depth against OS/filesystem stalls.
- Response cap 4 MiB/source, cache read cap 16 MiB, bulk catalog cap 4,096 entries/source. Reject unexpected MIME/content encoding. No automatic retries or per-model requests.
- Per-source success replaces only that source's rows. Failure preserves its last-good rows, checked_at, pricing_version and digest while recording an attempted_at and fixed sanitized error code. Other sources still update. A successful source removes rows no longer in its supported current catalog.
- Same-directory temporary JSON, flush/fsync and os.replace; bounded retries for Windows sharing violations. Failed replacement leaves the prior cache intact and removes its temporary file. Single writer is expected: parent should avoid overlapping scheduled/manual refreshes.
- Source status records raw-response SHA-256/byte count, published model_count, accepted count and skipped_count. Cache holds price/provenance fields, never raw response bodies or exception messages.
- Missing/corrupt caches are unknown, not free. Read paths do not refresh or modify files. Old successful data can be returned after failures; consumers should show freshness and source health.

## Tests and live verification

Run with an existing Python installation (no pytest dependency):

    python -B -m unittest discover -s ./tests -p test_pricing_catalog.py -v

TDD cycles were executed RED -> GREEN for normalization, last-good retention, atomic replacement, schema guards/provider isolation, deterministic documentation parsers, network limits/process cleanup, and dynamic-route sentinel handling. Fixtures contain synthetic prices only.

Live verification uses a temporary home under Hermes cache/scratch, calls the real refresh interface, reads its exact cache via lookup_price/status, then deletes that temporary home. Install the production schedule using the main README. Tests do not claim verified invoices, historical request-tier accuracy, full upstream coverage or end-to-end scheduling.
