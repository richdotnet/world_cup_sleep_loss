import os
import io
import time
import logging
from datetime import datetime, timezone

# Load .env so API token is available when the pipeline auto-runs
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import dash
from dash import dcc, html, dash_table, Input, Output, ctx
import dash_bootstrap_components as dbc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
_LOG = logging.getLogger(__name__)

OUTPUT_DIR    = "outputs"
CACHE_TTL_SEC = 24 * 3600          # 24-hour cache
_STAMP_FILE   = os.path.join(OUTPUT_DIR, ".cache_ts")

# ──────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cache_age_seconds() -> float:
    """Return seconds since the last successful pipeline run, or infinity."""
    try:
        return time.time() - os.path.getmtime(_STAMP_FILE)
    except FileNotFoundError:
        return float("inf")


def _write_cache_stamp():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(_STAMP_FILE, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())


# ──────────────────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_outputs(force: bool = False):
    """Run the pipeline only when outputs are missing or the 24-h cache has expired."""
    needed = [
        os.path.join(OUTPUT_DIR, f)
        for f in ("country_aggregates.csv", "country_match_impact.csv",
                  "stage_aggregates.csv", "top_worst_nights.csv")
    ]
    outputs_exist = all(os.path.exists(f) for f in needed)
    age = _cache_age_seconds()

    if not force and outputs_exist and age < CACHE_TTL_SEC:
        hrs = age / 3600
        _LOG.info("Cache hit — outputs are %.1f h old (TTL=24 h), skipping pipeline.", hrs)
        return

    reason = "forced refresh" if force else ("outputs missing" if not outputs_exist else "cache expired (%.1f h old)" % (age / 3600))
    _LOG.info("Running pipeline (%s)…", reason)
    import run_simulation as rs
    rs.main_no_args()
    _write_cache_stamp()


def load_data(force: bool = False):
    _ensure_outputs(force=force)
    agg   = pd.read_csv(os.path.join(OUTPUT_DIR, "country_aggregates.csv"))
    match = pd.read_csv(os.path.join(OUTPUT_DIR, "country_match_impact.csv"))
    stage = pd.read_csv(os.path.join(OUTPUT_DIR, "stage_aggregates.csv"))
    worst = pd.read_csv(os.path.join(OUTPUT_DIR, "top_worst_nights.csv"))

    # Format numbers for better readability
    agg["total_sleep_loss_M_hrs"]       = (agg["total_sleep_loss_tournament"] / 1e6).round(1)
    agg["avg_sleep_loss_per_capita_hrs"] = agg["avg_sleep_loss_per_capita"].round(2)
    agg["total_economic_loss_B"]        = (agg["total_economic_loss"] / 1e9).round(2)

    worst["sleep_loss_M_hrs"]    = (worst["total_sleep_loss_hours"] / 1e6).round(0)
    worst["economic_loss_M_usd"] = (worst["economic_loss"] / 1e6).round(1)
    worst["viewers_M"]           = (worst["viewers"] / 1e6).round(1)
    worst["local_kickoff_fmt"]   = worst["local_kickoff"].apply(
        lambda x: pd.to_datetime(x).strftime("%b %d  %H:%M") if pd.notna(x) else ""
    )

    stage["sleep_loss_M_hrs"]    = (stage["total_sleep_loss_hours"] / 1e6).round(1)
    stage["economic_loss_B_usd"] = (stage["total_economic_loss"]    / 1e9).round(2)
    stage["viewers_B"]           = (stage["total_viewers"] / 1e9).round(3)

    return agg, match, stage, worst


# ── Server-side data cache (avoids JSON serialisation round-trips) ─────────
class _Cache:
    agg       = None
    match     = None
    stage     = None
    worst     = None
    loaded_at = None   # UTC datetime of last in-memory load


def _refresh_cache(force: bool = False):
    _Cache.agg, _Cache.match, _Cache.stage, _Cache.worst = load_data(force=force)
    _Cache.loaded_at = datetime.now(timezone.utc)
    _LOG.info(
        "Cache refreshed: %d countries, %d match rows  (data age: %s)",
        len(_Cache.agg), len(_Cache.match),
        _fmt_age(_cache_age_seconds()),
    )


def _fmt_age(seconds: float) -> str:
    if seconds == float("inf") or seconds < 0:
        return "unknown"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m:02d}m ago" if h else f"{m}m ago"


_refresh_cache()  # load on startup — pipeline skipped if cache is fresh


# ──────────────────────────────────────────────────────────────────────────────
# Figure builders
# ──────────────────────────────────────────────────────────────────────────────

