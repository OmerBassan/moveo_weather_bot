# Evaluation results

- **Model:** `default (anthropic:claude-sonnet-5)`
- **Run:** 2026-09-27T13:02:11+00:00
- **Result:** 10/10 cases passed
- **Wall time:** 101s total, 10.1s per case
- **Tokens:** 113,627 in (95,016 from cache), 6,712 out

Every case runs through the same `run_turn` the `/chat` endpoint calls —
same tools, same scoring engine, same live NWS alerts. There is no
separate evaluation implementation, and no LLM judge: a judge that grades
an answer is another model output whose correctness would itself need
grading.

## Summary

| Case | Category | Result | Checks | Time |
| --- | --- | --- | --- | --- |
| `rank_midwest_winter` | ranking | **PASS** | 8/8 | 12.7s |
| `compare_miami_houston_hurricane` | comparison | **PASS** | 8/8 | 14.2s |
| `compare_miami_houston_flood` | comparison | **PASS** | 8/8 | 10.5s |
| `measure_denver_snow` | measurement | **PASS** | 4/4 | 10.0s |
| `explain_dallas` | explanation | **PASS** | 8/8 | 24.8s |
| `followup_flood_only` | follow-up | **PASS** | 8/8 | 10.5s |
| `followup_top_component` | follow-up | **PASS** | 7/7 | 5.8s |
| `unknown_hub` | refusal | **PASS** | 4/4 | 4.6s |
| `out_of_scope_hazard` | refusal | **PASS** | 4/4 | 4.1s |
| `ambiguous_portland` | clarification | **PASS** | 4/4 | 4.4s |

## What each check means

- **`intent`** — the question was routed to the right kind of answer
- **`hub_coverage`** — the hubs the question was about were all assessed
- **`hazard`** — the right hazard was scored
- **`score_integrity`** — response scores equal an independently recomputed engine score
- **`ranking_matches_engine`** — the stated order matches the engine's order
- **`disclosure`** — scores were reported with their assumptions
- **`sources`** — data sources were attributed
- **`groundedness`** — every number in the prose traces to a tool result
- **`measurement_accuracy`** — the measured figure matches the snapshot
- **`expected_phrasing`** — the answer addressed what was asked
- **`no_forbidden_phrasing`** — the answer avoided unsupported claims
- **`refused_as_out_of_scope`** — an unsupported request was refused
- **`asked_for_clarification`** — an ambiguous question was asked back, not guessed

## Case detail

### `rank_midwest_winter` — PASS

> Which hubs in the Midwest are most exposed to winter disruption?

Intent: `rank`

**Answer**

> Ranked by winter risk score (highest first), the 10 Midwest hubs are:
> 1. Milwaukee, WI — 66.2 (High)
> 2. Minneapolis, MN — 64.9 (High)
> 3. Detroit, MI — 61.6 (High)
> 4. Chicago, IL — 60.8 (High)
> 5. Columbus, OH — 56.5 (Moderate)
> 6. Omaha, NE — 55.7 (Moderate)
> 7. Indianapolis, IN — 55.3 (Moderate)
> 8. Kansas City, MO — 55.2 (Moderate)
> 9. Des Moines, IA — 54.3 (Moderate)
> 10. St. Louis, MO — 48.7 (Moderate)
> 
> Milwaukee leads the region; Milwaukee, Minneapolis, Detroit, and Chicago are the only "High" band hubs. Both Milwaukee and Chicago currently have an active NWS Special Weather Statement (Moderate severity).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'rank', expected one of ('rank',) |
| `hub_coverage` | pass | 10 hub(s) assessed |
| `hazard` | pass | assessed ['winter'] |
| `score_integrity` | pass | 10 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 2 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_hurricane` — PASS

> Compare Miami and Houston in terms of hurricane exposure.

Intent: `compare`

**Answer**

> Houston, TX and Miami, FL both score 60.0/100 for hurricane risk (High band), tied at rank 1 and 2 with a gap of 0.0 between them. Both scores come entirely from the FEMA NRI hurricane baseline (100.0/100, the max value), with no contribution from current NWS alerts specific to hurricane hazard. Miami currently has active alerts (Coastal Flood Advisory - Minor, Rip Current Statement - Moderate, Coastal Flood Statement - Minor) and Houston has an Air Quality Alert (Unknown severity), but none of these count toward the hurricane score.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['hurricane'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 2 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_flood` — PASS

