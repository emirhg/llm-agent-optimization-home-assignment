# Solution notes

Short engineering note for the GenAI Labs analytics pipeline assignment (see [README.md](README.md)).

## What changed

### `scripts/benchmark.py`

- Replaced `result["status"]` with `result.status` when aggregating success rate.
- Minor formatting (imports and line breaks) for consistency with typical Python style.

## Why

`AnalyticsPipeline.run()` returns a `PipelineOutput` dataclass (`src/types.py`), not a mapping. Accessing `result["status"]` raises `TypeError: 'PipelineOutput' object is not subscriptable` on the first prompt, so the benchmark never printed summary JSON. Timing collection (`result.timings["total_ms"]`) was already correct because `timings` is a dict field on the dataclass.

## Measured impact

| Metric | Before (broken script) | After fix (local run) |
|--------|-------------------------|------------------------|
| Script completion | Crashes on first sample | Completes full prompt set |
| Avg latency | Not measurable | 9590 ms (1×12 prompts, OpenRouter) |
| p50 / p95 | Not measurable | 9860 ms / 11859 ms |
| Success rate | Not measurable | 0.0 (12/12 non-`success`; see fix plan) |

**Baseline reference** (README, reference hardware): avg ~2900 ms, p50 ~2500 ms, p95 ~4700 ms, ~600 tokens/request.

The post-fix run confirms the benchmark instrument runs end-to-end. The **0% success rate** on `tests/public_prompts.json` reflects current pipeline behavior (SQL generation, validation, and execution), not the benchmark accessor bug. Improving success rate, latency, and tokens is covered in the fix plan below.

_Re-run after substantive pipeline changes:_

```bash
python3 scripts/benchmark.py --runs 3
```

## Tradeoffs and next steps

- **Tradeoff:** None for this change; it is a straight bug fix aligned with the public output contract.
- **Next:** Execute the fix plan so public tests pass, token counts are real, and CHECKLIST deliverable is complete.

---

## Fix plan (remaining issues)

Prioritized for README hard requirements, public tests, and production-readiness themes.

### P0 — Blocking grading / CI

1. **Token counting** (`src/llm_client.py`): Implement usage extraction from OpenRouter chat responses in `_chat()` (and increment `llm_calls`). Required by README; `_assert_internal_eval_contract` expects non-negative integer token fields.
2. **SQL validation** (`src/pipeline.py` `SQLValidator`): Today everything is marked valid. **Public test** `test_invalid_sql_is_rejected` expects `DELETE` (and similar) to yield `invalid_sql` with `sql_validation.error`. Add allow-list (single `SELECT`), table/column checks against the gaming schema, and basic safety (no `;` chaining destructive statements, etc.).
3. **Run public tests with API key:** `python3 -m unittest discover -s tests -p "test_public.py"` and fix any failures (e.g. unanswerable handling, answer text for bad questions).

### P1 — Correctness and robustness

4. **SQL generation quality:** Improve prompts / schema injection so answerable questions produce valid SQLite against the real table (column names, aggregates). Align with hidden eval and benchmark success rate.
5. **Unanswerable path:** Ensure questions about non-existent columns (e.g. zodiac) reliably map to `unanswerable` or `invalid_sql` and answers contain user-safe language matching `test_unanswerable_prompt_is_handled`.
6. **Result validation:** Row-count sanity, empty vs error distinction, optional caps already partially applied in answer generation.

### P2 — Observability (CHECKLIST.md)

7. **Structured logging** (request id, stage, latency, status).
8. **Metrics:** Counters/histograms for stage timings and outcomes (in-process or stdout for demo).
9. **Tracing:** Optional OpenTelemetry or simple span nesting around the four stages.

### P3 — Efficiency (README + CHECKLIST)

10. **Prompt and context size:** Shorter system prompts, schema subset or cached DDL, fewer rows passed to answer step where safe.
11. **Model / parameters:** Tune `max_tokens`, temperature; document `OPENROUTER_MODEL` tradeoffs.
12. **Re-measure:** Record before/after in `CHECKLIST.md` benchmark section and refresh this file.

### P4 — Deliverables

13. **Complete `CHECKLIST.md`** with checkboxes, descriptions, benchmark table, name/date/time spent.
14. **Optional tests:** Add non-public unit tests for validator and client (without touching `tests/test_public.py`).

### Optional (bonus)

15. **Multi-turn conversation** (README optional section): design in CHECKLIST if implemented.

---

**Completed by:** Developer (assignment)  
**Date:** 2026-04-07  
**Time spent:** (update as you go)