METRIC_META = {
    "total_sleep_loss_M_hrs": {
        "label": "Total Sleep Loss (Million Hours)",
        "color": "Reds",
        "fmt":   ".1f",
    },
    "avg_sleep_loss_per_capita_hrs": {
        "label": "Sleep Loss per Person (Hours)",
        "color": "YlOrRd",
        "fmt":   ".2f",
    },
    "total_economic_loss_B": {
        "label": "Economic Loss (Billion USD)",
        "color": "Purples",
        "fmt":   ".2f",
    },
}

STAGE_ORDER  = ["group", "ro16", "quarter", "semi", "final"]
STAGE_LABELS = {"group": "Group", "ro16": "Round of 16",
                "quarter": "Quarter-Final", "semi": "Semi-Final", "final": "Final"}


def fig_choropleth(agg: pd.DataFrame, metric: str) -> go.Figure:
    meta = METRIC_META[metric]
    fig = px.choropleth(
        agg,
        locations="iso3",
        color=metric,
        hover_name="country",
        hover_data={
            "iso3": False,
            "population": ":,",
            "total_sleep_loss_M_hrs": ":.1f",
            "avg_sleep_loss_per_capita_hrs": ":.2f",
            "total_economic_loss_B": ":.2f",
        },
        color_continuous_scale=meta["color"],
        projection="natural earth",
        labels={
            metric: meta["label"],
            "population": "Population",
            "total_sleep_loss_M_hrs": "Sleep Loss (M hrs)",
            "avg_sleep_loss_per_capita_hrs": "Per Person (hrs)",
            "total_economic_loss_B": "Econ. Loss (B USD)",
        },
    )
    fig.update_layout(
        margin=dict(t=10, b=0, l=0, r=0),
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(0,0,0,0.1)",
            showland=True,
            landcolor="#f9fafb",
            showocean=True,
            oceancolor="#dbeafe",
        ),
        coloraxis_colorbar=dict(title=meta["label"], tickformat=meta["fmt"], len=0.75),
        paper_bgcolor="white",
        plot_bgcolor="rgba(0,0,0,0)",
        height=440,
        font=dict(family="Geist, sans-serif", color="#1f2937"),
    )
    return fig


def fig_top10_bar(agg: pd.DataFrame, metric: str) -> go.Figure:
    meta = METRIC_META[metric]
    top  = agg.nlargest(10, metric).sort_values(metric)
    fig  = px.bar(
        top, x=metric, y="country", orientation="h",
        color=metric, color_continuous_scale=meta["color"],
        labels={metric: meta["label"], "country": ""},
        text=metric,
    )
    fig.update_traces(texttemplate=f"%{{text:{meta['fmt']}}}", textposition="outside")
    fig.update_layout(
        margin=dict(t=10, b=30, l=0, r=120),
        showlegend=False, coloraxis_showscale=False,
        paper_bgcolor="white", plot_bgcolor="rgba(0,0,0,0)",
        height=340, xaxis_title=meta["label"], yaxis_title="",
        font=dict(family="Geist, sans-serif", color="#1f2937"),
        xaxis=dict(gridcolor="#e5e7eb", gridwidth=0.5),
        yaxis=dict(tickfont=dict(size=11)),
    )
    return fig


def fig_stage_bar(stage: pd.DataFrame) -> go.Figure:
    df = stage.copy()
    df["stage_label"] = df["stage"].map(STAGE_LABELS).fillna(df["stage"])
    df["_order"]      = df["stage"].map({s: i for i, s in enumerate(STAGE_ORDER)})
    df = df.sort_values("_order")

    fig = make_subplots(
        rows=1, cols=3,
        subplot_titles=["Viewers (Billions)", "Sleep Loss (Million Hours)", "Economic Loss (B USD)"],
        shared_yaxes=True,
    )
    palette = ["#ef4444", "#8b5cf6", "#3b82f6"]
    for i, (col, _) in enumerate([
        ("viewers_B", ""), ("sleep_loss_M_hrs", ""), ("economic_loss_B_usd", ""),
    ], 1):
        fig.add_trace(
            go.Bar(y=df["stage_label"], x=df[col], orientation="h",
                   marker_color=palette[i - 1], showlegend=False,
                   hovertemplate="%{y}: %{x:.1f}<extra></extra>"),
            row=1, col=i,
        )
    fig.update_layout(
        margin=dict(t=35, b=10, l=80, r=10),
        paper_bgcolor="white", plot_bgcolor="rgba(0,0,0,0)", height=260,
        font=dict(family="Geist, sans-serif", color="#1f2937"),
        xaxis=dict(gridcolor="#e5e7eb", gridwidth=0.5),
        xaxis2=dict(gridcolor="#e5e7eb", gridwidth=0.5),
        xaxis3=dict(gridcolor="#e5e7eb", gridwidth=0.5),
    )
    return fig


