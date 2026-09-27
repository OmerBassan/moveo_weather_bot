"""Single typed configuration object, constructed in exactly one place.

Every external endpoint, path, window and threshold used anywhere in this
project is named here. Nothing else in the codebase may hardcode a URL, a
FIPS-handling rule or a magic number at a call site: the build scripts and
the runtime application must agree on what a snapshot contains, and the only
way to guarantee that is for both to read the same object.

Env-overridable so a run can be retargeted (a different history window, a
different snapshot directory) without a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Repository root: this file is app/config.py, so parents[1] is the root.
ROOT = Path(__file__).resolve().parents[1]

# Loaded here because this module is the one construction site for
# configuration, and every entry point (API, UI, scripts, evals) imports it.
# `override=False` so a real environment variable always beats the file --
# a deployed container sets its own secrets and must not be overridden by a
# .env that happened to be copied in.
load_dotenv(ROOT / ".env", override=False)


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


# --------------------------------------------------------------- FEMA NRI --

# The county-level layer of the National Risk Index FeatureServer. Public,
# anonymous, no key. Layer 0 is the only layer this service exposes.
NRI_QUERY_URL = (
    "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
    "National_Risk_Index_Counties/FeatureServer/0/query"
)

# The 5-digit state+county FIPS column. Verified against the v1.20.0 data
# dictionary; note that GEOID is NOT present in this layer.
NRI_FIPS_FIELD = "STCOFIPS"

# Hazard code -> the NRI field prefix. IFLD, not RFLD: FEMA's field prefix for
# riverine/inland flooding is IFLD even where the documentation says
# "Riverine Flooding".
NRI_HAZARD_CODES: dict[str, str] = {
    "winter_weather": "WNTW",
    "cold_wave": "CWAV",
    "hurricane": "HRCN",
    "inland_flooding": "IFLD",
    "coastal_flooding": "CFLD",
}

# Per-hazard suffixes we keep. _RISKS is the 0-100 nationally comparable
# percentile score and is the only number the scoring engine consumes;
# _RISKR is FEMA's own text rating and is carried purely as displayable
# provenance. The Expected Annual Loss family (_EALS/_EALT) is deliberately
# NOT collected: it introduces a dollar dimension this system does not model.
NRI_RISK_SCORE_SUFFIX = "_RISKS"
NRI_RISK_RATING_SUFFIX = "_RISKR"

# Composite context fields, carried for display, never scored.
NRI_COMPOSITE_FIELDS = ("RISK_SCORE", "RISK_RATNG")


# ------------------------------------------------------------- Open-Meteo --

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# THE MODEL IS PINNED, because the default is not a model -- it is Open-Meteo's
# "best match" blend, which stitches ECMWF IFS (9 km, 2017+), ERA5 (25 km,
# 1940+) and ERA5-Land together. A blend is reproducible only by accident: the
# seam moves when the window moves, and two runs of this project could then
# rank hubs off different physics without anything reporting it.
#
# Verified empirically rather than assumed, because the models disagree
# materially. Denver, 2025-01-01, daily snowfall_sum:
#
#     default      0.00 cm      <- identical to ecmwf_ifs
#     ecmwf_ifs    0.00 cm
#     era5         1.89 cm
#     era5_land    null         <- snowfall_sum is not served by this model
#
# ecmwf_ifs is chosen for the 2021-2025 window: it is the highest resolution
# available (9 km, so the least spatial smoothing of the lake-effect and
# orographic bands this system cares about) and it covers the whole window
# uniformly, so there is no seam inside it. A window reaching before 2017 must
# switch to era5 -- and would then produce systematically different snowfall,
# which is a re-baselining, not a config tweak.
OPEN_METEO_MODEL = _env_str("WRA_OPEN_METEO_MODEL", "ecmwf_ifs")

# Daily aggregation uses the hub's LOCAL day, not UTC. A hub is disrupted on
# the day its own staff could not work, and a UTC boundary shifts a Denver
# snowfall onto the wrong calendar day (verified: the same storm lands on
# 2025-01-04 local and 2025-01-05 UTC).
OPEN_METEO_TIMEZONE = "auto"

# The four daily variables that feed the three modelled hazards:
#   snowfall_sum + temperature_2m_min -> winter
#   precipitation_sum                 -> flooding
#   wind_gusts_10m_max                -> hurricane
OPEN_METEO_DAILY_VARS = (
    "snowfall_sum",
    "precipitation_sum",
    "temperature_2m_min",
    "wind_gusts_10m_max",
)

# Open-Meteo returns cm / mm / degC / kmh by default. We normalise to US units
# at the tool boundary -- an analyst reading "2.3 cm of snow" for a Denver
# distribution hub is a credibility leak -- and record the source unit in
# provenance so the conversion is auditable rather than assumed.
CM_TO_INCH = 0.393701
MM_TO_INCH = 0.0393701
KMH_TO_MPH = 0.621371

# Attribution required by Open-Meteo's CC BY 4.0 licence wherever the data is
# displayed. Rendered by the UI; kept here so there is one authoritative string.
OPEN_METEO_ATTRIBUTION_TEXT = "Weather data by Open-Meteo.com"
OPEN_METEO_ATTRIBUTION_URL = "https://open-meteo.com/"


# ---------------------------------------------------------------- NWS API --

NWS_API_ROOT = "https://api.weather.gov"

# NWS requires an identifying User-Agent and throttles generic ones (a bare
# python-httpx/* is a likely block). Contact info is included so NWS can reach
# the operator, which is exactly what the field is documented to be for.
NWS_USER_AGENT = _env_str(
    "WRA_NWS_USER_AGENT",
    "(moveo-weather-risk-agent, omer.m.bassan@gmail.com)",
)


def same_to_fips(same_code: str) -> str:
    """Convert an NWS SAME code to a 5-digit county FIPS code.

    SAME is `0` + the 5-digit FIPS, so this is a SLICE and never a strip.
    `"005133".lstrip("0")` yields `"5133"`, which would silently fail to match
    every county whose FIPS begins with a zero -- all of AL, AK, AZ, AR, CA,
    CO, CT, DE, DC, FL and GA. That failure would surface as "no active alerts
    in the West", which reads as low risk rather than as a bug.
    """
    if len(same_code) != 6 or not same_code.isdigit():
        raise ValueError(f"SAME code must be 6 digits, got {same_code!r}")
    return same_code[1:]


# ------------------------------------------------------- scoring behaviour --

# Two DIFFERENT definitions of a snow day, on purpose.
#
# A user asking "what percentage of days in Denver last year had snowfall?" is
# asking literally, so the measurement path counts any day with snowfall > 0
# and reports it as such.
#
# The risk engine asks a different question -- how often is this hub actually
# disrupted -- and a trace dusting does not shut a distribution hub. 1.0 inch
# of fresh snowfall is the threshold operational road and airport guidance
# conventionally uses, so the engine uses that rather than a number tuned to
# make the output look right.
#
# THE THRESHOLD ALSO GUARDS A REAL PROPERTY OF THE DATA. Reanalysis products
# in the ERA5 family are documented to spread precipitation across too many
# days while under-representing high-intensity events, and a grid cell reports
# a small positive value whenever snow fell ANYWHERE in it. So a positive
# `snowfall_sum` means "snow occurred somewhere in a 9 km cell", not "snow
# accumulated at the hub". Measured here: Denver shows 44-60 days/year with
# any modelled snowfall against a published station normal of ~30 days with
# >= 0.1 inch. Counting every nonzero day therefore overstates snow days,
# which is exactly what the literal question asks for and exactly what the
# risk model must not use.
#
# NOT A BIAS CORRECTION. No multiplier is applied to the data anywhere. A
# single multiplicative correction would fix an annual mean while making event
# counts worse -- if a model produces too many weak days and too few heavy
# ones, scaling everything up just inflates the marginal days.
SNOW_DAY_LITERAL_THRESHOLD_IN = 0.0
SNOW_DAY_DISRUPTION_THRESHOLD_IN = _env_float("WRA_SNOW_DAY_THRESHOLD_IN", 1.0)


@dataclass(frozen=True)
class AppConfig:
    """Constructed once, by `load_config()`."""

    # ---- where the frozen snapshots live -------------------------------
    hubs_path: Path
    nri_snapshot_path: Path
    history_snapshot_path: Path
    raw_dir: Path

    # ---- the historical baseline window --------------------------------
    # Five years rather than one: a single year of snowfall is noisy enough
    # that a mild winter would reorder the hub ranking, and a baseline that
    # reorders annually is not a baseline.
    history_start_date: str
    history_end_date: str

    # ---- HTTP ----------------------------------------------------------
    http_timeout_seconds: int
    http_max_retries: int
    nws_user_agent: str = field(default=NWS_USER_AGENT)


def load_config() -> AppConfig:
    """The one construction site."""
    data_dir = Path(_env_str("WRA_DATA_DIR", str(ROOT / "data")))
    return AppConfig(
        hubs_path=data_dir / "hubs.json",
        nri_snapshot_path=data_dir / "nri_snapshot.json",
        history_snapshot_path=data_dir / "history_snapshot.json",
        raw_dir=data_dir / "raw",
        history_start_date=_env_str("WRA_HISTORY_START", "2021-01-01"),
        # Open-Meteo's archive lags ~5 days, so this is set a little further
        # back than that. It is an explicit date rather than a computed
        # "today - 6", because the fetch script discards a snapshot whose
        # window differs from the requested one: a window that moved every
        # run would refetch all 40 hubs every time and could never resume.
        # Re-run scripts.fetch_history with WRA_HISTORY_END set forward to
        # extend the record.
        history_end_date=_env_str("WRA_HISTORY_END", "2026-09-20"),
        http_timeout_seconds=_env_int("WRA_HTTP_TIMEOUT", 60),
        http_max_retries=_env_int("WRA_HTTP_MAX_RETRIES", 3),
    )
