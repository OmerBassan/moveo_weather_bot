"""Day-counting over the frozen climatology. Pure, no I/O beyond one file read.

This module answers two DIFFERENT kinds of question from the same data, and
keeping them separate is the point:

  A LITERAL MEASUREMENT   "what percentage of days in Denver in 2025 had
                          snowfall?" -- a count the user asked for, at the
                          threshold the user implied (any snowfall at all).
  A RISK INPUT            "how often is this hub disrupted by snow?" -- a
                          count at an operational threshold, feeding the
                          scoring engine.

They give different numbers from the same series and both are correct. A
system that reports only one is either answering a question nobody asked or
modelling disruption from trace dustings.

Missing days are counted and reported, never silently treated as zero. A null
in the series means the model returned no value, which is not the same as
"no snow fell".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import load_config

# Snapshot variable names, so no caller spells them as string literals.
SNOWFALL = "snowfall_in"
PRECIPITATION = "precipitation_in"
TEMP_MIN = "temp_min_f"
WIND_GUST_MAX = "wind_gust_max_mph"


@dataclass(frozen=True)
class DayCount:
    """The result of counting days above a threshold, with its own denominator.

    `percent_of_observed` divides by observed days, not calendar days, so a
    gap in the data cannot quietly deflate a frequency. Where days are missing
    the two denominators differ and the caller can say so.
    """

    hub_id: str
    variable: str
    threshold: float
    comparison: str
    days_matching: int
    days_observed: int
    days_missing: int
    period_label: str

    @property
    def days_total(self) -> int:
        return self.days_observed + self.days_missing

    @property
    def percent_of_observed(self) -> float:
        if self.days_observed == 0:
            return 0.0
        return self.days_matching / self.days_observed * 100.0

    @property
    def days_per_year(self) -> float:
        """Matching days scaled to a 365-day year, for comparing windows of
        different lengths."""
        if self.days_observed == 0:
            return 0.0
        return self.days_matching / self.days_observed * 365.0

    def evidence(self) -> str:
        """One line a narrator can quote verbatim; every figure in it is from
        this object, so nothing here can be a model's invention."""
        base = (
            f"{self.days_matching} of {self.days_observed} observed days "
            f"({self.percent_of_observed:.1f}%) in {self.period_label} had "
            f"{self.variable} {self.comparison} {self.threshold}"
        )
        if self.days_missing:
            base += f"; {self.days_missing} day(s) had no modelled value"
        return base


@lru_cache(maxsize=1)
def load_history(path: Path | None = None) -> dict[str, Any]:
    target = path or load_config().history_snapshot_path
    return json.loads(target.read_text(encoding="utf-8"))


def available_years(hub_id: str) -> tuple[str, ...]:
    document = load_history()
    dates = document["hubs"][hub_id]["dates"]
    return tuple(sorted({d[:4] for d in dates}))


def count_days(
    hub_id: str,
    variable: str,
    threshold: float,
    *,
    year: str | None = None,
    strict: bool = False,
) -> DayCount:
    """Count days where `variable` exceeds `threshold`.

    `strict` selects `>` over `>=`. The literal "days with any snowfall"
    question needs `> 0`; every operational threshold needs `>=`, so that a
    threshold of 1.0 inch includes a day with exactly 1.0 inch.
    """
    document = load_history()
    hubs = document["hubs"]
    if hub_id not in hubs:
        raise KeyError(f"no history for hub {hub_id!r}")

    record = hubs[hub_id]
    series = record["daily"].get(variable)
    if series is None:
        raise KeyError(f"no variable {variable!r} in the history snapshot")

    matching = observed = missing = 0
    for date_str, value in zip(record["dates"], series, strict=True):
        if year is not None and not date_str.startswith(year):
            continue
        if value is None:
            missing += 1
            continue
        observed += 1
        if (value > threshold) if strict else (value >= threshold):
            matching += 1

    window = document["window"]
    return DayCount(
        hub_id=hub_id,
        variable=variable,
        threshold=threshold,
        comparison=">" if strict else ">=",
        days_matching=matching,
        days_observed=observed,
        days_missing=missing,
        period_label=year or f"{window['start']} to {window['end']}",
    )


def sum_variable(hub_id: str, variable: str, *, year: str | None = None) -> float:
    """Total of a variable over the period, skipping missing days."""
    document = load_history()
    record = document["hubs"][hub_id]
    total = 0.0
    for date_str, value in zip(record["dates"], record["daily"][variable], strict=True):
        if year is not None and not date_str.startswith(year):
            continue
        if value is not None:
            total += value
    return total


def provenance(hub_id: str) -> dict[str, Any]:
    """The grid cell this hub's numbers actually came from."""
    return load_history()["hubs"][hub_id]["provenance"]
