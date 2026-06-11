"""Aggregation helpers for country and global metrics."""
import pandas as pd


def aggregate_country_metrics(df_match: pd.DataFrame) -> pd.DataFrame:
    """Aggregate match-level results to per-country metrics."""
    agg = df_match.groupby(["iso3", "country", "population"]).agg(
        total_sleep_loss_tournament=("total_sleep_loss_hours", "sum"),
        avg_sleep_loss_per_match=("total_sleep_loss_hours", "mean"),
        max_single_match_impact=("total_sleep_loss_hours", "max"),
        total_economic_loss=("economic_loss", "sum"),
        matches_analyzed=("match_id", "count"),
    ).reset_index()
    agg["avg_sleep_loss_per_capita"] = (
        agg["total_sleep_loss_tournament"] / agg["population"]
    )
    return agg


def global_metrics(df_match: pd.DataFrame) -> dict:
    return {
        "total_sleep_loss_hours_world": float(df_match["total_sleep_loss_hours"].sum()),
        "total_economic_loss": float(df_match["economic_loss"].sum()),
        "matches_analyzed": int(df_match["match_id"].nunique()),
        "countries_analyzed": int(df_match["iso3"].nunique()),
    }


def top_worst_nights(df_match: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Return the n country-match pairs with the highest total sleep loss."""
    cols = ["country", "match_id", "stage", "local_kickoff",
            "viewers", "sleep_loss_per_viewer", "total_sleep_loss_hours", "economic_loss"]
    available = [c for c in cols if c in df_match.columns]
    return (
        df_match[available]
        .nlargest(n, "total_sleep_loss_hours")
        .reset_index(drop=True)
    )


def aggregate_by_stage(df_match: pd.DataFrame) -> pd.DataFrame:
    """Return per-stage global totals."""
    return (
        df_match.groupby("stage")
        .agg(
            matches=("match_id", "nunique"),
            total_viewers=("viewers", "sum"),
            total_sleep_loss_hours=("total_sleep_loss_hours", "sum"),
            total_economic_loss=("economic_loss", "sum"),
        )
        .reset_index()
    )
