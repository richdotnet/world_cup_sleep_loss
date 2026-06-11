# ⚽ World Cup Sleep Loss Map

A **production-quality data pipeline** that models the global sleep and economic cost of
watching World Cup football — driven entirely by **live public APIs** with no hardcoded data.

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your football-data.org token (free — see below)
$env:FOOTBALL_DATA_API_TOKEN = "your_token_here"   # PowerShell
# or:  export FOOTBALL_DATA_API_TOKEN=your_token   # bash/zsh
# or:  add  FOOTBALL_DATA_API_TOKEN=your_token  to a .env file

# 3. Run the pipeline (fetches live data, produces HTML dashboard)
python run_simulation.py

# Development mode (no API key required, uses bundled sample data)
python run_simulation.py --dev
```

Open `outputs/world_cup_sleep_loss_dashboard.html` in any browser.

---

## 📦 Data Sources

| Input | Source | Key | Notes |
|-------|--------|-----|-------|
| Match schedule | [football-data.org v4 API](https://www.football-data.org/) | Free token (register once) | Provides live fixture list for competition "WC" |
| Population | [World Bank — SP.POP.TOTL](https://data.worldbank.org/indicator/SP.POP.TOTL) | None | Most-recent available year (~2023) |
| GDP per capita | [World Bank — NY.GDP.PCAP.CD](https://data.worldbank.org/indicator/NY.GDP.PCAP.CD) | None | Current USD |
| Labour force participation | [World Bank — SL.TLF.CACT.ZS](https://data.worldbank.org/indicator/SL.TLF.CACT.ZS) | None | % of population aged 15+ |
| Timezone mapping | [pytz](https://pythonhosted.org/pytz/) bundled data | None | First listed timezone per country ISO-2 code |
| Viewership interest | [Google Trends via pytrends](https://github.com/GeneralMills/pytrends) | None | Unofficial; rate-limited; search interest ≠ TV viewership |

---

## ⚙️ Methodology

### Viewer Model

```
viewer_ratio = normalized_interest_score × match_importance × national_team_boost
viewers      = population × min(viewer_ratio, 0.90)
```

| Parameter | Value | Source |
|-----------|-------|--------|
| `normalized_interest_score` | 0–1 (Google Trends relative interest) | pytrends |
| `default_interest_score` | 0.10 | Fallback when Trends unavailable |
| `match_importance` | group=1.0, R16=1.2, QF=1.5, SF=2.0, Final=3.0 | Proxy from FIFA/Nielsen viewership reports |
| `national_team_boost` | 3.0× when the country's team is playing | Calibrated to match observed surges |
| `max_viewer_ratio` | 0.90 (90 % of population) | Hard cap |

### Sleep Loss Model (time-overlap)

Sleep loss per viewer = overlap in hours between:
- **Match window**: `kickoff → kickoff + 2 h` (in local time)
- **Sleep window**: `23:00 → 07:00` (configurable per country)

This produces **0 h** for afternoon kick-offs and up to **2 h** for late-night matches.

### Productivity & Economic Impact

```
lost_productive_hours = total_sleep_loss_hours × working_pop_ratio
economic_loss         = lost_productive_hours × (gdp_per_capita / work_hours_per_year)
```

Research basis: ~1.5 % productivity drop per hour of lost sleep on a work night.

### Calibration

All viewer values are uniformly scaled so that the modelled total for the Final matches
the published benchmark (FIFA/Nielsen: ~1.5 billion for the 2022 Final).

---

## 📊 Outputs

| File | Description |
|------|-------------|
| `outputs/country_match_impact.csv` | One row per country × match |
| `outputs/country_aggregates.csv` | Per-country totals across all matches |
| `outputs/stage_aggregates.csv` | Totals grouped by tournament stage |
| `outputs/top_worst_nights.csv` | Top 10 worst match-nights globally |
| `outputs/world_cup_sleep_loss_dashboard.html` | Interactive Plotly dashboard |

### Dashboard features
- **Metric dropdown**: total sleep loss · per-capita sleep loss · economic loss
- **World choropleth map** with rich hover tooltips
- **Top-10 countries** bar chart (per-capita sleep loss)
- **Top-10 worst match-nights** data table

---

## 🔑 Getting a football-data.org API Token

1. Go to [https://www.football-data.org/client/register](https://www.football-data.org/client/register)
2. Register for a **free** account — no payment required
3. Copy your token and set it via environment variable or `.env` file

---

## ⚠️ Assumptions & Limitations

| Assumption | Impact |
|------------|--------|
| Single timezone per country (capital/first listed) | Underestimates sleep loss in large countries (USA, Russia, Brazil) |
| Google Trends = viewership interest | Search interest ≠ TV audience; overrepresents internet-connected users |
| Uniform sleep window 23:00–07:00 globally | Ignores cultural differences (later bedtimes in Spain, Argentina) |
| No nap-recovery modelling | Likely overestimates cumulative economic impact |
| National team boost = 3× (fixed) | Real boost varies by team popularity and match context |
| Weekend/holiday matches ignored | Reduces actual economic impact vs. weekday matches |

---

## 🛠 Project Structure

```
sleep_loss/
├── run_simulation.py          # pipeline entry point
├── requirements.txt
├── .env                       # (create this) for API tokens
├── src/
│   ├── config.py              # all parameters, documented
│   ├── data_ingest.py         # live API fetchers (football-data + World Bank)
│   ├── trends.py              # Google Trends / pytrends
│   ├── simulate.py            # viewer model + sleep-overlap computation
│   ├── aggregate.py           # country & stage aggregation
│   ├── modeling.py            # calibration + validation
│   └── viz.py                 # Plotly interactive dashboard
└── outputs/                   # generated files
```

---

## ⚡ Configuration

All parameters live in `src/config.py`. Key knobs:

```python
"national_team_boost_playing": 3.0    # multiplier when a country's team plays
"default_interest_score": 0.10        # fallback when Trends unavailable
"match_duration_hours": 2.0           # assumed match duration for sleep overlap
"sleep_start_hour": 23                # sleep window start (local time)
"sleep_end_hour":   7                 # sleep window end   (local time)
"calibration": {"stage": "final", "benchmark_viewers": 1_500_000_000}
```

Key outputs
- `outputs/country_match_impact.csv` — country × match level dataset
- `outputs/country_aggregates.csv` — per-country aggregates
- `outputs/world_map_total_sleep_loss.html` — interactive choropleth (Plotly)

Quickstart
----------
1. Create a Python environment and install dependencies:

```bash
pip install -r requirements.txt
```

2. Run the sample simulation:

```bash
python run_simulation.py
```

The runner will generate sample data in `outputs/`.

Assumptions (short)
- Uses a configurable seeded simulator for reproducibility.
- Base interest is simulated by default (configurable).
- Timezone mapping uses a single timezone per country (capital timezone).
- Productivity loss = 1.5% per hour of lost sleep (configurable).
- GDP per hour = GDP per capita / 2000 (configurable).

See `src/` for implementation details and configurable parameters.
