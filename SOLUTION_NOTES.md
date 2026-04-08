# Solution notes

Engineering summary for the GenAI Labs analytics pipeline assignment. See [README.md](README.md) and [CHECKLIST.md](CHECKLIST.md).

## What changed (high level)

| Area | Change |
|------|--------|
| Benchmark | Use `PipelineOutput.status` (dataclass field), optional `configure_logging()`, percentile formatting fix. |
| LLM client | OpenRouter `usage` → aggregated tokens + `llm_calls`; assistant content normalization; `json_object` with tiered fallbacks; resilient JSON/SQL extraction; optional repair turn when local SQL validation fails; default model `openai/gpt-4o-mini`. |
| Schema | `src/gaming_schema.py` — canonical column list for prompts. |
| SQL policy | `src/sql_validator.py` — SELECT-only, single statement, forbidden keywords, required `FROM gaming_mental_health`; NL guards for destructive and off-schema questions. |
| Pipeline | Validation-first status ordering; auto `request_id`; unknown-column DB errors → `unanswerable` + safe answer; stage `span()` wrappers; metrics + logging. |
| Observability | `src/observability.py` — logging helper, in-process counters, DEBUG spans. |
| Tests | `tests/test_sql_validator.py` (new); `tests/test_public.py` unchanged. |
| Docs | README default model note; completed CHECKLIST. |

## Why

- **Contract & grading:** `PipelineOutput` must stay typed; token fields must be real for efficiency scoring.
- **Safety:** Read-only analytics should reject DML/DDL and obvious destructive intent; off-schema topics (e.g. zodiac) should not look like successful analytics on unrelated columns.
- **Reliability:** Reasoning-heavy models often returned prose; strict `FROM` matching and repair passes prevent bogus “SQL” from prose. `gpt-4o-mini` follows `json_object` consistently; `OPENROUTER_MODEL` still overrides for experiments (including `gpt-5-nano`).
- **Operability:** Logs and counters make local runs and demos debuggable without deploying a full metrics stack.

## Measured impact

| Metric | Early benchmark (nano, pre-fix) | After work (`gpt-4o-mini`, 1×12 prompts) |
|--------|----------------------------------|-------------------------------------------|
| Benchmark runnable | No (`TypeError` on status) | Yes |
| Success rate (public prompts) | 0% | 100% |
| Avg latency | ~9590 ms | ~3890 ms |
| p50 / p95 | ~9860 / ~11859 ms | ~3969 / ~4505 ms |

README reference baseline (~2900 ms avg, ~600 tokens) used different hardware/model; treat as directional only.

## Tradeoffs

- **Default model:** `gpt-4o-mini` trades cost vs. stability for JSON SQL; nano remains available via env.
- **NL keyword guards:** Simple list for off-schema topics — extend or replace with NLP if product scope grows.
- **SQL validation:** Heuristic, not a full SQL parser — balances dependency footprint vs. coverage.
- **Retries + repair:** Extra latency and tokens on failure paths; improves pass rate on flaky generations.

## Next steps (if continuing)

- JSON Schema response format for stricter `sql` typing.
- OpenTelemetry export from `span()` data.
- Richer answer verification (e.g. row-count / aggregate consistency checks).
- Optional multi-turn context (see README optional section).

---

**Completed by:** Assignment developer  
**Date:** 2026-04-07  
**Time spent:** ~6 hours
