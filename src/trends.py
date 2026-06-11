"""Google Trends interest-by-country fetcher (pytrends).

Fetches country-level search interest (0-1) for given query terms.
Used as a proxy for World Cup viewership interest by country.

Limitations
-----------
- Google Trends is unofficial and may rate-limit heavy use.
- Interest scores are relative to the highest-interest country (100).
- Data represents *search* interest, not TV viewership directly.
"""
import logging
from typing import List, Optional

_LOG = logging.getLogger(__name__)


def fetch_trends_interest(
    terms: List[str],
    timeframe: str = "today 12-m",
) -> Optional[object]:
    """Fetch country-level search interest from Google Trends.

    Parameters
    ----------
    terms     : query terms (e.g. ["World Cup", "football"])
    timeframe : pytrends timeframe string (default: last 12 months)

    Returns
    -------
    pd.DataFrame with columns iso3, country, interest_score (0-1),
    or None if unavailable.
    """
    try:
        import pandas as pd
        import pycountry
        from pytrends.request import TrendReq
    except ImportError as exc:
        _LOG.warning("pytrends unavailable: %s", exc)
        return None

    try:
        pt = TrendReq(hl="en-US", tz=0)
        pt.build_payload(terms, timeframe=timeframe)
        df = pt.interest_by_region(resolution="COUNTRY", inc_low_vol=True, inc_geo_code=True)
    except Exception as exc:
        _LOG.warning("Google Trends request failed: %s", exc)
        return None

    if df is None or df.empty:
        return None

    df = df.reset_index()

    # Identify columns
    geo_col  = "geoCode"  if "geoCode"  in df.columns else None
    name_col = "geoName"  if "geoName"  in df.columns else None
    term_cols = [c for c in df.columns if c not in ("geoCode", "geoName")]

    # Mean interest across all terms
    df["interest_raw"] = df[term_cols].mean(axis=1)

    # Map to ISO3
    def _geo_to_iso3(row):
        # Prefer geo code (ISO2) over name
        if geo_col and pd.notna(row.get(geo_col)):
            c = pycountry.countries.get(alpha_2=str(row[geo_col]).upper())
            if c:
                return c.alpha_3
        if name_col and pd.notna(row.get(name_col)):
            try:
                return pycountry.countries.lookup(str(row[name_col])).alpha_3
            except LookupError:
                pass
        return None

    df["iso3"]    = df.apply(_geo_to_iso3, axis=1)
    df["country"] = df[name_col] if name_col else None

    out = (
        df[["iso3", "country", "interest_raw"]]
        .rename(columns={"interest_raw": "interest_score"})
        .dropna(subset=["iso3"])
        .copy()
    )
    # Normalize from 0-100 → 0-1
    out["interest_score"] = (out["interest_score"].astype(float) / 100.0).clip(0.0, 1.0)
    _LOG.info("Fetched Trends interest for %d countries", len(out))
    return out.reset_index(drop=True)


