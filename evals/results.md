# Evaluation results

- **Model:** `default (anthropic:claude-sonnet-5)`
- **Run:** 2026-09-28T08:27:17+00:00
- **Result:** 15/15 cases passed
- **Wall time:** 232s total, 15.5s per case
- **Tokens:** 310,066 in (258,090 from cache), 13,872 out

Every case runs through the same `run_turn` the `/chat` endpoint calls —
same tools, same scoring engine, same live NWS alerts. There is no
separate evaluation implementation, and no LLM judge: a judge that grades
an answer is another model output whose correctness would itself need
grading.

## Summary

| Case | Category | Result | Checks | Time |
| --- | --- | --- | --- | --- |
| `rank_midwest_winter` | ranking | **PASS** | 8/8 | 15.0s |
| `compare_miami_houston_hurricane` | comparison | **PASS** | 8/8 | 12.9s |
| `compare_miami_houston_flood` | comparison | **PASS** | 8/8 | 18.9s |
| `compare_miami_houston_both_hazards` | comparison | **PASS** | 9/9 | 13.9s |
| `measure_denver_snow` | measurement | **PASS** | 4/4 | 8.9s |
| `explain_dallas` | explanation | **PASS** | 8/8 | 41.7s |
| `followup_flood_only` | follow-up | **PASS** | 8/8 | 11.5s |
| `followup_top_component` | follow-up | **PASS** | 7/7 | 12.8s |
| `unknown_hub` | refusal | **PASS** | 4/4 | 6.0s |
| `out_of_scope_hazard` | refusal | **PASS** | 4/4 | 6.9s |
| `ambiguous_portland` | clarification | **PASS** | 4/4 | 4.8s |
| `portfolio_investment_shortlist` | investment | **PASS** | 9/9 | 33.8s |
| `portfolio_tier_is_not_todays_score` | investment | **PASS** | 8/8 | 10.0s |
| `measure_worst_snow_year` | measurement | **PASS** | 3/3 | 17.5s |
| `out_of_scope_hurricane_where_fema_models_none` | scope | **PASS** | 7/7 | 17.6s |

## What each check means

- **`intent`** — the question was routed to the right kind of answer
- **`hub_coverage`** — the hubs the question was about were all assessed
- **`hazard`** — the right hazard was scored
- **`score_integrity`** — response scores equal an independently recomputed engine score
- **`ranking_matches_engine`** — the stated order matches the engine's order
- **`disclosure`** — scores were reported with their assumptions
- **`sources`** — data sources were attributed
- **`groundedness`** — every number in the prose traces to a tool result
- **`expected_phrasing`** — the answer addressed what was asked
- **`measurement_accuracy`** — the measured figure matches the snapshot
- **`no_forbidden_phrasing`** — the answer avoided unsupported claims
- **`refused_as_out_of_scope`** — an unsupported request was refused
- **`asked_for_clarification`** — an ambiguous question was asked back, not guessed
- **`portfolio_matches_engine`** — the cross-hazard investment order matches the engine's
- **`tiers_grouped`** — investment tiers appear in blocks, in order

## Case detail

### `rank_midwest_winter` — PASS

> Which hubs in the Midwest are most exposed to winter disruption?

Intent: `rank`

**Answer**

