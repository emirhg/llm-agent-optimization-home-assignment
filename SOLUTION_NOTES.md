# Solution notes

Engineering summary for the GenAI Labs analytics pipeline assignment. See [README.md](README.md) and [CHECKLIST.md](CHECKLIST.md).

## What changed (high level)

| Area | Change |
|------|--------|
| Benchmark | Use `PipelineOutput.status` (dataclass field), optional `configure_logging()`, percentile formatting fix. |
| LLM client | OpenRouter `usage` → aggregated tokens + `llm_calls`; assistant content normalization; `json_object` with tiered fallbacks; resilient JSON/SQL extraction; optional repair turn when local SQL validation fails; default model `openai/gpt-4o-mini`. |
| Schema | `src/gaming_schema.py` — canonical column list for prompts. |
| SQL policy | `src/sql_validator.py` — single-statement **SELECT or WITH (CTE)**; forbidden DDL/DML keywords; explicit **`REPLACE INTO`** block (SQLite upsert) while allowing scalar **`REPLACE()`**; required `FROM gaming_mental_health` somewhere in the statement; NL guards for destructive and off-schema questions. |
| Pipeline | Validation-first status ordering; after valid SQL, runs **`sql_validation.validated_sql`** (canonical trimmed statement); auto `request_id`; unknown-column DB errors → `unanswerable` + safe answer; stage `span()` wrappers; metrics + logging. |
| Observability | `src/observability.py` — logging helper, in-process counters, DEBUG spans. |
| Tests | `tests/test_sql_validator.py` — CTE, scalar `REPLACE()`, `REPLACE INTO`; `tests/test_conversation.py` (multi-turn, fake LLM); `tests/test_public.py` (OpenRouter integration contract). |
| Multi-turn | `src/conversation.py` — `ConversationSession`, `ConversationPipeline`, heuristic `classify_followup`, transcript in SQL context, `prior_answer` for explain-style reuse. |
| Docs | README default model note; completed CHECKLIST. |

## Why

- **Contract & grading:** `PipelineOutput` must stay typed; token fields must be real for efficiency scoring.
- **Safety:** Read-only analytics should reject DML/DDL and obvious destructive intent; off-schema topics (e.g. zodiac) should not look like successful analytics on unrelated columns.
- **Reliability:** Reasoning-heavy models often returned prose; strict `FROM` matching and repair passes prevent bogus “SQL” from prose. Valid **`WITH … SELECT`** (CTEs) must not be rejected as non-SELECT, or common “top *N* by group” answers surface as **`invalid_sql`** despite good generations. `gpt-4o-mini` follows `json_object` consistently; `OPENROUTER_MODEL` still overrides for experiments (including `gpt-5-nano`).
- **Operability:** Logs and counters make local runs and demos debuggable without deploying a full metrics stack.

## Measured impact

| Metric | Early benchmark (nano, pre-fix) | Latest (`gpt-4o-mini`, `benchmark.py --runs 3`) |
|--------|----------------------------------|--------------------------------------------------|
| Benchmark runnable | No (`TypeError` on status) | Yes |
| Success rate (public prompts) | 0% | 100% (36 samples) |
| Avg latency | ~9590 ms | ~3620 ms |
| p50 / p95 | ~9860 / ~11859 ms | ~3725 / ~4680 ms |

_Command:_ `python3 scripts/benchmark.py --runs 3` _on 2026-04-07._

README reference baseline (~2900 ms avg, ~600 tokens) used different hardware/model; treat as directional only.

## Tradeoffs

- **Default model:** `gpt-4o-mini` trades cost vs. stability for JSON SQL; nano remains available via env.
- **NL keyword guards:** Simple list for off-schema topics — extend or replace with NLP if product scope grows.
- **SQL validation:** Heuristic, not a full SQL parser — balances dependency footprint vs. coverage. CTEs are allowed by prefix check, not deep syntactic verification; `REPLACE INTO` is blocked separately from the scalar function.
- **Retries + repair:** Extra latency and tokens on failure paths; improves pass rate on flaky generations.

## Multi-turn (optional feature)

- **API:** `ConversationPipeline(db_path, llm_client).run_turn(ConversationSession(), question)` — each turn returns `PipelineOutput` and appends to the session.
- **Routing:** Phrases like “explain” / “clarify” reuse the last successful SQL (re-executed, no SQL LLM). Cues like “sort by”, “what about”, “instead” trigger a new SQL generation with `conversation_summary` in context.
- **Tests:** `python3 -m unittest tests.test_conversation` uses a fake LLM and temp SQLite (no `OPENROUTER_API_KEY`).

## Next steps (if continuing)

- JSON Schema response format for stricter `sql` typing.
- OpenTelemetry export from `span()` data.
- Richer answer verification (e.g. row-count / aggregate consistency checks).
- LLM-based intent for multi-turn when heuristics are insufficient; durable session store.

---

**Completed by:** Emir Herrera González  
**Date:** 2026-04-07  
**Time spent:** ~6 hours
