"""World Cup Sleep Loss Pipeline — live data edition.

Run
---
    python run_simulation.py                # live data (requires FOOTBALL_DATA_API_TOKEN)
    python run_simulation.py --dev          # offline dev mode using bundled sample data

Set your football-data.org API token in one of two ways:
  PowerShell  : $env:FOOTBALL_DATA_API_TOKEN = 'your_token'
  .env file   : add  FOOTBALL_DATA_API_TOKEN=your_token  to a .env file in this folder
  Get a free token at https://www.football-data.org/client/register
"""
import argparse
import logging
import os
import sys

# Load .env file if present (python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
_LOG = logging.getLogger(__name__)

from src import config as _cfg_module
from src.simulate  import simulate
from src.aggregate import aggregate_country_metrics, global_metrics, top_worst_nights, aggregate_by_stage
from src.modeling  import apply_calibration, validate_outputs
from src.viz       import create_sleep_loss_dashboard


def main():
    parser = argparse.ArgumentParser(description="World Cup Sleep Loss Pipeline")
    parser.add_argument(
        "--dev", action="store_true",
        help="Use bundled sample data (no API keys required; for development only)"
    )
    args = parser.parse_args()

    cfg    = _cfg_module.DEFAULT_CONFIG
    outdir = cfg.get("output_dir", "outputs")
    os.makedirs(outdir, exist_ok=True)

    _LOG.info("=== World Cup Sleep Loss Pipeline ===")

    # ── Step 1: Match schedule ───────────────────────────────────────────────
    if args.dev:
        from src.data_ingest import sample_match_schedule
        matches = sample_match_schedule()
        _LOG.info("DEV MODE: loaded %d sample matches", len(matches))
    else:
        from src.data_ingest import fetch_match_data
        _LOG.info("Step 1/4  Fetching match schedule from football-data.org…")
        try:
            matches = fetch_match_data(competition=cfg.get("competition", "WC"))
            _LOG.info("          %d matches loaded", len(matches))
        except EnvironmentError as exc:
            _LOG.error("\n%s\n", exc)
            sys.exit(1)
        except Exception as exc:
            _LOG.error("Match data fetch failed: %s", exc)
            sys.exit(1)

    if matches.empty:
        _LOG.error("No match data returned. Exiting.")
        sys.exit(1)

    # ── Step 2: Country data ─────────────────────────────────────────────────
    if args.dev:
        from src.data_ingest import sample_country_metadata
        countries = sample_country_metadata()
        _LOG.info("DEV MODE: loaded %d sample countries", len(countries))
    else:
        from src.data_ingest import fetch_country_data
        _LOG.info("Step 2/4  Fetching country data from World Bank…")
        countries = fetch_country_data()
        _LOG.info("          %d countries loaded", len(countries))

    # ── Step 3: Interest scores (Elo ratings → Google Trends fallback) ──────
    _LOG.info("Step 3/4  Fetching interest scores (World Football Elo ratings)…")
    default_interest = cfg.get("default_interest_score", 0.10)
    interest_df = None

    # Primary: World Football Elo ratings (eloratings.net) — live, no manual overrides,
    # naturally reflects post-2022 changes (Morocco surge, etc.)
    try:
        from src.data_ingest import fetch_elo_interest
        interest_df = fetch_elo_interest(default_interest=default_interest)
        if interest_df is not None and not interest_df.empty:
            _LOG.info("          Using Elo-based interest scores (%d countries)", len(interest_df))
        else:
            interest_df = None
    except Exception as exc:
        _LOG.warning("          Elo interest step failed: %s", exc)

    # Fallback: Google Trends (re-anchor mean to default_interest_score)
    if interest_df is None:
        _LOG.warning("          Falling back to Google Trends…")
        try:
            from src.trends import fetch_trends_interest
            trends_df = fetch_trends_interest(
                terms=cfg.get("trends_terms", ["World Cup", "football"]),
                timeframe=cfg.get("trends_timeframe", "today 12-m"),
            )
            if trends_df is not None and not trends_df.empty:
                # Re-anchor: preserve relative rankings, fix absolute level
                mean_score = trends_df["interest_score"].mean()
                if mean_score > 0:
                    trends_df["interest_score"] = (
                        trends_df["interest_score"] / mean_score * default_interest
                    )
                interest_df = trends_df
                _LOG.info("          Google Trends fallback: %d countries (re-anchored to %.2f mean)",
                          len(interest_df), default_interest)
        except Exception as exc:
            _LOG.warning("          Google Trends fallback skipped: %s", exc)

    if interest_df is not None and not interest_df.empty:
        countries = countries.merge(
            interest_df[["iso3", "interest_score"]], on="iso3", how="left"
        )
        n_enriched = countries["interest_score"].notna().sum()
        _LOG.info("          Enriched %d / %d countries with interest scores", n_enriched, len(countries))
    else:
        _LOG.warning("          All interest sources unavailable — using default_interest_score=%.2f",
                     default_interest)

    if "interest_score" not in countries.columns:
        countries["interest_score"] = default_interest
    countries["interest_score"] = countries["interest_score"].fillna(default_interest)

    # ── Step 4: Simulate + calibrate + aggregate ─────────────────────────────
    _LOG.info("Step 4/4  Running model…")
    df_match = simulate(countries, matches, cfg)
    _LOG.info("          %d country-match pairs computed", len(df_match))

    calib_cfg = cfg.get("calibration")
    if calib_cfg:
        df_match, scale = apply_calibration(
            df_match,
            stage=calib_cfg["stage"],
            benchmark_viewers=calib_cfg["benchmark_viewers"],
        )

    df_agg   = aggregate_country_metrics(df_match)
    metrics  = global_metrics(df_match)
    stage_df = aggregate_by_stage(df_match)
    worst_df = top_worst_nights(df_match, n=10)

    # ── Validation ───────────────────────────────────────────────────────────
    validate_outputs(df_match, df_agg, metrics)

    # ── Write outputs ────────────────────────────────────────────────────────
    df_match.to_csv(os.path.join(outdir, "country_match_impact.csv"), index=False)
    df_agg.to_csv(os.path.join(outdir, "country_aggregates.csv"),     index=False)
    stage_df.to_csv(os.path.join(outdir, "stage_aggregates.csv"),     index=False)
    worst_df.to_csv(os.path.join(outdir, "top_worst_nights.csv"),     index=False)

    _LOG.info(
        "\nGlobal summary:\n"
        "  Matches analysed  : %d\n"
        "  Countries in model: %d\n"
        "  Total sleep loss  : %.2f billion hours\n"
        "  Economic impact   : $%.2f billion USD",
        metrics["matches_analyzed"],
        metrics["countries_analyzed"],
        metrics["total_sleep_loss_hours_world"] / 1e9,
        metrics["total_economic_loss"] / 1e9,
    )

    # ── Dashboard ─────────────────────────────────────────────────────────────
    map_path = create_sleep_loss_dashboard(df_agg, df_match, output_dir=outdir)
    _LOG.info("Dashboard: %s", map_path)


if __name__ == "__main__":
    main()


def main_no_args():
    """Entry point for calling the pipeline from the Dash app (no argparse)."""
    import sys
    old_argv = sys.argv
    sys.argv = [sys.argv[0]]   # clear any Dash/Werkzeug arguments
    try:
        main()
    finally:
        sys.argv = old_argv