> Compare Miami and Houston for flood exposure.

Intent: `compare`

**Answer**

> Miami, FL ranks slightly higher for flood risk with a score of 73.1 (High band) vs Houston, TX at 71.2 (also High band) — a gap of 1.9 points. Miami's score is driven mostly by its FEMA baseline (99.7/100, 61% of its score) plus 3 active NWS alerts (Coastal Flood Advisory, Rip Current Statement, Coastal Flood Statement). Houston's baseline is lower (91.6/100) but its historical heavy-precipitation record is worse: 23.2 qualifying days/year vs Miami's 8.8 days/year, and it currently has no active flood-related alerts (only an unrelated Air Quality Alert).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `measure_denver_snow` — PASS

> What percentage of days in Denver last year had snowfall?

Intent: `measure`

**Answer**

> In 2025, Denver, CO had snowfall on 44 of 365 observed days (12.1%).

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

> Dallas, TX shows different risk levels by hazard: flood risk is High at 74.8/100 (ranked 1 of 10 South hubs), winter risk is Moderate at 51.4/100 (ranked 2 of 10), and hurricane risk is Moderate at 44.1/100 (ranked 9 of 10).
> 
> For flood: FEMA's NRI inland flooding baseline is 99.6/100 (60% of the score), and the historical record shows 16.8 days/year with precipitation >= 1.0 in. (84 of 1826 observed days, 4.6%), which maxes the historical component (30 pts, 40% of score).
> 
> For winter: FEMA's baseline (max of winter weather=98.7, cold wave=99.9) drives 97% of the score; only 1.0 snow day/year (5 of 1826 days) was observed historically, contributing just 3%.
> 
> For hurricane: FEMA's hurricane baseline is 73.6/100, driving 100% of that score (no historical component is used for hurricane).
> 
> No active NWS alerts are currently affecting any of these hazard scores for Dallas (there is an unrelated Air Quality Alert active).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'rank', 'compare') |
| `hub_coverage` | pass | 1 hub(s) assessed |
| `score_integrity` | pass | 3 score(s) verified |
| `ranking_matches_engine` | pass | 3 hazard ranking(s) match |
| `disclosure` | pass | 4 assumption(s), 4 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline', 'fema', 'flood', 'historical'] |

### `followup_flood_only` — PASS

> What about flooding only?

Intent: `compare`

**Answer**

> Miami, FL ranks 1st with a flood risk score of 73.1 (High band); Houston, TX ranks 2nd at 71.2 (High band), a gap of 1.9 points. Miami's score breaks down as: baseline 44.8 pts (FEMA NRI flood baseline 99.7/100), historical 22.0 pts (8.8 qualifying precip days/year), and current alerts 6.2 pts (active Coastal Flood Advisory). Houston's score breaks down as: baseline 41.2 pts (FEMA NRI flood baseline 91.6/100), historical 30.0 pts (23.2 qualifying precip days/year, the max historical contribution), and 0 pts from current alerts (none active for flood).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare', 'rank') |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 2 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `followup_top_component` — PASS

> Which component contributed most?

Intent: `explain`

**Answer**

> For both hubs, the top driver is the "baseline" (FEMA NRI) component. For Miami, FL, baseline contributed 44.8 of 73.1 points (61% of the score). For Houston, TX, baseline contributed 41.2 of 71.2 points (58% of the score).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'compare', 'rank') |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 0 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline'] |

### `unknown_hub` — PASS

> How exposed is our Reykjavik hub to winter weather?

Intent: `out_of_scope`

**Answer**

> There is no Reykjavik hub in this network. This system covers 40 US distribution hubs; Reykjavik, Iceland is not one of them, so I have no data to report.

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

> I can't answer this. This system covers only three hazards: winter, hurricane, and flood risk. Wildfire risk is not modeled here.

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

> There are two Portland hubs in the network — Portland, ME and Portland, OR — and their risk profiles differ.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'clarify', expected one of ('clarify',) |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['portland'] |
| `asked_for_clarification` | pass | present, expected present |

