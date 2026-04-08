"""Deterministic SQL validation for the analytics pipeline (SQLite, single table)."""

from __future__ import annotations

import re
import time

from src.gaming_schema import TABLE_NAME
from src.types import SQLValidationOutput

# Word-boundary disallowed statement types / pragmas (not inside identifiers for our schema).
# REPLACE is omitted here: SQLite's scalar REPLACE() would false-positive; block REPLACE INTO below.
_FORBIDDEN_KEYWORD = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|"
    r"TRUNCATE|VACUUM|BEGIN|COMMIT|ROLLBACK|GRANT|REVOKE|CALL|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)

_REPLACE_INTO = re.compile(r"\bREPLACE\s+INTO\b", re.IGNORECASE)

FROM_GAMING_TABLE = re.compile(
    rf"\bFROM\s+[`\"]?(?P<t>{re.escape(TABLE_NAME)})[`\"]?\b",
    re.IGNORECASE,
)


def select_reads_gaming_table(sql: str) -> bool:
    """True if SQL has a FROM clause referencing the survey table (SQLite identifier rules)."""
    return bool(FROM_GAMING_TABLE.search(sql))

# Read-only API: block obvious data-modifying intent in natural language.
_DESTRUCTIVE_PHRASES = (
    "delete all",
    "delete from",
    "drop table",
    "truncate ",
    "insert into",
    "update all",
    "update the",
    "remove all rows",
)


def destructive_question_error(question: str) -> str | None:
    q = question.lower()
    if any(p in q for p in _DESTRUCTIVE_PHRASES):
        return "Destructive or data-modifying requests are not supported for this read-only analytics API."
    return None


# Topics not present in gaming_mental_health (blocks bogus “success” on unrelated questions).
_OFF_SCHEMA_TERMS = (
    "zodiac",
    "horoscope",
    "astrology",
    "star sign",
    "blood type",
    "iq score",
    "political party",
)


def off_schema_question_error(question: str) -> str | None:
    q = question.lower()
    if any(t in q for t in _OFF_SCHEMA_TERMS):
        return "The question references fields or topics that are not available in this survey table."
    return None


def validate_sql(sql: str | None) -> SQLValidationOutput:
    """Allow single-statement SELECT or WITH (CTE) queries that read the configured survey table."""
    start = time.perf_counter()

    def done(is_valid: bool, validated: str | None, error: str | None) -> SQLValidationOutput:
        return SQLValidationOutput(
            is_valid=is_valid,
            validated_sql=validated,
            error=error,
            timing_ms=(time.perf_counter() - start) * 1000,
        )

    if sql is None:
        return done(False, None, "No SQL provided")

    text = sql.strip()
    if not text:
        return done(False, None, "No SQL provided")

    parts = [p.strip() for p in text.split(";") if p.strip()]
    if len(parts) != 1:
        return done(False, None, "Only a single SQL statement is allowed")

    stmt = parts[0]
    if not (
        re.match(r"^\s*SELECT\b", stmt, re.IGNORECASE)
        or re.match(r"^\s*WITH\b", stmt, re.IGNORECASE)
    ):
        return done(False, None, "Only SELECT or WITH (CTE) queries are permitted")

    if _FORBIDDEN_KEYWORD.search(stmt) or _REPLACE_INTO.search(stmt):
        return done(False, None, "Query contains disallowed SQL operations")

    if not FROM_GAMING_TABLE.search(stmt):
        return done(
            False,
            None,
            f"Query must read from table {TABLE_NAME!r}",
        )

    return done(True, stmt, None)
