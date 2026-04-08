"""Single-table schema for the gaming mental health analytics pipeline."""

from __future__ import annotations

TABLE_NAME = "gaming_mental_health"

# Keep in sync with CSV/SQLite import (39 feature columns).
COLUMN_NAMES: tuple[str, ...] = (
    "age",
    "gender",
    "income",
    "daily_gaming_hours",
    "weekly_sessions",
    "years_gaming",
    "sleep_hours",
    "caffeine_intake",
    "exercise_hours",
    "stress_level",
    "anxiety_score",
    "depression_score",
    "social_interaction_score",
    "relationship_satisfaction",
    "academic_performance",
    "work_productivity",
    "addiction_level",
    "multiplayer_ratio",
    "toxic_exposure",
    "violent_games_ratio",
    "mobile_gaming_ratio",
    "night_gaming_ratio",
    "weekend_gaming_hours",
    "friends_gaming_count",
    "online_friends",
    "streaming_hours",
    "esports_interest",
    "headset_usage",
    "microtransactions_spending",
    "parental_supervision",
    "loneliness_score",
    "aggression_score",
    "happiness_score",
    "bmi",
    "screen_time_total",
    "eye_strain_score",
    "back_pain_score",
    "competitive_rank",
    "internet_quality",
)


def sql_generation_context() -> dict[str, str]:
    """Context passed into SQL generation (serializable, prompt-friendly)."""
    return {
        "table": TABLE_NAME,
        "columns": ", ".join(COLUMN_NAMES),
    }
