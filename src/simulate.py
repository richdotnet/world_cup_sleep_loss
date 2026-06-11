"""Simulation logic for estimating per-country sleep loss per match.

A country only accumulates sleep loss for matches in which its own national team plays.
Countries whose teams did not qualify contribute zero sleep loss.
Matches where both teams are still TBD (future knockout fixtures) are skipped.

Viewer model:
    viewer_ratio = interest_score × stage_multiplier

Sleep loss is computed as the overlap (in hours) between the match time window
(kickoff → kickoff + match_duration) and a country's sleep window (default 23:00–07:00).
"""
from datetime import datetime, timezone, timedelta, time
import pytz
import pandas as pd


def _sleep_overlap_hours(local_start_dt: datetime, duration_hours: float = 2.0, sleep_start_hour: int = 23, sleep_end_hour: int = 7) -> float:
    """Compute overlap in hours between the match interval and the sleep window.

    - local_start_dt: timezone-aware datetime for match kickoff in local tz
    - duration_hours: match duration (hours)
    - sleep_start_hour/sleep_end_hour: integers in [0,24) describing sleep window (may cross midnight)
    """
    if local_start_dt.tzinfo is None:
        # treat as naive local time
        raise ValueError("local_start_dt must be timezone-aware")

    match_end = local_start_dt + timedelta(hours=duration_hours)
    total_overlap = 0.0

    # consider the sleep window for the day before, the day of, and the day after kickoff
    for day_offset in (-1, 0, 1):
        day = (local_start_dt + timedelta(days=day_offset)).date()
        if sleep_start_hour < sleep_end_hour:
            window_start = datetime.combine(day, time(hour=sleep_start_hour)).replace(tzinfo=local_start_dt.tzinfo)
            window_end = datetime.combine(day, time(hour=sleep_end_hour)).replace(tzinfo=local_start_dt.tzinfo)
        else:
            # window crosses midnight
            window_start = datetime.combine(day, time(hour=sleep_start_hour)).replace(tzinfo=local_start_dt.tzinfo)
            window_end = datetime.combine(day + timedelta(days=1), time(hour=sleep_end_hour)).replace(tzinfo=local_start_dt.tzinfo)

        latest_start = max(local_start_dt, window_start)
        earliest_end = min(match_end, window_end)
        delta = (earliest_end - latest_start).total_seconds()
        if delta > 0:
            total_overlap += delta / 3600.0

    # cap overlap to match duration
    return min(total_overlap, duration_hours)


def simulate(country_df, match_df, config, scenario: str = "baseline"):
    """Simulate sleep loss for each country, only for matches their national team plays in.

    - Countries not in any match (didn't qualify) produce no rows.
    - Matches where both teams are TBD are skipped entirely.
    - Viewer model: viewer_ratio = interest_score × stage_multiplier  (capped at max_viewer_ratio)
    """
    rows = []

    match_duration = float(config.get("match_duration_hours", 2.0))

    # ── Build set of qualifying ISO3s (teams that appear in at least one match) ──
    qualified_isos = set()
    for _, m in match_df.iterrows():
        for key in ("team1_iso3", "team2_iso3"):
            v = m.get(key)
            if v and pd.notna(v):
                qualified_isos.add(v)

    # Only iterate over countries whose teams qualified
    playing_countries = country_df[country_df["iso3"].isin(qualified_isos)].copy()

    for _, c in playing_countries.iterrows():
        for _, m in match_df.iterrows():
            team_isos  = {v for k in ("team1_iso3", "team2_iso3") if pd.notna(v := m.get(k)) and v}
            team_names = {v for k in ("team1", "team2")         if pd.notna(v := m.get(k)) and v}

            # Skip fixtures where both teams are still TBD
            if not team_isos:
                continue

            # Only simulate if this country's team is actually playing
            playing = (c["iso3"] in team_isos) or (c["country"] in team_names)
            if not playing:
                continue

            # kickoff UTC -> local
            utc = pd.to_datetime(m["utc_kickoff"])
            if utc.tzinfo is None:
                utc = utc.tz_localize("UTC")
            try:
                tz_name = c.get("timezone") if c.get("timezone") else "UTC"
                local = utc.tz_convert(pytz.timezone(tz_name))
            except Exception:
                local = utc.tz_convert(pytz.timezone("UTC"))

            # interest score
            if "interest_score" in c and pd.notna(c["interest_score"]):
                interest_score = float(c["interest_score"])
            elif "base_interest" in c and pd.notna(c["base_interest"]):
                interest_score = float(c["base_interest"])
            else:
                interest_score = float(config.get("default_interest_score", 0.10))

            # stage multiplier
            stage = m.get("stage", "group")
            match_importance = config.get("stage_multipliers", {}).get(stage, 1.0)

            # viewer model: own-team match, no external boost needed
            viewer_ratio = interest_score * match_importance
            viewer_ratio = min(viewer_ratio, config.get("max_viewer_ratio", 0.9))

            population = int(c["population"]) if pd.notna(c.get("population")) else 0
            viewers = int(population * viewer_ratio)

            # sleep overlap
            sleep_start = int(c.get("sleep_start_hour") or config.get("sleep_start_hour", 23))
            sleep_end   = int(c.get("sleep_end_hour")   or config.get("sleep_end_hour",   7))
            sleep_loss_per_viewer = _sleep_overlap_hours(
                local, duration_hours=match_duration,
                sleep_start_hour=sleep_start, sleep_end_hour=sleep_end,
            )
            total_sleep_loss_hours = viewers * sleep_loss_per_viewer

            working_pop_ratio = c.get("working_pop_ratio", config.get("default_working_pop_ratio", 0.6))
            lost_productive_hours = total_sleep_loss_hours * working_pop_ratio

            gdp_per_capita = c.get("gdp_per_capita", 0.0) or 0.0
            gdp_per_hour = gdp_per_capita / config.get("work_hours_per_year", 2000)
            economic_loss = lost_productive_hours * gdp_per_hour

            rows.append({
                "country": c["country"],
                "iso3": c["iso3"],
                "match_id": m["match_id"],
                "utc_kickoff": utc.isoformat(),
                "local_kickoff": local.isoformat(),
                "timezone": c.get("timezone"),
                "population": population,
                "viewers": int(viewers),
                "viewer_ratio": float(viewer_ratio),
                "stage": stage,
                "match_importance": float(match_importance),
                "base_interest": float(interest_score),
                "sleep_loss_per_viewer": float(sleep_loss_per_viewer),
                "total_sleep_loss_hours": float(total_sleep_loss_hours),
                "working_pop_ratio": float(working_pop_ratio),
                "lost_productive_hours": float(lost_productive_hours),
                "gdp_per_capita": float(gdp_per_capita),
                "gdp_per_hour": float(gdp_per_hour),
                "economic_loss": float(economic_loss),
            })

    df = pd.DataFrame(rows)
    return df