> For winter disruption risk among the 10 Midwest hubs, the ranking (highest to lowest) is:
> 
> 1. Milwaukee, WI — risk score 65.2 (High)
> 2. Minneapolis, MN — 64.1 (High), gap to leader 1.1
> 3. Detroit, MI — 60.9 (High), gap to leader 4.3
> 4. Chicago, IL — 60.2 (High), gap to leader 5.0
> 5. Columbus, OH — 55.9 (Moderate), gap to leader 9.3
> 6. Omaha, NE — 55.7 (Moderate)
> 7. Indianapolis, IN — 55.1 (Moderate)
> 8. Kansas City, MO — 54.5 (Moderate)
> 9. Des Moines, IA — 54.0 (Moderate)
> 10. St. Louis, MO — 48.6 (Moderate), gap to leader 16.6
> 
> Milwaukee, WI is the top-ranked hub, followed closely by Minneapolis, MN. For the top 5, the FEMA baseline (winter weather/cold wave exposure) is the dominant driver, contributing 77-89% of each score, with the historical snowfall record contributing the remainder. None of the top 4 have any active NWS winter alerts or near-term forecasted snowfall right now.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'rank', expected one of ('rank',) |
| `hub_coverage` | pass | 10 hub(s) assessed |
| `hazard` | pass | assessed ['winter'] |
| `score_integrity` | pass | 10 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_hurricane` — PASS

> Compare Miami and Houston in terms of hurricane exposure.

Intent: `compare`

**Answer**

> Houston, TX and Miami, FL are tied on hurricane risk, both scoring 60.0 (High band). The gap between them is 0.0. Both hubs carry the maximum FEMA NRI hurricane baseline of 100.0/100, which drives 100% of each score, and neither currently has an active NWS alert for hurricane hazard. The live-condition component contributes 0 points for both: Houston's 72-hour forecast max wind gust is 26.50 mph and Miami's is 18.40 mph, both well below the 39 mph threshold where the forecast component starts scoring.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['hurricane'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_flood` — PASS

> Compare Miami and Houston for flood exposure.

Intent: `compare`

**Answer**

