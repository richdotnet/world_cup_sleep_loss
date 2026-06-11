"""Live data ingestion from public APIs.

Sources
-------
Match schedule  : football-data.org v4 API (free tier)
                  Register at https://www.football-data.org/client/register
                  Set token via: FOOTBALL_DATA_API_TOKEN env var (or .env file)
Country data    : World Bank Open Data API — no key required
                  https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
                  Indicators: SP.POP.TOTL · NY.GDP.PCAP.CD · SL.TLF.CACT.ZS
Timezone map    : pytz bundled country → timezone lookup
"""
import os
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import pycountry
import pytz
import requests

_LOG = logging.getLogger(__name__)

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
WORLD_BANK_BASE    = "https://api.worldbank.org/v2"

# Primary (most-populated) timezone per ISO-2 country code.
# pytz.country_timezones returns zones alphabetically, not by population,
# so large multi-zone countries get the wrong zone (e.g. Brazil → America/Noronha
# instead of America/Sao_Paulo, Australia → Australia/Lord_Howe instead of
# Australia/Sydney). Override here for any country where that matters.
_PRIMARY_TIMEZONE: dict = {
    "BR": "America/Sao_Paulo",       # 90 %+ of population in BRT (UTC-3)
    "AU": "Australia/Sydney",         # largest metro; east coast majority
    "RU": "Europe/Moscow",            # ~80 % of population west of Urals
    "US": "America/New_York",         # Eastern time zone has largest population share
    "CA": "America/Toronto",          # Ontario/Quebec majority
    "MX": "America/Mexico_City",      # Central time; ~85 % of population
    "ID": "Asia/Jakarta",             # Java/Bali; majority of population
    "CN": "Asia/Shanghai",            # single official tz used nationally
    "IN": "Asia/Kolkata",             # single tz
    "KZ": "Asia/Almaty",             # largest city
    "MN": "Asia/Ulaanbaatar",        # capital
    "CD": "Africa/Kinshasa",          # majority in west
    "EC": "America/Guayaquil",        # mainland majority
    "KI": "Pacific/Tarawa",           # capital atoll
}

# Map football-data.org stage codes → internal keys used in stage_multipliers
_STAGE_MAP = {
    "GROUP_STAGE":        "group",
    "ROUND_OF_16":        "ro16",
    "QUARTER_FINALS":     "quarter",
    "SEMI_FINALS":        "semi",
    "FINAL":              "final",
    "THIRD_PLACE":        "semi",
    "PLAYOFF_ROUND_ONE":  "group",
    "PLAYOFF_ROUND_TWO":  "ro16",
}


# Home nations that don't have separate World Bank data
# Home nations with no ISO 3166-1 alpha-3 code of their own.
# WAL / NIR are NOT in WC 2026, so we only need special handling for SCO.
_UNMAPPED_HOME_NATIONS = {"WAL", "NIR"}

# Hardcoded entries for FIFA nations that have no World Bank / ISO 3166-1 record.
# Statistics sourced from ONS / Scottish Government 2023 estimates.
_HARDCODED_COUNTRIES: list = [
    {
        "iso3": "SCO",
        "country": "Scotland",
        "population": 5_500_000,
        "gdp_per_capita": 46_000.0,   # similar to UK average
        "working_pop_ratio": 0.73,
        "timezone": "Europe/London",
    },
]


# FIFA TLA codes that differ from ISO-3166-1 alpha-3 (only the actual WC 2026 teams)
_FIFA_TLA_OVERRIDES: dict = {
    # Home nations
    "ENG": "GBR",   # England → United Kingdom (has ISO3166 + World Bank data)
    "SCO": "SCO",   # Scotland → internal code (hardcoded stats, no World Bank row)
    # Europe
    "GER": "DEU",   # Germany
    "SUI": "CHE",   # Switzerland
    "NED": "NLD",   # Netherlands
    "CRO": "HRV",   # Croatia
    "POR": "PRT",   # Portugal
    # Africa
    "RSA": "ZAF",   # South Africa
    "ALG": "DZA",   # Algeria
    # Americas
    "HAI": "HTI",   # Haiti
    "PAR": "PRY",   # Paraguay
    # Middle East
    "KSA": "SAU",   # Saudi Arabia
}


