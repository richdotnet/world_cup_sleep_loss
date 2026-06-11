"""Modeling utilities: calibration and validation.

Calibration scales predicted viewers to a published benchmark.
Validation runs sanity checks on model outputs.
"""
import logging
from typing import Tuple

import pandas as pd

_LOG = logging.getLogger(__name__)


def apply_calibration(
    df_match: pd.DataFrame,
    stage: str = "final",
    benchmark_viewers: float = 1_500_000_000,
) -> Tuple[pd.DataFrame, float]:
    """Scale match-level `viewers` so the given stage totals `benchmark_viewers`.

    Returns (df_calibrated, scale_factor).
    The scale factor is also written to a `calibration_scale` column for transparency.
    """
    df = df_match.copy()
    mask = df["stage"] == stage
    model_total = float(df.loc[mask, "viewers"].sum())
    scale = (benchmark_viewers / model_total) if model_total > 0 else 1.0

    df["viewers"]               = (df["viewers"].astype(float) * scale).round().astype(int)
    df["total_sleep_loss_hours"] = df["viewers"] * df["sleep_loss_per_viewer"].astype(float)
    df["lost_productive_hours"]  = df["total_sleep_loss_hours"] * df["working_pop_ratio"].astype(float)
    df["economic_loss"]          = df["lost_productive_hours"] * df["gdp_per_hour"].astype(float)
    df["calibration_scale"]      = scale

    _LOG.info(
        "Calibration: stage=%s  model_total=%.0f  benchmark=%.0f  scale=%.4f",
        stage, model_total, benchmark_viewers, scale,
    )
    return df, scale


def validate_outputs(df_match: pd.DataFrame, df_agg: pd.DataFrame, metrics: dict) -> None:
    """Run sanity checks and log warnings for suspicious results.

    Checks
    ------
    1. Qualifying countries appear in the output (non-empty result).
    2. Countries with large football cultures appear in the top 20 by sleep loss.
    3. Matches with local kickoff between 08:00-20:00 produce near-zero sleep loss.
    """
    # 1. Basic non-empty check
    n_countries = df_match["iso3"].nunique()
    n_matches   = df_match["match_id"].nunique()
    if n_countries == 0:
        _LOG.warning("[SANITY] No countries in output — check match team ISO codes.")
    elif n_matches == 0:
        _LOG.warning("[SANITY] No matches in output — check match data fetch.")
    else:
        _LOG.info("[SANITY] %d qualifying countries × %d matches — OK", n_countries, n_matches)

    # 2. Football nations in top ranks
    known_football_nations = {"BRA", "ARG", "FRA", "DEU", "ESP", "ENG", "GBR", "MEX", "COL", "PRT"}
    top20_isos = set(df_agg.nlargest(20, "total_sleep_loss_tournament")["iso3"].tolist())
    overlap = known_football_nations & top20_isos
    if len(overlap) < 2:
        _LOG.warning("[SANITY] Few major football nations in top-20 (%s) — check interest scores.", overlap)
    else:
        _LOG.info("[SANITY] Top-20 includes football nations: %s — OK", overlap)

    # 3. Matches with local kickoff strictly between sleep-window-end and 21:00
    #    should produce zero sleep loss (no overlap with 23:00-07:00 window).
    #    Parse each string individually (avoids mixed-timezone Series warning).
    daytime_mask = df_match["local_kickoff"].apply(
        lambda x: 8 <= pd.to_datetime(x).hour <= 20 if pd.notna(x) else False
    )
    if daytime_mask.any():
        daytime_loss = df_match.loc[daytime_mask, "sleep_loss_per_viewer"].max()
        if daytime_loss > 0.5:
            _LOG.warning("[SANITY] Daytime matches show sleep_loss_per_viewer=%.2f — expected ≈ 0.", daytime_loss)
        else:
            _LOG.info("[SANITY] Daytime match sleep loss ≈ 0 — OK")