def fig_sleep_scatter(match: pd.DataFrame) -> go.Figure:
    df = match.copy()
    df["local_hour"] = df["local_kickoff"].apply(
        lambda x: pd.to_datetime(x).hour if pd.notna(x) else 12
    )
    sample = df.sample(min(3000, len(df)), random_state=42)
    fig = px.scatter(
        sample, x="local_hour", y="sleep_loss_per_viewer",
        color="stage", color_discrete_sequence=px.colors.qualitative.Bold,
        opacity=0.55,
        labels={
            "local_hour": "Local Kickoff Hour",
            "sleep_loss_per_viewer": "Sleep Loss / Viewer (hrs)",
            "stage": "Stage",
        },
        hover_data={"country": True, "match_id": True},
        category_orders={"stage": STAGE_ORDER},
    )
    fig.update_layout(
        margin=dict(t=10, b=30, l=0, r=0),
        paper_bgcolor="white", plot_bgcolor="rgba(0,0,0,0.02)", height=300,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        font=dict(family="Geist, sans-serif", color="#1f2937"),
        xaxis=dict(gridcolor="#e5e7eb", gridwidth=0.5),
        yaxis=dict(gridcolor="#e5e7eb", gridwidth=0.5),
    )
    return fig


# ──────────────────────────────────────────────────────────────────────────────
# Layout helpers
# ──────────────────────────────────────────────────────────────────────────────

def _kpi_card(title: str, value: str, color: str = "#ef4444") -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([
            html.P(title, className="kpi-label"),
            html.H4(value, className="kpi-value", style={"color": color}),
        ]),
        className="kpi-card",
    )


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, "assets/custom.css"],
    title="World Cup Sleep Loss",
    assets_folder="assets",
)

