"""Rich interactive Plotly dashboard for World Cup Sleep Loss metrics.

Outputs a single self-contained HTML file with:
  - Choropleth world map with a metric dropdown (3 metrics)
  - Top-10 countries bar chart (sleep loss per capita)
  - Worst match-nights data table
"""
import os
import logging

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_LOG = logging.getLogger(__name__)


def create_sleep_loss_dashboard(
    df_agg: pd.DataFrame,
    df_match: pd.DataFrame,
    output_dir: str = "outputs",
) -> str:
    """Build and write the interactive HTML dashboard.

    Parameters
    ----------
    df_agg    : country-level aggregates from aggregate_country_metrics()
    df_match  : match-level detail from simulate()
    output_dir: destination folder

    Returns
    -------
    str : absolute path to the written HTML file
    """
    os.makedirs(output_dir, exist_ok=True)

    # ── Metric definitions ──────────────────────────────────────────────────
    METRICS = [
        {
            "col":        "total_sleep_loss_tournament",
            "label":      "Total Sleep Loss (M hrs)",
            "scale":      1e6,
            "colorscale": "Reds",
            "cbformat":   ".1f",
            "unit":       "M hrs",
        },
        {
            "col":        "avg_sleep_loss_per_capita",
            "label":      "Sleep Loss per Capita (hrs)",
            "scale":      1.0,
            "colorscale": "YlOrRd",
            "cbformat":   ".3f",
            "unit":       "hrs",
        },
        {
            "col":        "total_economic_loss",
            "label":      "Economic Loss (M USD)",
            "scale":      1e6,
            "colorscale": "Purples",
            "cbformat":   ".0f",
            "unit":       "M USD",
        },
    ]

    # ── Subplot layout ───────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=2,
        specs=[
            [{"type": "choropleth", "colspan": 2}, None],
            [{"type": "bar"},                       {"type": "table"}],
        ],
        subplot_titles=[
            "",
            "Top 10 Countries — Sleep Loss per Capita",
            "Top 10 Worst Match-Nights",
        ],
        row_heights=[0.62, 0.38],
        vertical_spacing=0.08,
    )

    # ── Choropleth traces (one per metric, only first visible) ───────────────
    custom_cols = [
        "country", "total_sleep_loss_tournament",
        "avg_sleep_loss_per_capita", "total_economic_loss", "population",
    ]
    customdata = df_agg[custom_cols].values

    for idx, m in enumerate(METRICS):
        z = (df_agg[m["col"]] / m["scale"]) if m["scale"] != 1.0 else df_agg[m["col"]]
        fig.add_trace(
            go.Choropleth(
                locations=df_agg["iso3"],
                z=z,
                text=df_agg["country"],
                colorscale=m["colorscale"],
                visible=(idx == 0),
                name=m["label"],
                customdata=customdata,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Sleep Loss Total: %{customdata[1]:,.0f} hrs<br>"
                    "Per Capita: %{customdata[2]:.4f} hrs<br>"
                    "Economic Loss: $%{customdata[3]:,.0f}<br>"
                    "Population: %{customdata[4]:,}"
                    "<extra></extra>"
                ),
                colorbar=dict(
                    title=dict(text=m["unit"], side="right"),
                    tickformat=m["cbformat"],
                    x=0.46,
                    len=0.55,
                ),
                showscale=True,
            ),
            row=1, col=1,
        )

    # ── Top-10 bar chart ─────────────────────────────────────────────────────
    top10 = df_agg.nlargest(10, "avg_sleep_loss_per_capita").sort_values(
        "avg_sleep_loss_per_capita"
    )
    fig.add_trace(
        go.Bar(
            x=top10["avg_sleep_loss_per_capita"],
            y=top10["country"],
            orientation="h",
            marker=dict(
                color=top10["avg_sleep_loss_per_capita"],
                colorscale="YlOrRd",
                showscale=False,
            ),
            hovertemplate="<b>%{y}</b><br>%{x:.4f} hrs per capita<extra></extra>",
            name="",
        ),
        row=2, col=1,
    )

    # ── Worst match-nights table ─────────────────────────────────────────────
    worst = (
        df_match
        .nlargest(10, "total_sleep_loss_hours")
        [["country", "stage", "local_kickoff", "viewers",
          "sleep_loss_per_viewer", "total_sleep_loss_hours", "economic_loss"]]
        .copy()
    )
    worst["local_kickoff"]        = pd.to_datetime(worst["local_kickoff"], utc=True).dt.strftime("%b %d %H:%M")
    worst["sleep_loss_M_hrs"]     = (worst["total_sleep_loss_hours"] / 1e6).round(2)
    worst["economic_loss_B_usd"]  = (worst["economic_loss"] / 1e9).round(3)
    worst["viewers_M"]            = (worst["viewers"] / 1e6).round(1)

    CELL_COLORS = [["#fff", "#fef9ec"] * 6]
    fig.add_trace(
        go.Table(
            header=dict(
                values=["<b>Country</b>", "<b>Stage</b>", "<b>Kickoff (local)</b>",
                        "<b>Viewers (M)</b>", "<b>Sleep Loss (M hrs)</b>", "<b>Econ. Loss ($B)</b>"],
                fill_color="#c0392b",
                font=dict(color="white", size=11),
                align="left",
                height=26,
            ),
            cells=dict(
                values=[
                    worst["country"].tolist(),
                    worst["stage"].tolist(),
                    worst["local_kickoff"].tolist(),
                    worst["viewers_M"].tolist(),
                    worst["sleep_loss_M_hrs"].tolist(),
                    worst["economic_loss_B_usd"].tolist(),
                ],
                fill_color=CELL_COLORS,
                align="left",
                font=dict(size=10),
                height=22,
            ),
        ),
        row=2, col=2,
    )

    # ── Dropdown — metric selection ──────────────────────────────────────────
    n_traces = len(fig.data)  # 3 choropleth + 1 bar + 1 table

    def _vis(active_idx):
        v = [False] * len(METRICS)
        v[active_idx] = True
        v.extend([True, True])   # bar and table always visible
        return v

    dropdown_buttons = [
        dict(label=m["label"], method="update", args=[{"visible": _vis(i)}])
        for i, m in enumerate(METRICS)
    ]

    n_matches  = df_match["match_id"].nunique()
    n_countries = len(df_agg)

    fig.update_layout(
        title=dict(
            text=(
                f"⚽  World Cup Sleep Loss Dashboard"
                f"<br><sup>{n_matches} matches · {n_countries} countries · "
                "Model: interest × stage importance × national boost</sup>"
            ),
            font=dict(size=17, family="Arial Black"),
            x=0.5, xanchor="center",
        ),
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(0,0,0,0.2)",
            projection_type="natural earth",
            showland=True,
            landcolor="#f5f5f5",
            showocean=True,
            oceancolor="#d6eaf8",
        ),
        updatemenus=[dict(
            buttons=dropdown_buttons,
            direction="down",
            showactive=True,
            x=0.01, y=1.04,
            xanchor="left", yanchor="top",
            bgcolor="white",
            bordercolor="#ccc",
            font=dict(size=12),
        )],
        annotations=[
            dict(
                text="<b>Metric:</b>",
                x=-0.01, y=1.055,
                xref="paper", yref="paper",
                showarrow=False,
                font=dict(size=12),
            ),
        ],
        height=920,
        template="plotly_white",
        showlegend=False,
        margin=dict(t=110, b=50, l=10, r=10),
    )

    fig.update_xaxes(title_text="Hrs per capita", row=2, col=1)

    out_path = os.path.join(output_dir, "world_cup_sleep_loss_dashboard.html")
    fig.write_html(out_path, include_plotlyjs="cdn")
    _LOG.info("Dashboard written to %s", out_path)
    return out_path


# Legacy shim so existing code calling choropleth_country_aggregates() still works
def choropleth_country_aggregates(df_agg, metric="total_sleep_loss_tournament", output_dir="outputs"):
    return create_sleep_loss_dashboard(df_agg, pd.DataFrame(), output_dir=output_dir)