def fetch_trends_interest(terms: List[str], timeframe: str = "today 12-m", source: str = "pytrends", local_path: Optional[str] = None):
    """Fetch country-level interest scores.

    Parameters:
    - terms: list of query terms
    - timeframe: pytrends timeframe string
    - source: one of 'pytrends', 'csv', or 'sample'
    - local_path: path to CSV when source == 'csv'

    Returns a pandas.DataFrame with columns: `iso3`, `country`, `interest_score` (0-1), or None on failure.
    """
    if source == "sample":
        return None

    if source == "csv":
        try:
            import pandas as pd
            import pycountry
        except Exception as e:
            _LOG.warning("CSV trends fallback requires pandas and pycountry: %s", e)
            return None
        if not local_path:
            _LOG.warning("No local_path provided for trends CSV source")
            return None
        try:
            df = pd.read_csv(local_path)
        except Exception as e:
            _LOG.warning("Failed reading trends CSV %s: %s", local_path, e)
            return None
        # normalize
        cols = {c.lower(): c for c in df.columns}
        if "iso3" in cols:
            iso3_col = cols["iso3"]
            country_col = cols.get("country")
        elif "iso2" in cols:
            iso2_col = cols["iso2"]
            # convert iso2 -> iso3
            def _iso2_to_iso3(code):
                try:
                    return pycountry.countries.get(alpha_2=code.upper()).alpha_3
                except Exception:
                    return None
            df["iso3"] = df[iso2_col].apply(_iso2_to_iso3)
            iso3_col = "iso3"
            country_col = cols.get("country")
        else:
            # try mapping by country name
            if "country" in cols:
                country_col = cols["country"]
                import pycountry as _pc
                def _name_to_iso3(name):
                    try:
                        c = _pc.countries.lookup(name)
                        return c.alpha_3
                    except Exception:
                        return None
                df["iso3"] = df[country_col].apply(_name_to_iso3)
                iso3_col = "iso3"
            else:
                _LOG.warning("Trends CSV missing iso2/iso3/country columns")
                return None

        # find interest column
        interest_col = None
        for candidate in ("interest", "interest_score", "value", "score"):
            if candidate in cols:
                interest_col = cols[candidate]
                break
        if interest_col is None:
            # pick the first numeric column not iso/country
            for c in df.columns:
                if c not in (iso3_col, country_col) and pd.api.types.is_numeric_dtype(df[c]):
                    interest_col = c
                    break
        if interest_col is None:
            _LOG.warning("No numeric interest column found in trends CSV")
            return None

        out = df[[iso3_col, country_col, interest_col]].rename(columns={iso3_col: "iso3", country_col: "country", interest_col: "interest_score"})
        # normalize 0-100 -> 0-1 if needed
        try:
            out["interest_score"] = out["interest_score"].astype(float)
            if out["interest_score"].max() > 1.01:
                out["interest_score"] = out["interest_score"] / 100.0
        except Exception:
            _LOG.warning("Failed to normalize interest scores from CSV")
            return None
        out = out.dropna(subset=["iso3"])
        return out[["iso3", "country", "interest_score"]]

    # default: pytrends
    try:
        from pytrends.request import TrendReq
        import pycountry
        import pandas as pd
    except Exception as e:
        _LOG.warning("pytrends or dependencies not available: %s", e)
        return None

    try:
        pytrends = TrendReq(hl="en-US", tz=0)
        pytrends.build_payload(terms, timeframe=timeframe)
        # get interest by region (country resolution); include geo code when available
        df = pytrends.interest_by_region(resolution="COUNTRY", inc_low_vol=True, inc_geo_code=True)
        if df is None or df.empty:
            return None
        # df may include a 'geoCode' column (iso2) after inc_geo_code=True
        df_reset = df.reset_index()
        # aggregate interest across terms by taking the mean
        term_cols = [c for c in df_reset.columns if c not in ("geoCode", "geoName")]
        # if geoCode exists, use it for mapping; otherwise map country name
        if "geoCode" in df_reset.columns:
            df_reset["iso2"] = df_reset["geoCode"]
        # determine country name column
        if "geoName" in df_reset.columns:
            df_reset["country"] = df_reset["geoName"]
        elif df_reset.index.name:
            df_reset["country"] = df_reset.index
        else:
            df_reset["country"] = df_reset["country"] if "country" in df_reset.columns else df_reset.index

        # compute mean interest across terms
        df_reset["interest_raw"] = df_reset[term_cols].mean(axis=1)

        # map iso2 -> iso3
        def _iso2_to_iso3(code):
            try:
                if not code or pd.isna(code):
                    return None
                c = pycountry.countries.get(alpha_2=code.upper())
                return c.alpha_3 if c else None
            except Exception:
                return None

        if "iso2" in df_reset.columns:
            df_reset["iso3"] = df_reset["iso2"].apply(_iso2_to_iso3)
        else:
            # try mapping by country name
            def _name_to_iso3(name):
                try:
                    c = pycountry.countries.lookup(name)
                    return c.alpha_3
                except Exception:
                    return None
            df_reset["iso3"] = df_reset["country"].apply(_name_to_iso3)

        out = df_reset[["iso3", "country", "interest_raw"]].dropna(subset=["iso3"]).copy()
        out = out.rename(columns={"interest_raw": "interest_score"})
        # normalize 0-100 -> 0-1
        out["interest_score"] = out["interest_score"].astype(float) / 100.0
        out = out.reset_index(drop=True)
        return out
    except Exception as e:
        _LOG.warning("pytrends request failed: %s", e)
        return None