def _team_iso3(team_obj: dict) -> Optional[str]:
    """Extract a validated ISO-3166-1-alpha-3 code from a football-data team object.

    Strategy (in order):
    1. If TLA is an unmapped home nation (SCO, WAL, NIR), return None.
    2. Use team.tla directly if it's a valid ISO-3166-1 alpha-3 code in pycountry.
    3. Look up in the FIFA TLA → ISO3 override table.
    4. Fuzzy-search pycountry by the team's display name.
    """
    tla  = (team_obj.get("tla") or "").strip().upper()
    name = (team_obj.get("name") or "").strip()

    # 1. Reject unmapped home nations explicitly (prevent fuzzy-match to GBR)
    if tla in _UNMAPPED_HOME_NATIONS:
        return None

    if tla:
        # 2. Direct match in pycountry (works for MEX, BRA, ARG, USA, JPN, …)
        c = pycountry.countries.get(alpha_3=tla)
        if c:
            return c.alpha_3
        # 3. Manual override (GER→DEU, ENG→GBR, RSA→ZAF, …)
        if tla in _FIFA_TLA_OVERRIDES:
            return _FIFA_TLA_OVERRIDES[tla]

    # 4. Fuzzy name search as last resort
    if name:
        try:
            results = pycountry.countries.search_fuzzy(name)
            if results:
                return results[0].alpha_3
        except LookupError:
            pass

    return None


def sample_country_metadata():
    """Return a small sample DataFrame of country metadata for demo purposes."""
    countries = [
        {"iso3": "ARG", "country": "Argentina", "population": 45195777, "timezone": "America/Argentina/Buenos_Aires", "gdp_per_capita": 10000, "working_pop_ratio": 0.60},
        {"iso3": "BRA", "country": "Brazil", "population": 212559417, "timezone": "America/Sao_Paulo", "gdp_per_capita": 9000, "working_pop_ratio": 0.60},
        {"iso3": "DEU", "country": "Germany", "population": 83783942, "timezone": "Europe/Berlin", "gdp_per_capita": 46000, "working_pop_ratio": 0.65},
        {"iso3": "FRA", "country": "France", "population": 65273511, "timezone": "Europe/Paris", "gdp_per_capita": 42000, "working_pop_ratio": 0.62},
        {"iso3": "GBR", "country": "United Kingdom",  "population":  68207116, "timezone": "Europe/London",    "gdp_per_capita": 40000, "working_pop_ratio": 0.60},
        {"iso3": "USA", "country": "United States",    "population": 331002651, "timezone": "America/New_York", "gdp_per_capita": 65000, "working_pop_ratio": 0.63},
        {"iso3": "MEX", "country": "Mexico", "population": 128932753, "timezone": "America/Mexico_City", "gdp_per_capita": 9800, "working_pop_ratio": 0.55},
        {"iso3": "JPN", "country": "Japan", "population": 126476461, "timezone": "Asia/Tokyo", "gdp_per_capita": 40000, "working_pop_ratio": 0.60},
        {"iso3": "AUS", "country": "Australia", "population": 25499884, "timezone": "Australia/Sydney", "gdp_per_capita": 53000, "working_pop_ratio": 0.62},
        {"iso3": "NGA", "country": "Nigeria", "population": 206139589, "timezone": "Africa/Lagos", "gdp_per_capita": 2300, "working_pop_ratio": 0.50},
        {"iso3": "EGY", "country": "Egypt", "population": 102334404, "timezone": "Africa/Cairo", "gdp_per_capita": 3000, "working_pop_ratio": 0.48},
    ]
    return pd.DataFrame(countries)


