"""Pipeline configuration.

All real-data parameters.  Override any key by passing a modified dict to the pipeline.

API tokens
----------
Set FOOTBALL_DATA_API_TOKEN as an environment variable (or in a .env file).
The World Bank and Google Trends APIs require no token.
"""

DEFAULT_CONFIG = {
    # ── Tournament ────────────────────────────────────────────────────────────
    "competition": "WC",          # football-data.org competition code (WC = FIFA World Cup)
    "trends_terms": ["World Cup", "football"],
    "trends_timeframe": "today 12-m",

    # ── Viewer model ──────────────────────────────────────────────────────────
    # Each country only watches matches where its own national team plays.
    # viewer_ratio = interest_score × stage_multiplier  (capped at max_viewer_ratio)
    # Used when Google Trends data is unavailable for a country
    "default_interest_score": 0.10,
    # Stage-importance multipliers (defensible proxies from FIFA viewership reports)
    "stage_multipliers": {
        "group":   1.0,
        "ro16":    1.2,
        "quarter": 1.5,
        "semi":    2.0,
        "final":   3.0,
    },
    # Hard cap: no country can exceed 90 % of its population as viewers
    "max_viewer_ratio": 0.90,

    # ── Sleep model ───────────────────────────────────────────────────────────
    # Assumed match duration in hours (regulation + extra time buffer)
    "match_duration_hours": 2.0,
    # Default national sleep window (hour-of-day, 24h clock, crosses midnight)
    "sleep_start_hour": 23,
    "sleep_end_hour":    7,

    # ── Productivity & economic model ─────────────────────────────────────────
    # Research-backed: each hour of sleep loss during a work night → ~1.5 % productivity drop
    "productivity_drop_per_hour": 0.015,
    "work_hours_per_year": 2000,

    # ── Outputs ───────────────────────────────────────────────────────────────
    "output_dir": "outputs",
}
