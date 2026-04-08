"""Multi-turn conversation orchestration on top of the analytics pipeline."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from src.gaming_schema import sql_generation_context
from src.llm_client import OpenRouterLLMClient, build_default_llm_client
from src.observability import logger, new_request_id, record_pipeline_outcome, span
from src.pipeline import (
    SQLiteExecutor,
    aggregate_llm_stats,
    aggregate_timings,
    resolve_status_and_answer,
)
from src.sql_validator import destructive_question_error, off_schema_question_error, validate_sql
from src.types import (
    AnswerGenerationOutput,
    PipelineOutput,
    SQLGenerationOutput,
    SQLValidationOutput,
)


class FollowupKind(Enum):
    NEW_QUERY = "new_query"
    REUSE_PRIOR_SQL = "reuse_prior_sql"


# Strong cues that the user wants a new analysis (conservative default when ambiguous).
_NEW_QUERY_MARKERS = (
    "sort by",
    "instead",
    "now use",
    "what about",
    "only ",
    "filter",
    "breakdown by",
    "break down by",
    "group by",
    "compare ",
    "how many",
    "which ",
    "show ",
    "list ",
    "top ",
    "average ",
    "mean ",
    "distribution",
)

# Follow-ups that can reuse the last result set (explain / clarify).
_REUSE_MARKERS = (
    "explain",
    "why ",
    "why?",
    "what does that mean",
    "clarify",
    "elaborate",
    "meaning of",
)


def classify_followup(message: str, *, has_successful_prior: bool) -> FollowupKind:
    """Heuristic routing: no LLM call. Defaults to NEW_QUERY when unsure."""
    if not has_successful_prior:
        return FollowupKind.NEW_QUERY
    q = message.lower()
    for m in _NEW_QUERY_MARKERS:
        if m in q:
            return FollowupKind.NEW_QUERY
    for m in _REUSE_MARKERS:
        if m in q:
            return FollowupKind.REUSE_PRIOR_SQL
    return FollowupKind.NEW_QUERY


def format_conversation_summary(session: "ConversationSession", *, last_n: int = 3) -> str:
    """Compact transcript for SQL generation (last n turns)."""
    lines: list[str] = []
    tail = session.turns[-last_n:] if session.turns else []
    for turn in tail:
        sql_hint = ""
        if turn.output.sql:
            s = turn.output.sql.replace("\n", " ").strip()
            if len(s) > 160:
                s = s[:157] + "..."
            sql_hint = f"\n  SQL: {s}"
        ans = turn.output.answer.replace("\n", " ").strip()
        if len(ans) > 200:
            ans = ans[:197] + "..."
        lines.append(f"- User: {turn.user_message}\n  Assistant: {ans}{sql_hint}")
    return "\n".join(lines) if lines else ""


@dataclass
class ConversationTurn:
    user_message: str
    output: PipelineOutput


@dataclass
class ConversationSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turns: list[ConversationTurn] = field(default_factory=list)
    max_turns: int = 10

    def last_successful(self) -> ConversationTurn | None:
        for turn in reversed(self.turns):
            if turn.output.status == "success" and turn.output.sql:
                return turn
        return None

    def append(self, turn: ConversationTurn) -> None:
        self.turns.append(turn)
        while len(self.turns) > self.max_turns:
            self.turns.pop(0)


def _stub_sql_generation(sql: str, model: str) -> SQLGenerationOutput:
    return SQLGenerationOutput(
        sql=sql,
        timing_ms=0.0,
        llm_stats={
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "model": model,
        },
        error=None,
    )


class ConversationPipeline:
    """Runs multi-turn analytics: optional reuse of prior SQL for explain-style follow-ups."""

    def __init__(self, db_path: str | Path, llm_client: OpenRouterLLMClient | None = None) -> None:
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)

    def run_turn(self, session: ConversationSession, question: str, request_id: str | None = None) -> PipelineOutput:
        start = time.perf_counter()
        rid = request_id or new_request_id()
        model = getattr(self.llm, "model", "unknown")

        prior = session.last_successful()
        kind = classify_followup(question, has_successful_prior=prior is not None)
        if kind == FollowupKind.REUSE_PRIOR_SQL and (prior is None or not prior.output.sql):
            kind = FollowupKind.NEW_QUERY

        prior_answer_for_llm: str | None = None
        if kind == FollowupKind.REUSE_PRIOR_SQL and prior is not None:
            prior_answer_for_llm = prior.output.answer

        with span("conversation_turn", request_id=rid):
            if kind == FollowupKind.REUSE_PRIOR_SQL and prior is not None:
                with span("sql_generation", request_id=rid):
                    sql = prior.output.sql
                    sql_gen_output = _stub_sql_generation(sql, model)
            else:
                ctx = dict(sql_generation_context())
                summary = format_conversation_summary(session)
                if summary:
                    ctx["conversation_summary"] = summary
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
                answer_output = self.llm.generate_answer(
                    question,
                    sql,
                    rows,
                    prior_answer=prior_answer_for_llm,
                )

        status, answer = resolve_status_and_answer(
            validation_output=validation_output,
            sql_gen_output=sql_gen_output,
            execution_output=execution_output,
            sql=sql,
            answer=answer_output.answer,
        )

        timings = aggregate_timings(
            sql_generation_ms=sql_gen_output.timing_ms,
            sql_validation_ms=validation_output.timing_ms,
            sql_execution_ms=execution_output.timing_ms,
            answer_generation_ms=answer_output.timing_ms,
            total_ms=(time.perf_counter() - start) * 1000,
        )
        total_llm_stats = aggregate_llm_stats(sql_gen_output, answer_output)

        record_pipeline_outcome(status)
        logger.info(
            "conversation_turn_done session=%s request_id=%s status=%s total_ms=%.2f",
            session.session_id,
            rid,
            status,
            timings["total_ms"],
        )

        out = PipelineOutput(
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
        session.append(ConversationTurn(user_message=question, output=out))
        return out