app.layout = dbc.Container(
    fluid=True,
    style={"minHeight": "100vh", "padding": "0", "backgroundColor": "transparent"},
    children=[

        # ── Header ─────────────────────────────────────────────────────────
        dbc.Navbar(
            dbc.Container([
                dbc.NavbarBrand(
                    "World Cup Sleep Loss Dashboard",
                    style={"fontWeight": 800, "fontSize": "1.35rem", "color": "white", "letterSpacing": "-0.5px"},
                ),
                dbc.NavItem(
                    dbc.Button(
                        "↻  Refresh Live Data", id="refresh-btn", n_clicks=0,
                        color="light", outline=True, size="sm",
                        style={"marginLeft": "auto", "fontWeight": 500},
                    ),
                ),
            ], fluid=True),
            color="danger", dark=True,
            style={"marginBottom": "0"},
        ),

        # ── Main Content ───────────────────────────────────────────────────
        html.Main(
            style={"padding": "2rem 2.5rem"},
            children=[

                # ── Controls ────────────────────────────────────────────────────────
                dbc.Row([
                    dbc.Col([
                        html.Div(
                            className="controls-section",
                            children=[
                                dbc.Row([
                                    dbc.Col([
                                        html.Label("Map / Chart Metric", style={"fontWeight": 600, "fontSize": "0.875rem"}),
                                        dcc.Dropdown(
                                            id="metric-dropdown",
                                            options=[{"label": v["label"], "value": k} for k, v in METRIC_META.items()],
                                            value="total_sleep_loss_M_hrs",
                                            clearable=False,
                                        ),
                                    ], md=4),
                                    dbc.Col([
                                        html.Label("Filter Scatter by Stage", style={"fontWeight": 600, "fontSize": "0.875rem"}),
                                        dcc.Dropdown(
                                            id="stage-dropdown",
                                            options=[{"label": "All Stages", "value": "ALL"}]
                                                   + [{"label": STAGE_LABELS.get(s, s), "value": s} for s in STAGE_ORDER],
                                            value="ALL",
                                            clearable=False,
                                        ),
                                    ], md=3),
                                    dbc.Col(
                                        dcc.Loading(html.Div(id="refresh-status"), color="#ef4444", type="dot"),
                                        md=2, className="d-flex align-items-end",
                                        style={"paddingBottom": "0.5rem"}
                                    ),
                                ], align="end", className="g-3"),
                            ],
                        ),
                    ], width=12),
                ], className="mb-4"),

                # ── KPI row ─────────────────────────────────────────────────────────
                dbc.Row(id="kpi-row", className="mb-4 g-3"),

                # ── World map ────────────────────────────────────────────────────────
                dbc.Row([
                    dbc.Col(dbc.Card([
                        dbc.CardHeader("🗺️  World Map"),
                        dbc.CardBody(dcc.Graph(id="choropleth-map", config={"scrollZoom": True})),
                    ]), width=12),
                ], className="mb-4"),

                # ── Top-10 + Stage ────────────────────────────────────────────────────
                dbc.Row([
                    dbc.Col(dbc.Card([
                        dbc.CardHeader("🏆  Top 10 Countries"),
                        dbc.CardBody(dcc.Graph(id="top10-bar")),
                    ], className="h-100"), md=6),
                    dbc.Col(dbc.Card([
                        dbc.CardHeader("📊  By Tournament Stage"),
                        dbc.CardBody(dcc.Graph(id="stage-bar")),
                    ], className="h-100"), md=6),
                ], className="mb-4 g-3"),

                # ── Scatter + Worst nights ───────────────────────────────────────────
                dbc.Row([
                    dbc.Col(dbc.Card([
                        dbc.CardHeader("🕐  Kickoff Hour vs. Sleep Loss"),
                        dbc.CardBody(dcc.Graph(id="scatter-plot")),
                    ], className="h-100"), md=5),
                    dbc.Col(dbc.Card([
                        dbc.CardHeader("😴  Top 10 Worst Match-Nights"),
                        dbc.CardBody(id="worst-table"),
                    ], className="h-100"), md=7),
                ], className="mb-4 g-3"),

                # ── Country table ────────────────────────────────────────────────────
                dbc.Row([
                    dbc.Col(dbc.Card([
                        dbc.CardHeader(
                            dbc.Row([
                                dbc.Col("📋  All Countries",
                                        style={"fontWeight": 600},
                                        className="d-flex align-items-center"),
                                dbc.Col(
                                    dbc.Input(
                                        id="country-search",
                                        placeholder="Search (e.g. united, bra, 2.5)…",
                                        debounce=True,
                                        size="sm",
                                    ),
                                    md=6,
                                ),
                            ], align="center", justify="between"),
                        ),
                        dbc.CardBody(id="country-table"),
                    ]), width=12),
                ], className="mb-4"),
            ],
        ),

        # ── Footer ───────────────────────────────────────────────────────────
        html.Footer(
            children=[
                html.Div(
                    "Sources: football-data.org · World Bank · Google Trends  |  "
                    "Model: viewer_ratio = interest × stage (own-team only)  |  "
                    "Sleep loss = overlap(match window, 23:00–07:00 local)  |  "
                    "Economic loss = sleep_loss × GDP per capita",
                    id="footer-econ-impact",
                    style={"fontSize": "0.8rem", "color": "#6b7280"},
                ),
            ],
            style={
                "textAlign": "center", "padding": "1.5rem 2rem",
                "borderTop": "1px solid #e5e7eb", "backgroundColor": "white",
                "marginTop": "3rem",
            },
        ),
    ],
)


# ──────────────────────────────────────────────────────────────────────────────
# Single callback — reads from server-side cache, no serialisation
# ──────────────────────────────────────────────────────────────────────────────

