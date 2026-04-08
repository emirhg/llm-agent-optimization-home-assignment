"""Multi-turn conversation tests (no OpenRouter; no test_public)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.conversation import (
    ConversationPipeline,
    ConversationSession,
    FollowupKind,
    classify_followup,
    format_conversation_summary,
)
from src.types import AnswerGenerationOutput, SQLGenerationOutput


class FakeLLMClient:
    """Tracks calls; returns fixed valid SQL for the in-memory gaming_mental_health schema."""

    model = "fake-model"

    def __init__(self) -> None:
        self.sql_calls = 0
        self.answer_calls = 0
        self.last_prior_answer: str | None = None
        self.last_sql_context: dict[str, Any] | None = None
        self._sql = (
            "SELECT gender, AVG(addiction_level) AS avg_add FROM gaming_mental_health GROUP BY gender"
        )
        self._answer = "Males average 3.0, females 2.0."

    def generate_sql(self, question: str, context: dict) -> SQLGenerationOutput:
        self.sql_calls += 1
        self.last_sql_context = dict(context)
        return SQLGenerationOutput(
            sql=self._sql,
            timing_ms=1.0,
            llm_stats={
                "llm_calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": self.model,
            },
            error=None,
        )

    def generate_answer(
        self,
        question: str,
        sql: str | None,
        rows: list[dict[str, Any]],
        *,
        prior_answer: str | None = None,
    ) -> AnswerGenerationOutput:
        self.answer_calls += 1
        self.last_prior_answer = prior_answer
        body = self._answer
        if prior_answer:
            body = f"(follow-up) {self._answer}"
        return AnswerGenerationOutput(
            answer=body,
            timing_ms=1.0,
            llm_stats={
                "llm_calls": 1,
                "prompt_tokens": 8,
                "completion_tokens": 4,
                "total_tokens": 12,
                "model": self.model,
            },
            error=None,
        )

    def pop_stats(self) -> dict[str, Any]:
        return {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class ClassifyFollowupTests(unittest.TestCase):
    def test_no_prior_always_new(self) -> None:
        self.assertEqual(
            classify_followup("Explain this", has_successful_prior=False),
            FollowupKind.NEW_QUERY,
        )

    def test_explain_reuses_when_prior(self) -> None:
        self.assertEqual(
            classify_followup("Explain the highest value", has_successful_prior=True),
            FollowupKind.REUSE_PRIOR_SQL,
        )

    def test_sort_instead_new_query(self) -> None:
        self.assertEqual(
            classify_followup("Now sort by anxiety_score instead", has_successful_prior=True),
            FollowupKind.NEW_QUERY,
        )

    def test_what_about_new_query(self) -> None:
        self.assertEqual(
            classify_followup("What about males specifically?", has_successful_prior=True),
            FollowupKind.NEW_QUERY,
        )

    def test_default_new_query(self) -> None:
        self.assertEqual(
            classify_followup("Thanks", has_successful_prior=True),
            FollowupKind.NEW_QUERY,
        )


class ConversationSummaryTests(unittest.TestCase):
    def test_format_summary_empty(self) -> None:
        s = ConversationSession()
        self.assertEqual(format_conversation_summary(s), "")

    def test_format_summary_truncates(self) -> None:
        from src.conversation import ConversationTurn
        from src.types import (
            PipelineOutput,
            SQLExecutionOutput,
            SQLGenerationOutput,
            SQLValidationOutput,
        )

        sg = SQLGenerationOutput(sql="SELECT 1", timing_ms=0.0, llm_stats={}, error=None)
        sv = SQLValidationOutput(is_valid=True, validated_sql="SELECT 1", error=None, timing_ms=0.0)
        se = SQLExecutionOutput(rows=[], row_count=0, timing_ms=0.0, error=None)
        ag = AnswerGenerationOutput(answer="Hi " * 100, timing_ms=0.0, llm_stats={}, error=None)
        po = PipelineOutput(
            status="success",
            question="Q",
            request_id="r",
            sql_generation=sg,
            sql_validation=sv,
            sql_execution=se,
            answer_generation=ag,
            sql="SELECT 1",
            rows=[],
            answer=ag.answer,
            timings={},
            total_llm_stats={},
        )
        session = ConversationSession()
        session.append(ConversationTurn("Q1", po))
        text = format_conversation_summary(session)
        self.assertIn("User: Q1", text)
        self.assertTrue(len(text) < len("Hi " * 100) + 50)


class ConversationPipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._fd, self._db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(self._fd)
        self.addCleanup(lambda: os.unlink(self._db_path))
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE gaming_mental_health (gender TEXT, addiction_level REAL, anxiety_score REAL)"
        )
        conn.execute(
            "INSERT INTO gaming_mental_health (gender, addiction_level, anxiety_score) VALUES ('M', 3, 2), ('F', 2, 5)"
        )
        conn.commit()
        conn.close()
        self.fake = FakeLLMClient()
        self.pipeline = ConversationPipeline(Path(self._db_path), llm_client=self.fake)

    def test_first_turn_success_and_sql_called(self) -> None:
        session = ConversationSession()
        out = self.pipeline.run_turn(session, "Average addiction by gender?")
        self.assertEqual(out.status, "success")
        self.assertEqual(self.fake.sql_calls, 1)
        self.assertEqual(self.fake.answer_calls, 1)
        self.assertIsNone(self.fake.last_prior_answer)
        self.assertEqual(len(session.turns), 1)

    def test_explain_follow_up_skips_sql_generation(self) -> None:
        session = ConversationSession()
        self.pipeline.run_turn(session, "Average addiction by gender?")
        sql_calls_after_first = self.fake.sql_calls
        self.pipeline.run_turn(session, "Explain the highest value in that table.")
        self.assertEqual(self.fake.sql_calls, sql_calls_after_first)
        self.assertEqual(self.fake.answer_calls, 2)
        self.assertIsNotNone(self.fake.last_prior_answer)

    def test_sort_follow_up_invokes_sql_again(self) -> None:
        session = ConversationSession()
        self.pipeline.run_turn(session, "Average addiction by gender?")
        sql_calls_after_first = self.fake.sql_calls
        self.pipeline.run_turn(session, "Now sort by anxiety_score instead.")
        self.assertGreater(self.fake.sql_calls, sql_calls_after_first)
        self.assertEqual(self.fake.answer_calls, 2)

    def test_conversation_summary_passed_on_new_query(self) -> None:
        session = ConversationSession()
        self.pipeline.run_turn(session, "First question?")
        self.pipeline.run_turn(session, "Now sort by anxiety_score instead.")
        ctx = self.fake.last_sql_context or {}
        self.assertIn("conversation_summary", ctx)
        self.assertIn("First question", ctx["conversation_summary"])

    def test_session_max_turns_trims(self) -> None:
        session = ConversationSession(max_turns=2)
        self.pipeline.run_turn(session, "Q1?")
        self.pipeline.run_turn(session, "Q2?")
        self.pipeline.run_turn(session, "Q3?")
        self.assertEqual(len(session.turns), 2)
        self.assertEqual(session.turns[0].user_message, "Q2?")
        self.assertEqual(session.turns[1].user_message, "Q3?")


if __name__ == "__main__":
    unittest.main()