def sample_match_schedule():
    """Return a small sample match schedule with UTC kickoff times (ISO 8601 strings)."""
    matches = [
        {"match_id": "M1", "team1": "Argentina",      "team1_iso3": "ARG", "team2": "Saudi Arabia",  "team2_iso3": "SAU", "utc_kickoff": "2022-11-21T18:00:00Z", "stage": "group"},
        {"match_id": "M2", "team1": "Brazil",          "team1_iso3": "BRA", "team2": "Switzerland",   "team2_iso3": "CHE", "utc_kickoff": "2022-11-22T22:00:00Z", "stage": "group"},
        {"match_id": "M3", "team1": "Germany",         "team1_iso3": "DEU", "team2": "Japan",         "team2_iso3": "JPN", "utc_kickoff": "2022-11-23T00:30:00Z", "stage": "group"},
        {"match_id": "M4", "team1": "United Kingdom",  "team1_iso3": "GBR", "team2": "United States", "team2_iso3": "USA", "utc_kickoff": "2022-11-24T03:00:00Z", "stage": "group"},
        {"match_id": "M5", "team1": "France",          "team1_iso3": "FRA", "team2": "Australia",     "team2_iso3": "AUS", "utc_kickoff": "2022-11-25T20:00:00Z", "stage": "quarter"},
        {"match_id": "M6", "team1": "Mexico",          "team1_iso3": "MEX", "team2": "Nigeria",       "team2_iso3": "NGA", "utc_kickoff": "2022-11-26T23:30:00Z", "stage": "semi"},
    ]
    df = pd.DataFrame(matches)
    df["utc_kickoff"] = pd.to_datetime(df["utc_kickoff"])
    return df