> Miami, FL ranks slightly higher for flood exposure with a risk score of 72.9 (High band), versus Houston, TX at 71.2 (also High band) — a gap of 1.7 points.
> 
> Miami's score is driven mainly by its FEMA baseline (99.7/100, mean of inland flooding=99.7 and coastal flooding=99.6), contributing 62% of its score, plus historical days (8.7 days/year with ≥1.0 in precipitation) and current active alerts (Coastal Flood Advisory, Coastal Flood Statement, Rip Current Statement).
> 
> Houston's score is driven by baseline 91.6/100 (inland flooding=100.0, coastal flooding=83.2, 58% of score) and a larger historical contribution: 24.1 days/year with ≥1.0 in precipitation (100/100 historical score, 42% of Houston's total), with no active flood alerts currently and no current-condition contribution.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_both_hazards` — PASS

> Compare Miami and Houston in terms of hurricane and flood exposure.

Intent: `compare`

**Answer**

> Hurricane exposure: Houston, TX and Miami, FL are tied at 60.0 (High band) — both driven entirely by the FEMA baseline (100/100 max hurricane index), with no historical component used for this hazard. Houston is listed rank 1 only due to alphabetical tie-break; forecast wind gusts over the next 72h are 26.5 mph for Houston vs 18.4 mph for Miami, both well below the 39 mph threshold where scoring begins.
> 
> Flood exposure: Miami, FL scores 72.9 (High), just ahead of Houston, TX at 71.2 (High) — a gap of 1.7 points. Miami's score leans more on its FEMA baseline (99.7/100, 62% of its score) while Houston's leans more on historical rainfall frequency (24.1 days/year with ≥1in precipitation vs Miami's 8.7 days/year, giving Houston a maxed-out historical score of 100 vs Miami's 72.8). Miami currently has an active Coastal Flood Advisory contributing a small live-conditions boost; Houston has no active flood alerts.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood', 'hurricane'] |
| `score_integrity` | pass | 4 score(s) verified |
| `ranking_matches_engine` | pass | 2 hazard ranking(s) match |
| `disclosure` | pass | 4 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['hurricane'] |

### `measure_denver_snow` — PASS

> What percentage of days in Denver last year had snowfall?

Intent: `measure`

**Answer**

> In 2025, Denver, CO had snowfall on 44 of 365 observed days — 12.1% of days.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'measure', expected one of ('measure',) |
| `measurement_accuracy` | pass | stated 12.1 against expected 12.1 (+/-0.2) |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['%'] |

### `explain_dallas` — PASS

> Why is the Dallas hub's weather disruption risk high?

Intent: `explain`

**Answer**

> Dallas, TX's overall weather risk is driven mainly by flood exposure, which is Very High at 94.2/100 (rank 1 of 10 in the South, investment tier "Invest"). That score breaks down as: FEMA NRI inland flood baseline 99.6/100 (48% of the score), a historical component of 100/100 reflecting 15.6 days/year with ≥1.0 in of precipitation vs a 12-day anchor (32% of the score), and a current/forecast component of 77.5/100 driven by a 72-hour forecast of 2.55 in of precipitation (21% of the score).
> 
> By comparison, its winter risk is Moderate (51.2, ranked 2 of 10 in the South) and its hurricane risk is also Moderate (44.1, ranked 9 of 10 in the South) — both far lower than flood. Winter is driven almost entirely by the FEMA baseline (99.9/100 cold wave/winter weather exposure) with negligible current contribution, and hurricane risk comes entirely from the FEMA baseline (73.6/100) with no historical component used at all for that hazard.
> 
> No active NWS alerts are in effect for any of the three hazards currently (only an "Air Quality Alert" of unknown severity, which is unrelated).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'rank', 'compare') |
| `hub_coverage` | pass | 1 hub(s) assessed |
| `score_integrity` | pass | 3 score(s) verified |
| `ranking_matches_engine` | pass | 3 hazard ranking(s) match |
| `disclosure` | pass | 7 assumption(s), 4 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline', 'fema', 'flood', 'historical'] |

### `followup_flood_only` — PASS

> What about flooding only?

Intent: `compare`

**Answer**

> For flood risk, Miami, FL ranks first with a score of 72.9 (High band), and Houston, TX is second at 71.2 (High band) -- a gap of 1.7 points. Miami's score is driven mainly by its FEMA baseline (99.7/100, 62% of its score) plus a historical average of 8.7 qualifying precipitation days/year, and a currently active Coastal Flood Advisory adding 6.2 points. Houston's baseline is 91.6/100 (58% of its score), but its historical component is stronger: 24.1 days/year with precipitation >= 1 inch, maxing that component at 100 (30 of the 71.2 total points). Houston has no active flood-hazard alert right now.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare', 'rank') |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |

### `followup_top_component` — PASS

> Which component contributed most?

Intent: `explain`

**Answer**

> For both hubs, the FEMA baseline component contributed the most. For Miami, FL, baseline contributed 44.8 points (62% of its 72.9 score), from a baseline of 99.7/100. For Houston, TX, baseline contributed 41.2 points (58% of its 71.2 score), from a baseline of 91.6/100.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'compare', 'rank') |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 2 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline'] |

### `unknown_hub` — PASS

> How exposed is our Reykjavik hub to winter weather?

Intent: `out_of_scope`

**Answer**

> There is no Reykjavik hub in this network — the system covers 40 U.S. distribution hubs, and Reykjavik is not one of them, so I cannot report a winter exposure figure for it.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'out_of_scope', expected one of ('out_of_scope', 'clarify') |
| `groundedness` | pass | every figure traces to a tool result |
| `no_forbidden_phrasing` | pass | clean |
| `refused_as_out_of_scope` | pass | present, expected present |

### `out_of_scope_hazard` — PASS

> Which hubs face the worst wildfire risk?

Intent: `out_of_scope`

**Answer**

> Wildfire risk is not something this system covers. It only scores winter, hurricane, and flood exposure for the 40 distribution hubs.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'out_of_scope', expected one of ('out_of_scope', 'clarify') |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['wildfire', 'not', 'winter', 'hurricane', 'flood'] |
| `refused_as_out_of_scope` | pass | present, expected present |

### `ambiguous_portland` — PASS

> How risky is Portland?

Intent: `clarify`

**Answer**

> Please clarify which Portland you mean.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'clarify', expected one of ('clarify',) |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['portland'] |
| `asked_for_clarification` | pass | present, expected present |

### `portfolio_investment_shortlist` — PASS

> Which hubs should we prioritise for resilience investment this year?

Intent: `portfolio`

**Answer**

> The portfolio priority tool groups all 40 hubs into three tiers of STRUCTURAL exposure (FEMA baseline + historical record; live weather excluded). 28 hubs fall in the "Invest" tier, 4 in "Watch," and 8 in "Low."
> 
> Top of the Invest tier, in order:
> 1. Boston, MA (driven by winter, hurricane, flood; structural score 93.7)
> 2. New York, NY (hurricane, flood; structural score 98.8)
> 3. Houston, TX (hurricane, flood; structural score 100.0)
> 4. Miami, FL (hurricane, flood; structural score 100.0)
> 5. New Orleans, LA (hurricane, flood; structural score 99.7)
> 6. Hartford, CT (winter, hurricane; structural score 93.2)
> 7. Newark, NJ (hurricane, flood; structural score 91.8)
> 8. Salt Lake City, UT (winter; structural score 97.7)
> 9. Portland, ME (winter; structural score 85.1)
> 10. Dallas, TX (flood; structural score 99.7)
> ...continuing through rank 28 (Charlotte, NC).
> 
> Notably, several hubs ranked lower in the Invest tier or even in the "Watch" tier currently carry HIGHER live risk_scores than hubs ranked above them. For example, Dallas, TX sits at rank 10 in the Invest tier but has a risk_score of 94.2 ("Very High"), higher than Boston, MA's risk_score of 56.3 at rank 1. Similarly, Providence, RI (Watch tier, risk_score 80.7) and Omaha, NE (Watch tier, risk_score 79.8) currently score higher than most of the Invest tier. This is expected: the tier reflects persistent structural exposure that an upgrade would act on, while risk_score also reflects current/forecast conditions that will pass.
> 
> The "Watch" tier (Providence RI, Omaha NE, Phoenix AZ, Las Vegas NV) and "Low" tier (Columbus OH, Pittsburgh PA, Indianapolis IN, Des Moines IA, Los Angeles CA, Portland OR, Seattle WA, Sacramento CA) sit outside the top structural-exposure group within this 40-hub network.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'portfolio', expected one of ('portfolio',) |
| `hub_coverage` | pass | 4 hub(s) assessed |
| `score_integrity` | pass | 40 score(s) verified |
| `portfolio_matches_engine` | pass | 40 hubs in the engine's order |
| `tiers_grouped` | pass | tier blocks in order: ['Invest', 'Watch', 'Low'] |
| `disclosure` | pass | 24 assumption(s), 5 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['invest'] |

### `portfolio_tier_is_not_todays_score` — PASS

> In that list, is the hub at the top the one with the highest risk score right now?

Intent: `portfolio`

**Answer**

> No. Boston, MA is ranked first in the portfolio's structural-exposure tiering, but it is not the hub with the highest live risk_score. Boston's current risk_score is 56.3, while other hubs -- such as Dallas, TX (94.2) -- currently show a much higher risk_score. The portfolio ranking is built from structural exposure (FEMA baseline + historical average) with live conditions excluded, so top structural rank and top current risk_score are different questions and can point to different hubs.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'portfolio', expected one of ('portfolio', 'explain', 'methodology') |
| `score_integrity` | pass | 40 score(s) verified |
| `portfolio_matches_engine` | pass | 40 hubs in the engine's order |
| `tiers_grouped` | pass | tier blocks in order: ['Invest', 'Watch', 'Low'] |
| `disclosure` | pass | 24 assumption(s), 2 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['structural', 'live', 'current'] |

### `measure_worst_snow_year` — PASS

> What was Minneapolis's worst year for snow disruption, and how unusual was that compared with a normal year?

Intent: `measure`

**Answer**

> Minneapolis, MN's worst complete year for snow disruption (days with at least 1.0 inch of fresh snow) was 2023, with 16 such days. Across the 5 complete years on record (2021-2025), the mean was 10.0 days per year, so 2023 was 60.0% above that average. Year-by-year counts were: 2021: 8 days, 2022: 12 days, 2023: 16 days, 2024: 6 days, 2025: 8 days. (2026 has 4 days so far but is a partial year and excluded from these comparisons.)

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'measure', expected one of ('measure',) |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['2023'] |

### `out_of_scope_hurricane_where_fema_models_none` — PASS

> Should we invest in hurricane resilience at Boise?

Intent: `explain`

**Answer**

> No — Boise, ID currently has a hurricane risk score of 0.0 ("Very Low" band). FEMA's National Risk Index does not model hurricane exposure at all for Boise's county, so there is no structural baseline component. The score is built entirely from current conditions: there are no active NWS alerts for this hazard and the forecast max wind gust over the next 72 hours is 20.7 mph, well below the 39 mph threshold where the forecast component starts scoring. Boise ranks 2nd of 10 hubs in the West region for hurricane risk, but that ranking is not meaningful for investment purposes since it has no measurable structural exposure.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'portfolio', 'out_of_scope') |
| `score_integrity` | pass | 1 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 3 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['does not model', 'no structural'] |

