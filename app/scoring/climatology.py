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

# Which side of the threshold counts. Snowfall, precipitation and wind are
# hazardous when they are HIGH; a minimum temperature is hazardous when it is
# LOW. A count-above-only helper cannot express the second, and the failure is
# silent rather than loud: asking it for "days at or below 32F" returns the
# complement -- the days that did NOT freeze -- which is a plausible-looking
# number attached to the wrong question.
ABOVE = "above"
BELOW = "below"

# (direction, strict) -> the operator, which is also what `evidence()` prints,
# so the reported comparison cannot drift from the one applied.
_COMPARISONS = {
    (ABOVE, False): ">=",
    (ABOVE, True): ">",
    (BELOW, False): "<=",
    (BELOW, True): "<",
}


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


def window() -> dict[str, str]:
    """The period the snapshot actually covers, as ISO dates."""
    return dict(load_history()["window"])


def historical_source() -> str:
    """The provenance label for the reanalysis, DERIVED rather than written down.

    A hardcoded "2021-2025" was shown as the source on every answer that reads
    this data while the snapshot ran to 2026-09-20 -- a false provenance claim,
    and one that contradicted the dated instruction the agent is given in the
    same request. The window is extended by re-running scripts.fetch_history,
    so the label has to come from the file.
    """
    covered = window()
    return f"Open-Meteo ECMWF IFS reanalysis, {covered['start']} to {covered['end']}"


