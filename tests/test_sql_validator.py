"""Unit tests for deterministic SQL validation (does not call OpenRouter)."""

from __future__ import annotations

import unittest

from src.sql_validator import destructive_question_error, off_schema_question_error, validate_sql


class SqlValidatorTests(unittest.TestCase):
    def test_allows_simple_select(self) -> None:
        sql = "SELECT gender, AVG(addiction_level) FROM gaming_mental_health GROUP BY gender"
        out = validate_sql(sql)
        self.assertTrue(out.is_valid)
        self.assertIsNotNone(out.validated_sql)

    def test_rejects_delete(self) -> None:
        out = validate_sql("DELETE FROM gaming_mental_health")
        self.assertFalse(out.is_valid)
        self.assertIsNotNone(out.error)

    def test_rejects_multi_statement(self) -> None:
        out = validate_sql("SELECT 1 FROM gaming_mental_health; DROP TABLE gaming_mental_health;")
        self.assertFalse(out.is_valid)

    def test_rejects_wrong_table(self) -> None:
        out = validate_sql("SELECT * FROM other_table")
        self.assertFalse(out.is_valid)

    def test_destructive_question_phrase(self) -> None:
        err = destructive_question_error("Please delete all rows from the gaming_mental_health table")
        self.assertIsNotNone(err)

    def test_off_schema_question(self) -> None:
        err = off_schema_question_error("Which zodiac sign has the highest stress score?")
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