@app.callback(
    Output("kpi-row",        "children"),
    Output("choropleth-map", "figure"),
    Output("top10-bar",      "figure"),
    Output("stage-bar",      "figure"),
    Output("scatter-plot",   "figure"),
    Output("worst-table",    "children"),
    Output("country-table",  "children"),
    Output("refresh-status", "children"),
    Output("footer-econ-impact", "children"),
    Input("refresh-btn",     "n_clicks"),
    Input("metric-dropdown", "value"),
    Input("stage-dropdown",  "value"),
    Input("country-search",  "value"),
)
def update_all(n_clicks, metric, stage_filter, country_search):
    # Re-run pipeline only on explicit refresh click
    if ctx.triggered_id == "refresh-btn" and n_clicks and n_clicks > 0:
        _LOG.info("Refresh button clicked — forcing pipeline re-run (bypassing cache)…")
        try:
            _refresh_cache(force=True)
        except Exception as exc:
            _LOG.error("Pipeline refresh failed: %s", exc)

    agg   = _Cache.agg
    match = _Cache.match
    stage = _Cache.stage
    worst = _Cache.worst

    # ── KPIs ────────────────────────────────────────────────────────────────
    total_sleep_M = match["total_sleep_loss_hours"].sum() / 1e6
    total_econ_B  = match["economic_loss"].sum() / 1e9
    n_matches   = match["match_id"].nunique()
    n_countries = agg["iso3"].nunique()

    kpi_cards = [
        dbc.Col(_kpi_card("Matches Analysed",     f"{n_matches:,}",                           "#ef4444"), md=3),
        dbc.Col(_kpi_card("Countries in Model",   f"{n_countries:,}",                          "#3b82f6"), md=3),
        dbc.Col(_kpi_card("Total Sleep Loss",     f"{total_sleep_M:,.0f} Million Hrs",      "#8b5cf6"), md=3),
        dbc.Col(_kpi_card("Est. Economic Impact", f"${total_econ_B:.1f} Billion",             "#10b981"), md=3),
    ]

    # ── Figures ──────────────────────────────────────────────────────────────
    match_f = match if stage_filter == "ALL" else match[match["stage"] == stage_filter]

    # ── Worst-nights table ────────────────────────────────────────────────────
    worst_disp = worst[[
        "country", "stage", "local_kickoff_fmt", "viewers_M",
        "sleep_loss_M_hrs", "economic_loss_M_usd",
    ]].rename(columns={
        "local_kickoff_fmt":   "Kickoff (Local Time)",
        "viewers_M":           "Viewers (Millions)",
        "sleep_loss_M_hrs":    "Sleep Loss (M Hours)",
        "economic_loss_M_usd": "Economic Loss (M USD)",
        "country": "Country",
        "stage":   "Stage",
    })
    worst_table = dash_table.DataTable(
        data=worst_disp.to_dict("records"),
        columns=[{"name": c, "id": c} for c in worst_disp.columns],
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#1f2937", "color": "white",
                      "fontWeight": "bold", "fontSize": "0.85rem", "textTransform": "uppercase",
                      "letterSpacing": "0.3px", "padding": "1rem 0.875rem"},
        style_cell={"fontSize": "0.9rem", "padding": "0.875rem",
                    "border": "1px solid #e5e7eb"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#f9fafb"},
            {"if": {"state": "hover"}, "backgroundColor": "rgba(239, 68, 68, 0.03)"},
        ],
        sort_action="native",
    )

    # ── Country detail table ──────────────────────────────────────────────────
    country_disp = agg[[
        "country", "population", "total_sleep_loss_M_hrs",
        "avg_sleep_loss_per_capita_hrs", "total_economic_loss_B", "matches_analyzed",
    ]].rename(columns={
        "country":                       "Country",
        "population":                    "Population",
        "total_sleep_loss_M_hrs":        "Sleep Loss (Million Hrs)",
        "avg_sleep_loss_per_capita_hrs": "Per Person (Hrs)",
        "total_economic_loss_B":         "Economic Loss (B USD)",
        "matches_analyzed":              "Matches",
    }).sort_values("Sleep Loss (Million Hrs)", ascending=False)

    if country_search:
        q = country_search.strip()
        mask = country_disp.apply(
            lambda row: row.astype(str).str.contains(q, case=False, na=False).any(),
            axis=1,
        )
        country_disp = country_disp[mask]

    country_table = dash_table.DataTable(
        data=country_disp.to_dict("records"),
        columns=[{"name": c, "id": c} for c in country_disp.columns],
        sort_action="native",
        page_size=15,
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#1f2937", "color": "white",
                      "fontWeight": "bold", "fontSize": "0.85rem", "textTransform": "uppercase",
                      "letterSpacing": "0.3px", "padding": "1rem 0.875rem"},
        style_cell={"fontSize": "0.9rem", "padding": "0.875rem",
                    "border": "1px solid #e5e7eb", "minWidth": "80px"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#f9fafb"},
            {"if": {"state": "hover"}, "backgroundColor": "rgba(239, 68, 68, 0.03)"},
        ],
    )

    age_str = _fmt_age(_cache_age_seconds())
    status = html.Small(
        f"{n_matches} matches · {n_countries} countries · data {age_str}",
        style={"color": "#888"},
    )
    
    econ_impact_text = html.Div(
        [
            html.Div(
                "Sources: football-data.org · World Bank · Google Trends  |  "
                "Model: viewer_ratio = interest × stage (own-team only)  |  "
                "Sleep loss = overlap(match window, 23:00–07:00 local)  |  "
                "Economic loss = sleep_loss × GDP per capita",
                style={"fontSize": "0.8rem", "color": "#6b7280", "marginTop": "0.25rem"},
            ),
        ],
        style={"textAlign": "center"},
    )

    return (
        kpi_cards,
        fig_choropleth(agg, metric),
        fig_top10_bar(agg, metric),
        fig_stage_bar(stage),
        fig_sleep_scatter(match_f),
        worst_table,
        country_table,
        status,
        econ_impact_text,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _LOG.info("Starting World Cup Sleep Loss app")
    app.run(debug=False, host="127.0.0.1", port=8050)