def year_is_complete(year: str) -> bool:
    """Does the snapshot cover this calendar year end to end?

    A percentage over a partial year is not wrong, but it is not comparable to
    a full one -- and for a seasonal variable it is badly misleading, because
    the months missing from a year in progress are not a random sample of it.
    Callers use this to say so rather than leaving the reader to check the
    denominator.
    """
    covered = window()
    return covered["start"] <= f"{year}-01-01" and covered["end"] >= f"{year}-12-31"


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
    start_date: str | None = None,
    end_date: str | None = None,
    strict: bool = False,
    direction: str = ABOVE,
) -> DayCount:
    """Count days where `variable` passes `threshold` on the given side.

    The period is either a whole `year`, an explicit `start_date`/`end_date`
    range (ISO YYYY-MM-DD, inclusive), or -- given neither -- the whole
    snapshot window.

    A RANGE IS SUPPORTED BECAUSE RELATIVE PERIODS EXIST. "The last six
    months", "the 2023-24 winter season" and "since April" are ordinary
    questions that a year-only filter silently cannot answer, and the failure
    mode would be answering about a whole calendar year instead -- a wrong
    answer wearing a right answer's clothes. The caller resolves the phrase to
    dates; this function reports back exactly which dates it used, so a
    mis-resolved period is visible in the answer rather than hidden in it.

    `strict` selects the exclusive operator (`>` rather than `>=`, `<` rather
    than `<=`). The literal "days with any snowfall" question needs `> 0`;
    every operational threshold needs `>=`, so that a threshold of 1.0 inch
    includes a day with exactly 1.0 inch.

    `direction` says which side of the threshold counts, and has no default
    that is right for every variable -- see ABOVE/BELOW above.
    """
    if direction not in (ABOVE, BELOW):
        raise ValueError(f"direction must be {ABOVE!r} or {BELOW!r}, got {direction!r}")
    document = load_history()
    hubs = document["hubs"]
    if hub_id not in hubs:
        raise KeyError(f"no history for hub {hub_id!r}")

    record = hubs[hub_id]
    series = record["daily"].get(variable)
    if series is None:
        raise KeyError(f"no variable {variable!r} in the history snapshot")

    if year is not None and (start_date or end_date):
        raise ValueError("pass either year or a start_date/end_date range, not both")

    comparison = _COMPARISONS[(direction, strict)]
    if direction == ABOVE:
        passes = (lambda v: v > threshold) if strict else (lambda v: v >= threshold)
    else:
        passes = (lambda v: v < threshold) if strict else (lambda v: v <= threshold)

    matching = observed = missing = 0
    first_seen: str | None = None
    last_seen: str | None = None

    for date_str, value in zip(record["dates"], series, strict=True):
        if year is not None and not date_str.startswith(year):
            continue
        if start_date is not None and date_str < start_date:
            continue
        if end_date is not None and date_str > end_date:
            continue

        # Track the dates actually covered, so the label reports the REAL
        # period rather than the one that was requested. A range extending
        # past the snapshot must not be described as if it were covered.
        if first_seen is None:
            first_seen = date_str
        last_seen = date_str

        if value is None:
            missing += 1
            continue
        observed += 1
        if passes(value):
            matching += 1

    window = document["window"]
    if year is not None:
        label = year
    elif start_date or end_date:
        label = (
            f"{first_seen} to {last_seen}"
            if first_seen and last_seen
            else "no days in the requested range"
        )
    else:
        label = f"{window['start']} to {window['end']}"

    return DayCount(
        hub_id=hub_id,
        variable=variable,
        threshold=threshold,
        comparison=comparison,
        days_matching=matching,
        days_observed=observed,
        days_missing=missing,
        period_label=label,
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


# --------------------------------------------------- year-by-year counting --
#
# The scoring engine's historical component uses a single MEAN frequency across
# the whole window (`DayCount.days_per_year`). That is the right input to a
# score and the wrong input to an investment decision: resilience spending is
# sized against the bad year, not the average one, and a mean over five years
# hides which year was bad and by how much.
#
# Everything below is reportage. None of it reaches a score or a tier.


@dataclass(frozen=True)
class YearCount:
    """One calendar year's count, and whether the snapshot covers all of it."""

    year: str
    count: DayCount
    complete: bool


@dataclass(frozen=True)
class YearlySeries:
    """A hub's year-by-year counts for one variable and threshold.

    WHY THIS EXISTS RATHER THAN A LOOP AT THE CALL SITE. Two rules have to hold
    and both are easy to forget:

    COMPLETE YEARS ONLY for anything derived. The snapshot window ends
    mid-year, and for a seasonal variable the missing months are not a random
    sample -- a January-to-September slice of a snow year is not a small year,
    it is most of one. The partial year is still carried in `years` so a caller
    can report it with the caveat; it is excluded from `worst_year` and from
    the slope.

    NEVER COMPARE YEARS VIA `days_per_year`. That property scales matching days
    to 365, which is correct for comparing windows of different lengths and
    wrong here: applied to a partial year it inflates the count to a full-year
    equivalent, turning nine months of snow into a fictional twelve. Complete
    years share a denominator to within a leap day, so `days_matching` is the
    honest comparison.
    """

    hub_id: str
    variable: str
    threshold: float
    comparison: str
    years: tuple[YearCount, ...]

    @property
    def complete_years(self) -> tuple[YearCount, ...]:
        return tuple(year for year in self.years if year.complete)

    @property
    def partial_years(self) -> tuple[YearCount, ...]:
        return tuple(year for year in self.years if not year.complete)

    @property
    def worst_year(self) -> YearCount | None:
        """The complete year with the most qualifying days.

        A literal fact about the record, which is what makes it the useful half
        of this module: unlike a trend it asserts nothing about what happens
        next. Ties resolve to the EARLIER year, so the result is stable across
        runs rather than depending on iteration order.
        """
        complete = self.complete_years
        if not complete:
            return None
        return min(complete, key=lambda year: (-year.count.days_matching, year.year))

    @property
    def mean_days(self) -> float | None:
        """Mean qualifying days across complete years, for context against the
        worst one. None when no year is complete."""
        complete = self.complete_years
        if not complete:
            return None
        return sum(year.count.days_matching for year in complete) / len(complete)

    @property
    def slope_days_per_year(self) -> float | None:
        """Least-squares slope over complete years, in days per year.

        DELIBERATELY A NUMBER AND NOT A LABEL. Calling this "rising" or "flat"
        needs a cutoff separating the two, and there is no basis for one here:
        no outcome data, and five points. A slope with its sample size attached
        lets a reader judge it; a label decides for them on our behalf, and
        would be a new scoring constant in everything but name.

        None below five complete years.
        """
        complete = self.complete_years
        if len(complete) < 5:
            return None
        counts = [year.count.days_matching for year in complete]
        indices = list(range(len(counts)))
        mean_x = sum(indices) / len(indices)
        mean_y = sum(counts) / len(counts)
        variance = sum((x - mean_x) ** 2 for x in indices)
        if variance == 0:
            return None
        covariance = sum(
            (x - mean_x) * (y - mean_y) for x, y in zip(indices, counts, strict=True)
        )
        return covariance / variance

    def evidence(self) -> tuple[str, ...]:
        """One line per year, plus the derived facts. Every figure traces to a
        `DayCount`, so a narrator can quote these verbatim."""
        lines = [
            f"{year.year}: {year.count.days_matching} day(s) with {self.variable} "
            f"{self.comparison} {self.threshold}"
            + ("" if year.complete else " (PARTIAL YEAR -- not comparable)")
            for year in self.years
        ]
        worst = self.worst_year
        if worst is not None and worst.count.days_matching == 0:
            # Naming a "worst year" of zero days implies that year was
            # notable. For a hub where this never happens -- snow in Miami --
            # the honest statement is that the record contains no such day.
            lines.append(
                f"no day in {len(self.complete_years)} complete year(s) met this "
                f"threshold"
            )
        elif worst is not None and self.mean_days is not None:
            lines.append(
                f"worst complete year: {worst.year} with "
                f"{worst.count.days_matching} day(s), against a "
                f"{self.mean_days:.1f}-day mean across "
                f"{len(self.complete_years)} complete year(s)"
            )
        slope = self.slope_days_per_year
        if slope is not None:
            lines.append(
                f"least-squares slope across {len(self.complete_years)} complete "
                f"years: {slope:+.2f} day(s) per year"
            )
        return tuple(lines)

    def assumptions(self) -> tuple[str, ...]:
        """The disclosures that must travel with this series.

        The slope caveat is not optional. This repository already DELETED a
        scoring component for precisely this reason -- see the hurricane block
        in weights.yaml, which argues that five years cannot characterise a
        hazard's frequency. A least-squares line through five annual counts
        makes the same claim in a different costume, so it ships with the
        objection attached and never reaches a score.
        """
        notes: list[str] = []
        partial = self.partial_years
        if partial:
            labels = ", ".join(year.year for year in partial)
            verb = "that year is" if len(partial) == 1 else "those years are"
            notes.append(
                f"The snapshot does not cover all of {labels}, so {verb} reported "
                f"separately and excluded from the worst-year and trend figures. "
                f"For a seasonal variable the missing months are not a random "
                f"sample of the year."
            )
        if self.slope_days_per_year is not None:
            notes.append(
                f"The slope describes the direction of THIS "
                f"{len(self.complete_years)}-year sample and is not a forecast. "
                f"Five annual counts cannot separate a trend from ordinary "
                f"year-to-year variation -- the same objection that keeps a "
                f"historical component out of the hurricane score. It is context "
                f"only, and is not an input to any risk score or priority tier."
            )
        missing = sum(year.count.days_missing for year in self.years)
        if missing:
            notes.append(
                f"{missing} day(s) across the window had no modelled value and "
                f"were excluded from every count rather than counted as zero."
            )
        return tuple(notes)


def count_days_by_year(
    hub_id: str,
    variable: str,
    threshold: float,
    *,
    strict: bool = False,
    direction: str = ABOVE,
) -> YearlySeries:
    """Count qualifying days for each calendar year the snapshot covers.

    A loop over `available_years` calling `count_days(year=...)`, so the
    per-year numbers come from exactly the same code path as every other count
    in this system and cannot drift from it.
    """
    years = tuple(
        YearCount(
            year=year,
            count=count_days(
                hub_id,
                variable,
                threshold,
                year=year,
                strict=strict,
                direction=direction,
            ),
            complete=year_is_complete(year),
        )
        for year in available_years(hub_id)
    )
    if not years:
        raise KeyError(f"no years in the history snapshot for hub {hub_id!r}")
    return YearlySeries(
        hub_id=hub_id,
        variable=variable,
        threshold=threshold,
        comparison=years[0].count.comparison,
        years=years,
    )