def fetch_match_data(api_token: Optional[str] = None, competition: str = "WC") -> pd.DataFrame:
    """Fetch the live match schedule from football-data.org v4.

    Parameters
    ----------
    api_token   : falls back to ``FOOTBALL_DATA_API_TOKEN`` env var.
    competition : football-data.org competition code.  "WC" = FIFA World Cup.

    Returns
    -------
    pd.DataFrame
        Columns: match_id · team1 · team1_iso3 · team2 · team2_iso3
                 · utc_kickoff (tz-aware UTC) · stage
    """
    token = api_token or os.environ.get("FOOTBALL_DATA_API_TOKEN")
    if not token:
        raise EnvironmentError(
            "FOOTBALL_DATA_API_TOKEN is not set.\n"
            "  1. Get a free token at https://www.football-data.org/client/register\n"
            "  2. Set it: $env:FOOTBALL_DATA_API_TOKEN='your_token'  (PowerShell)\n"
            "     or add to a .env file: FOOTBALL_DATA_API_TOKEN=your_token"
        )

    url  = f"{FOOTBALL_DATA_BASE}/competitions/{competition}/matches"
    resp = requests.get(url, headers={"X-Auth-Token": token}, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    rows = []
    for i, m in enumerate(payload.get("matches", [])):
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        rows.append({
            "match_id":   str(m.get("id", f"m{i}")),
            "team1":      home.get("name"),
            "team1_iso3": _team_iso3(home),
            "team2":      away.get("name"),
            "team2_iso3": _team_iso3(away),
            "utc_kickoff": pd.to_datetime(m.get("utcDate")),
            "stage":      _STAGE_MAP.get(m.get("stage", "GROUP_STAGE"), "group"),
        })

    df = pd.DataFrame(rows)
    if not df.empty and df["utc_kickoff"].dt.tz is None:
        df["utc_kickoff"] = df["utc_kickoff"].dt.tz_localize("UTC")
    
    # Drop matches where BOTH teams are unmapped (fully-TBD knockout fixtures).
    # If only one team is unmapped (e.g. Scotland, which has no World Bank data),
    # keep the match so the opposing team still accumulates sleep loss.
    before = len(df)
    df = df[df["team1_iso3"].notna() | df["team2_iso3"].notna()].copy()
    if len(df) < before:
        _LOG.info(
            "Filtered %d fully-TBD matches (both teams unmapped)",
            before - len(df),
        )
    
    _LOG.info("Fetched %d matches for competition %s", len(df), competition)
    return df


def fetch_country_data(default_lfp: float = 0.60) -> pd.DataFrame:
    """Fetch live country-level data from the World Bank Open Data API (no key needed).

    Indicators
    ----------
    SP.POP.TOTL    – Population, total
    NY.GDP.PCAP.CD – GDP per capita (current USD)
    SL.TLF.CACT.ZS – Labour force participation rate, total (%%)

    Parameters
    ----------
    default_lfp : float
        Fallback labour-force participation ratio (0-1) used when the World Bank
        indicator is unavailable for a given country.

    Returns
    -------
    pd.DataFrame
        Columns: iso3 · country · population · gdp_per_capita
                 · working_pop_ratio · timezone
    """
    def _fetch_wb(indicator: str) -> dict:
        """Return {iso3: value} for the most-recently reported value per country."""
        url = (
            f"{WORLD_BANK_BASE}/country/all/indicator/{indicator}"
            "?format=json&mrv=1&per_page=20000"
        )
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
            return {}
        return {
            rec["countryiso3code"]: rec["value"]
            for rec in payload[1]
            if rec.get("value") is not None and rec.get("countryiso3code")
        }

    _LOG.info("Fetching World Bank: population, GDP per capita, labour force participation…")
    pop_data = _fetch_wb("SP.POP.TOTL")
    gdp_data = _fetch_wb("NY.GDP.PCAP.CD")
    lfp_data = _fetch_wb("SL.TLF.CACT.ZS")  # percentage 0-100

    rows = []
    for iso3, population in pop_data.items():
        country_obj = pycountry.countries.get(alpha_3=iso3)
        if not country_obj:
            continue                           # skip regional aggregates
        iso2 = country_obj.alpha_2
        tz   = _PRIMARY_TIMEZONE.get(iso2) or (pytz.country_timezones.get(iso2) or ["UTC"])[0]
        lfp  = lfp_data.get(iso3, default_lfp * 100)  # pct or fallback×100
        rows.append({
            "iso3":              iso3,
            "country":           country_obj.name,
            "population":        int(population),
            "gdp_per_capita":    gdp_data.get(iso3),
            "working_pop_ratio": float(lfp) / 100.0,
            "timezone":          tz,
        })

    df = pd.DataFrame(rows).dropna(subset=["population"])

    # Inject hardcoded nations (e.g. Scotland) that have no World Bank record
    extras = pd.DataFrame(_HARDCODED_COUNTRIES)
    df = pd.concat([df, extras], ignore_index=True)

    _LOG.info("Loaded %d countries from World Bank (+%d hardcoded)", len(df) - len(extras), len(extras))
    return df


# World Football Elo Ratings
# Source: https://www.eloratings.net/World.tsv  (updated after every match)
# Column layout (tab-separated): rank · prev_rank · tla · elo · ...
# The TLA codes follow eloratings.net conventions, which differ from both
# ISO-3166-1 alpha-2 and FIFA TLAs — see _ELO_TLA_TO_ISO3 below.
_ELO_RATINGS_URL = "https://www.eloratings.net/World.tsv"

# eloratings.net TLA → ISO-3166-1 alpha-3.
# Only non-obvious mappings are listed; straightforward 2→3 letter codes
# (BRA, ARG, FRA, etc.) are handled by pycountry lookup on the full name.
_ELO_TLA_TO_ISO3: dict = {
    "EN":  "GBR",   # England (no separate ISO3)
    "SC":  "GBR",   # Scotland (no separate ISO3; shares GBR — handled below)
    "WA":  "GBR",   # Wales
    "NI":  "GBR",   # Northern Ireland
    "MA":  "MAR",   # Morocco
    "IR":  "IRN",   # Iran
    "KO":  "KOR",   # South Korea (eloratings uses KO, not KR)
    "CI":  "CIV",   # Côte d'Ivoire
    "GU":  "GUM",   # Guam
    "TZ":  "TZA",   # Tanzania
    "BO":  "BOL",   # Bolivia
    "SL":  "SLE",   # Sierra Leone
    "ZI":  "ZWE",   # Zimbabwe (old FIFA code)
    "SO":  "SOM",   # Somalia
    "BU":  "BDI",   # Burundi (eloratings uses BU)
    "TO":  "TON",   # Tonga
    "SA":  "SAU",   # Saudi Arabia
    "DZ":  "DZA",   # Algeria
    "SN":  "SEN",   # Senegal
    "GH":  "GHA",   # Ghana
    "CM":  "CMR",   # Cameroon
    "TN":  "TUN",   # Tunisia
    "EG":  "EGY",   # Egypt
    "ZA":  "ZAF",   # South Africa
    "NG":  "NGA",   # Nigeria
    "ML":  "MLI",   # Mali
    "CD":  "COD",   # DR Congo
    "CG":  "COG",   # Congo
    "MZ":  "MOZ",   # Mozambique
    "AO":  "AGO",   # Angola
    "ZM":  "ZMB",   # Zambia
    "RW":  "RWA",   # Rwanda
    "UG":  "UGA",   # Uganda
    "KE":  "KEN",   # Kenya
    "ET":  "ETH",   # Ethiopia
    "MG":  "MDG",   # Madagascar
    "BF":  "BFA",   # Burkina Faso
    "GW":  "GNB",   # Guinea-Bissau
    "GN":  "GIN",   # Guinea
    "LY":  "LBY",   # Libya
    "SD":  "SDN",   # Sudan
    "MR":  "MRT",   # Mauritania
    "NE":  "NER",   # Niger
    "TD":  "TCD",   # Chad
    "CF":  "CAF",   # Central African Republic
    "GA":  "GAB",   # Gabon
    "BI":  "BDI",   # Burundi
    "SZ":  "SWZ",   # Eswatini (Swaziland)
    "LS":  "LSO",   # Lesotho
    "BW":  "BWA",   # Botswana
    "NA":  "NAM",   # Namibia
    "CV":  "CPV",   # Cabo Verde
    "MU":  "MUS",   # Mauritius
    "SY":  "SYR",   # Syria
    "IQ":  "IRQ",   # Iraq
    "JO":  "JOR",   # Jordan
    "LB":  "LBN",   # Lebanon
    "PS":  "PSE",   # Palestine
    "YE":  "YEM",   # Yemen
    "OM":  "OMN",   # Oman
    "BH":  "BHR",   # Bahrain
    "QA":  "QAT",   # Qatar
    "KW":  "KWT",   # Kuwait
    "AE":  "ARE",   # UAE
    "UZ":  "UZB",   # Uzbekistan
    "KZ":  "KAZ",   # Kazakhstan
    "TM":  "TKM",   # Turkmenistan
    "TJ":  "TJK",   # Tajikistan
    "KG":  "KGZ",   # Kyrgyzstan
    "AF":  "AFG",   # Afghanistan
    "NP":  "NPL",   # Nepal
    "BD":  "BGD",   # Bangladesh
    "LK":  "LKA",   # Sri Lanka
    "MM":  "MMR",   # Myanmar
    "KH":  "KHM",   # Cambodia
    "LA":  "LAO",   # Laos
    "MN":  "MNG",   # Mongolia
    "MV":  "MDV",   # Maldives
    "BN":  "BRN",   # Brunei
    "TL":  "TLS",   # Timor-Leste
    "FJ":  "FJI",   # Fiji
    "PG":  "PNG",   # Papua New Guinea
    "SB":  "SLB",   # Solomon Islands
    "VU":  "VUT",   # Vanuatu
    "WS":  "WSM",   # Samoa
    "CK":  "COK",   # Cook Islands
    "NU":  "NIU",   # Niue
    "TO":  "TON",   # Tonga
    "KI":  "KIR",   # Kiribati
    "MH":  "MHL",   # Marshall Islands
    "FM":  "FSM",   # Micronesia
    "NR":  "NRU",   # Nauru
    "PW":  "PLW",   # Palau
    "TV":  "TUV",   # Tuvalu
    "HT":  "HTI",   # Haiti
    "JM":  "JAM",   # Jamaica
    "TT":  "TTO",   # Trinidad & Tobago
    "BB":  "BRB",   # Barbados
    "GD":  "GRD",   # Grenada
    "LC":  "LCA",   # St. Lucia
    "VC":  "VCT",   # St. Vincent
    "AG":  "ATG",   # Antigua & Barbuda
    "KN":  "KNA",   # St. Kitts & Nevis
    "DM":  "DMA",   # Dominica
    "BS":  "BHS",   # Bahamas
    "BZ":  "BLZ",   # Belize
    "SR":  "SUR",   # Suriname
    "GY":  "GUY",   # Guyana
    "CW":  "CUW",   # Curaçao
    "KS":  "XKX",   # Kosovo
    "XK":  "XKX",   # Kosovo (alt)
}


def fetch_elo_interest(
    default_interest: float = 0.10,
    min_interest: float = 0.04,
    max_interest: float = 0.45,
) -> Optional[pd.DataFrame]:
    """Derive per-country interest scores from live World Football Elo ratings.

    Logic
    -----
    Elo ratings measure national team strength from actual match results and are
    updated after every game — they naturally reflect post-2022 changes (e.g.
    Morocco's surge after their semi-final run) without any manual overrides.

    Mapping:  interest_score = default_interest * (elo / median_elo_of_all_rated_teams)

    A country whose Elo equals the global median gets exactly ``default_interest``.
    Top nations (Spain ~2157, Argentina ~2115) score proportionally higher.
    Lower-rated nations score proportionally less, clamped to ``min_interest``.
    The simulator's ``max_viewer_ratio`` caps the final viewer fraction at 90 %.

    Returns
    -------
    pd.DataFrame with columns: iso3, country, interest_score, elo
    or None on failure.
    """
    try:
        r = requests.get(_ELO_RATINGS_URL, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        _LOG.warning("Elo ratings unavailable: %s", exc)
        return None

    rows = []
    for line in r.text.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        tla = parts[2].strip().upper()
        try:
            elo = float(parts[3].strip())
        except ValueError:
            continue
        rows.append({"tla": tla, "elo": elo})

    if not rows:
        _LOG.warning("Elo ratings: no rows parsed")
        return None

    df = pd.DataFrame(rows)

    # Map TLA → ISO3
    def _tla_to_iso3(tla: str) -> Optional[str]:
        if tla in _ELO_TLA_TO_ISO3:
            return _ELO_TLA_TO_ISO3[tla]
        # Try direct ISO2 lookup (works for BR, FR, DE, JP, etc.)
        c = pycountry.countries.get(alpha_2=tla)
        if c:
            return c.alpha_3
        # Try ISO3 directly (some entries use 3-letter codes)
        c = pycountry.countries.get(alpha_3=tla)
        if c:
            return c.alpha_3
        return None

    df["iso3"] = df["tla"].apply(_tla_to_iso3)
    df = df.dropna(subset=["iso3"])

    # Deduplicate: keep highest Elo when multiple TLAs map to same ISO3
    df = df.sort_values("elo", ascending=False).drop_duplicates("iso3")

    # Anchor: median-rated country → default_interest
    median_elo = df["elo"].median()
    df["interest_score"] = (df["elo"] / median_elo * default_interest).clip(
        lower=min_interest, upper=max_interest
    )

    # Carry a readable name from pycountry
    def _iso3_to_name(iso3: str) -> str:
        c = pycountry.countries.get(alpha_3=iso3)
        return c.name if c else iso3

    df["country"] = df["iso3"].apply(_iso3_to_name)

    out = df[["iso3", "country", "interest_score", "elo"]].reset_index(drop=True)
    _LOG.info(
        "Elo interest scores: %d countries  (median Elo=%.0f → interest=%.2f)",
        len(out), median_elo, default_interest,
    )
    return out


# ---------------------------------------------------------------------------
# Sample data — development / offline testing only (python run_simulation.py --dev)
# ---------------------------------------------------------------------------
