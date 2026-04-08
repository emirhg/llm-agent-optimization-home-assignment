from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from src.gaming_schema import sql_generation_context
from src.llm_client import OpenRouterLLMClient, build_default_llm_client
from src.observability import logger, new_request_id, record_pipeline_outcome, span
from src.sql_validator import destructive_question_error, off_schema_question_error, validate_sql
from src.types import (
    SQLExecutionOutput,
    SQLValidationOutput,
    PipelineOutput,
)


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "gaming_mental_health.sqlite"


class SQLValidationError(Exception):
    pass


class SQLiteExecutor:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def run(self, sql: str | None) -> SQLExecutionOutput:
        start = time.perf_counter()
        error = None
        rows = []
        row_count = 0

        if sql is None:
            return SQLExecutionOutput(
                rows=[],
                row_count=0,
                timing_ms=(time.perf_counter() - start) * 1000,
                error=None,
            )

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchmany(100)]
                row_count = len(rows)
        except Exception as exc:
            error = str(exc)
            rows = []
            row_count = 0

        return SQLExecutionOutput(
            rows=rows,
            row_count=row_count,
            timing_ms=(time.perf_counter() - start) * 1000,
            error=error,
        )


class AnalyticsPipeline:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, llm_client: OpenRouterLLMClient | None = None) -> None:
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)

    def run(self, question: str, request_id: str | None = None) -> PipelineOutput:
        start = time.perf_counter()
        rid = request_id or new_request_id()
        ctx = sql_generation_context()

        with span("pipeline_run", request_id=rid):
            with span("sql_generation", request_id=rid):
                sql_gen_output = self.llm.generate_sql(question, ctx)
            sql = sql_gen_output.sql

            with span("sql_validation", request_id=rid):
                v_start = time.perf_counter()
                nl_block = destructive_question_error(question) or off_schema_question_error(question)
                if nl_block:
                    validation_output = SQLValidationOutput(
                        is_valid=False,
                        validated_sql=None,
                        error=nl_block,
                        timing_ms=(time.perf_counter() - v_start) * 1000,
                    )
                    sql = None
                else:
                    validation_output = validate_sql(sql)
            if not validation_output.is_valid:
                sql = None
            elif validation_output.validated_sql is not None:
                sql = validation_output.validated_sql

            with span("sql_execution", request_id=rid):
                execution_output = self.executor.run(sql)
            rows = execution_output.rows

            with span("answer_generation", request_id=rid):
                answer_output = self.llm.generate_answer(question, sql, rows)

        # Prefer validation outcome over LLM transport errors when SQL is absent.
        if not validation_output.is_valid:
            status = "invalid_sql"
        elif sql_gen_output.sql is None and sql_gen_output.error:
            status = "unanswerable"
        elif execution_output.error:
            status = "error"
        elif sql is None:
            status = "unanswerable"
        else:
            status = "success"

        answer = answer_output.answer
        if (
            status == "error"
            and execution_output.error
            and "no such column" in execution_output.error.lower()
        ):
            status = "unanswerable"
            answer = (
                "I cannot answer this with the available table and schema. "
                "Please rephrase using known survey fields."
            )

        timings = {
            "sql_generation_ms": sql_gen_output.timing_ms,
            "sql_validation_ms": validation_output.timing_ms,
            "sql_execution_ms": execution_output.timing_ms,
            "answer_generation_ms": answer_output.timing_ms,
            "total_ms": (time.perf_counter() - start) * 1000,
        }

        total_llm_stats = {
            "llm_calls": sql_gen_output.llm_stats.get("llm_calls", 0) + answer_output.llm_stats.get("llm_calls", 0),
            "prompt_tokens": sql_gen_output.llm_stats.get("prompt_tokens", 0) + answer_output.llm_stats.get("prompt_tokens", 0),
            "completion_tokens": sql_gen_output.llm_stats.get("completion_tokens", 0) + answer_output.llm_stats.get("completion_tokens", 0),
            "total_tokens": sql_gen_output.llm_stats.get("total_tokens", 0) + answer_output.llm_stats.get("total_tokens", 0),
            "model": sql_gen_output.llm_stats.get("model", "unknown"),
        }

        record_pipeline_outcome(status)
        logger.info(
            "pipeline_done request_id=%s status=%s total_ms=%.2f llm_calls=%s tokens=%s",
            rid,
            status,
            timings["total_ms"],
            total_llm_stats["llm_calls"],
            total_llm_stats["total_tokens"],
        )

        return PipelineOutput(
            status=status,
            question=question,
            request_id=rid,
            sql_generation=sql_gen_output,
            sql_validation=validation_output,
            sql_execution=execution_output,
            answer_generation=answer_output,
            sql=sql,
            rows=rows,
            answer=answer,
            timings=timings,
            total_llm_stats=total_llm_stats,
        )
