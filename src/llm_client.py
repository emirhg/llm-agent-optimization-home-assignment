from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from src.sql_validator import select_reads_gaming_table, validate_sql
from src.types import SQLGenerationOutput, AnswerGenerationOutput

# Default tuned for instruction-following with json_object; override via OPENROUTER_MODEL.
DEFAULT_MODEL = "openai/gpt-4o-mini"


def _assistant_message_text(message: Any) -> str:
    """Normalize OpenRouter assistant messages (string or structured content)."""
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            else:
                txt = getattr(item, "text", None)
                if isinstance(txt, str):
                    parts.append(txt)
        joined = "\n".join(parts).strip()
        if joined:
            return joined
    reasoning = getattr(message, "reasoning", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    return ""


class OpenRouterLLMClient:
    """LLM client using the OpenRouter SDK for chat completions."""

    provider_name = "openrouter"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        try:
            from openrouter import OpenRouter
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install 'openrouter'.") from exc
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        self._client = OpenRouter(api_key=api_key)
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _chat(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        *,
        response_format: Any | None = None,
        reasoning: Any | None = None,
    ) -> str:
        send_kw: dict[str, Any] = {
            "messages": messages,
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format is not None:
            send_kw["response_format"] = response_format
        if reasoning is not None:
            send_kw["reasoning"] = reasoning
        res = self._client.chat.send(**send_kw)

        self._stats["llm_calls"] += 1
        usage = getattr(res, "usage", None)
        if usage is not None:
            pt = int(getattr(usage, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage, "completion_tokens", 0) or 0)
            tt = int(getattr(usage, "total_tokens", 0) or 0)
            if tt == 0:
                tt = pt + ct
            self._stats["prompt_tokens"] += pt
            self._stats["completion_tokens"] += ct
            self._stats["total_tokens"] += tt

        choices = getattr(res, "choices", None) or []
        if not choices:
            raise RuntimeError("OpenRouter response contained no choices.")
        msg = getattr(choices[0], "message", None)
        text = _assistant_message_text(msg)
        if not text:
            raise RuntimeError("OpenRouter response contained no text content.")
        return text

    @staticmethod
    def _extract_sql(text: str) -> str | None:
        raw = text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```\s*$", "", raw).strip()

        if raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = json.loads(raw)
                sql = parsed.get("sql")
                if isinstance(sql, str) and sql.strip():
                    return sql.strip()
            except json.JSONDecodeError:
                pass

        decoder = json.JSONDecoder()
        pos = 0
        while True:
            i = raw.find("{", pos)
            if i < 0:
                break
            try:
                obj, _ = decoder.raw_decode(raw, i)
            except json.JSONDecodeError:
                pos = i + 1
                continue
            if isinstance(obj, dict):
                sql = obj.get("sql")
                if isinstance(sql, str) and sql.strip():
                    return sql.strip()
            pos = i + 1

        lower = raw.lower()
        idx = lower.rfind("select ")
        if idx >= 0:
            candidate = raw[idx:].strip()
            if len(candidate) > 2000 or "**" in candidate or candidate.count("\n\n") > 5:
                return None
            stmt = candidate.split(";")[0].strip()
            if (
                stmt.lower().startswith("select")
                and "from" in stmt.lower()
                and select_reads_gaming_table(stmt)
            ):
                return stmt
        return None

    def generate_sql(self, question: str, context: dict) -> SQLGenerationOutput:
        table = context.get("table", "gaming_mental_health")
        columns = context.get("columns", "")
        system_prompt = (
            "You are a SQLite analytics assistant. "
            f"The only table is {table!r}. Use exact column names from the schema. "
            "Reply with one JSON object only — no markdown fences, no commentary, no reasoning. "
            f'Shape: {{"sql": "SELECT ... FROM {table} ..."}}. '
            "The sql field must be a single valid SQLite SELECT against that table."
        )
        user_prompt = (
            f"Table {table} columns:\n{columns}\n\n"
            f"Question:\n{question}\n\n"
            "Return JSON: {\"sql\": \"...\"}"
        )

        start = time.perf_counter()
        error = None
        sql = None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            from openrouter.components import FormatJSONObjectConfig, Reasoning

            modes: list[dict[str, Any]] = [
                {"response_format": FormatJSONObjectConfig(type="json_object")},
                {
                    "response_format": FormatJSONObjectConfig(type="json_object"),
                    "reasoning": Reasoning(effort="none"),
                },
                {},
            ]
            for attempt in range(10):
                max_tokens = 512 if attempt < 4 else 1024
                text = ""
                for extra in modes:
                    try:
                        text = self._chat(messages, 0.0, max_tokens, **extra)
                        break
                    except Exception:
                        continue
                sql = self._extract_sql(text)
                if sql:
                    break
                if attempt >= 3:
                    time.sleep(0.3 * (attempt - 2))
        except Exception as exc:
            error = str(exc)

        if sql and not error:
            vr = validate_sql(sql)
            if not vr.is_valid:
                fix_user = (
                    f"Validator rejected the SQL ({vr.error!s}). "
                    f"Emit one JSON object with a corrected \"sql\" string only. "
                    f"Table: {table}. Question:\n{question}\n\n"
                    f"Rejected SQL:\n{sql[:2000]}"
                )
                repair_messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": fix_user},
                ]
                try:
                    from openrouter.components import FormatJSONObjectConfig, Reasoning

                    repair_modes: list[dict[str, Any]] = [
                        {"response_format": FormatJSONObjectConfig(type="json_object")},
                        {
                            "response_format": FormatJSONObjectConfig(type="json_object"),
                            "reasoning": Reasoning(effort="none"),
                        },
                        {},
                    ]
                    for _ in range(5):
                        for extra in repair_modes:
                            try:
                                text_fix = self._chat(repair_messages, 0.0, 1200, **extra)
                            except Exception:
                                continue
                            sql2 = self._extract_sql(text_fix)
                            if not sql2:
                                continue
                            vr2 = validate_sql(sql2)
                            if vr2.is_valid and vr2.validated_sql:
                                sql = vr2.validated_sql
                                break
                        else:
                            continue
                        break
                except Exception:
                    pass

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model

        return SQLGenerationOutput(
            sql=sql,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    def generate_answer(self, question: str, sql: str | None, rows: list[dict[str, Any]]) -> AnswerGenerationOutput:
        if not sql:
            return AnswerGenerationOutput(
                answer="I cannot answer this with the available table and schema. Please rephrase using known survey fields.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )
        if not rows:
            return AnswerGenerationOutput(
                answer="Query executed, but no rows were returned.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )

        system_prompt = (
            "You are a concise analytics assistant. "
            "Use only the provided SQL results. Do not invent data."
        )
        user_prompt = (
            f"Question:\n{question}\n\nSQL:\n{sql}\n\n"
            f"Rows (JSON):\n{json.dumps(rows[:30], ensure_ascii=True)}\n\n"
            "Write a concise answer in plain English."
        )

        start = time.perf_counter()
        error = None
        answer = ""

        try:
            answer = self._chat(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2,
                max_tokens=220,
            )
        except Exception as exc:
            error = str(exc)
            answer = f"Error generating answer: {error}"

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model

        return AnswerGenerationOutput(
            answer=answer,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    def pop_stats(self) -> dict[str, Any]:
        out = dict(self._stats or {})
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return out


def build_default_llm_client() -> OpenRouterLLMClient:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required.")
    return OpenRouterLLMClient(api_key=api_key)
