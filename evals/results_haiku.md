# Evaluation results

- **Model:** `anthropic:claude-haiku-4-5-20251001`
- **Run:** 2026-09-27T12:59:59+00:00
- **Result:** 10/10 cases passed
- **Wall time:** 61s total, 6.1s per case
- **Tokens:** 93,009 in (8,654 from cache), 4,222 out

Every case runs through the same `run_turn` the `/chat` endpoint calls —
same tools, same scoring engine, same live NWS alerts. There is no
separate evaluation implementation, and no LLM judge: a judge that grades
an answer is another model output whose correctness would itself need
grading.

## Summary

| Case | Category | Result | Checks | Time |
| --- | --- | --- | --- | --- |
| `rank_midwest_winter` | ranking | **PASS** | 8/8 | 6.9s |
| `compare_miami_houston_hurricane` | comparison | **PASS** | 8/8 | 5.6s |
| `compare_miami_houston_flood` | comparison | **PASS** | 8/8 | 8.9s |
| `measure_denver_snow` | measurement | **PASS** | 4/4 | 5.8s |
| `explain_dallas` | explanation | **PASS** | 8/8 | 11.4s |
| `followup_flood_only` | follow-up | **PASS** | 8/8 | 6.4s |
| `followup_top_component` | follow-up | **PASS** | 7/7 | 3.9s |
| `unknown_hub` | refusal | **PASS** | 4/4 | 4.2s |
| `out_of_scope_hazard` | refusal | **PASS** | 4/4 | 3.4s |
| `ambiguous_portland` | clarification | **PASS** | 4/4 | 4.8s |

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

> The Midwest hubs ranked by winter disruption risk, highest first:
> 
> 1. **Milwaukee, WI** – Risk score 66.2 (High)
> 2. **Minneapolis, MN** – Risk score 64.9 (High)
> 3. **Detroit, MI** – Risk score 61.6 (High)
> 4. **Chicago, IL** – Risk score 60.8 (High)
> 5. **Columbus, OH** – Risk score 56.5 (Moderate)
> 6. **Omaha, NE** – Risk score 55.7 (Moderate)
> 7. **Indianapolis, IN** – Risk score 55.3 (Moderate)
> 8. **Kansas City, MO** – Risk score 55.2 (Moderate)
> 9. **Des Moines, IA** – Risk score 54.3 (Moderate)
> 10. **St. Louis, MO** – Risk score 48.7 (Moderate)
> 
> The top four hubs (Milwaukee, Minneapolis, Detroit, and Chicago) are all in the High risk band. Milwaukee leads by 1.3 points over Minneapolis.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'rank', expected one of ('rank',) |
| `hub_coverage` | pass | 10 hub(s) assessed |
| `hazard` | pass | assessed ['winter'] |
| `score_integrity` | pass | 10 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 1 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_hurricane` — PASS

> Compare Miami and Houston in terms of hurricane exposure.

Intent: `compare`

**Answer**

> Houston, TX and Miami, FL are tied in hurricane risk. Both score 60.0 (High risk band). Both have a FEMA NRI hurricane baseline of 100.0/100, and neither has active National Weather Service alerts for hurricane hazard. The gap between them is 0.0.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['hurricane'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 1 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_flood` — PASS

> Compare Miami and Houston for flood exposure.

Intent: `compare`

**Answer**

> Miami, FL has higher flood risk than Houston, TX. Miami scores 73.1 (High risk band) versus Houston's 71.2 (also High). The gap between them is 1.9 points.
> 
> Miami's risk is driven primarily by its FEMA baseline score of 99.7 (mean of inland and coastal flooding), which contributes 61% of its total score. It also has 3 active flood-related alerts: Coastal Flood Advisory (Minor), Rip Current Statement (Moderate), and Coastal Flood Statement (Minor).
> 
> Houston's baseline is 91.6, lower than Miami's. However, Houston has substantially more historical precipitation days—23.2 days per year with precipitation ≥1.0 inch, compared to Miami's 8.8 days per year. This historical component contributes 42% of Houston's score. Houston currently has no active NWS alerts for flood hazards.
> 
> Both hubs are in the High risk band, but Miami ranks first due to its higher baseline coastal and inland flood vulnerability and current active alerts.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 2 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `measure_denver_snow` — PASS

> What percentage of days in Denver last year had snowfall?

Intent: `measure`

**Answer**

> 12.1% of days in 2025 had snowfall at Denver, CO (44 of 365 observed days). For operational context, 2.7% of days had disruptive snowfall of at least 1.0 inch.

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

> Dallas, TX's overall weather disruption risk is driven primarily by flood hazard, which scores 74.8 (High). This is the highest flood risk among the 10 hubs in the South region.
> 
> The flood score is built from two components: a FEMA NRI baseline of 99.6/100 for inland flooding (contributing 44.8 points, 60% of the score), and a historical component of 16.8 days per year with precipitation ≥1.0 inch (contributing 30.0 points, 40% of the score). The historical frequency exceeds the 12-day anchor used in scoring.
> 
> Dallas also carries moderate risk for winter (51.4, ranked 2 of 10 in the South) and hurricane (44.1, ranked 9 of 10 in the South). The winter score is driven almost entirely by a FEMA baseline of 99.9/100, despite only 1.0 day per year of snowfall ≥1.0 inch observed in the 2021–2025 record. The hurricane score reflects a FEMA baseline of 73.6/100 with no historical component (five-year data cannot measure hurricane frequency).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'rank', 'compare') |
| `hub_coverage` | pass | 1 hub(s) assessed |
| `score_integrity` | pass | 3 score(s) verified |
| `ranking_matches_engine` | pass | 3 hazard ranking(s) match |
| `disclosure` | pass | 4 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline', 'fema', 'flood', 'historical'] |

### `followup_flood_only` — PASS

> What about flooding only?

Intent: `compare`

**Answer**

> Miami, FL ranks higher for flood risk than Houston, TX. Miami scores 73.1 (High risk band) versus Houston's 71.2 (High risk band). The gap between them is 1.9 points. Miami's score is driven primarily by its FEMA NRI flood baseline (44.8 points, 61% of its score) and historical precipitation (22.0 points, 30%), plus an active Coastal Flood Advisory contributing 6.2 points. Houston's score is driven by its baseline (41.2 points, 58%) and notably higher historical precipitation days—23.2 days per year versus Miami's 8.8 days per year—contributing 30.0 points (42% of its score), but it has no active flood alerts.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare', 'rank') |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 1 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `followup_top_component` — PASS

> Which component contributed most?

Intent: `compare`

**Answer**

> For Miami, FL: baseline contributed most, accounting for 61% of its 73.1 score (44.8 points). Historical precipitation contributed 30% (22.0 points), and current alerts contributed 9% (6.2 points).
> 
> For Houston, TX: baseline also contributed most, accounting for 58% of its 71.2 score (41.2 points). Historical precipitation contributed 42% (30.0 points), and current alerts contributed 0% (0.0 points, as there are no active flood alerts).
> 
> In both hubs, the FEMA baseline is the dominant driver of flood risk.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('explain', 'compare', 'rank') |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 1 assumption(s), 0 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline', 'historical', 'current'] |

### `unknown_hub` — PASS

> How exposed is our Reykjavik hub to winter weather?

Intent: `out_of_scope`

**Answer**

> Reykjavik is not part of this distribution network. The system covers 40 hubs in the United States only.

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

> This system does not cover wildfire risk. The three hazards modeled here are winter weather, hurricanes, and flooding. I can rank hubs by risk for any of those three hazards if that would help.

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

> The network has two Portland hubs. Please specify Portland, ME or Portland, OR.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'clarify', expected one of ('clarify',) |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['portland'] |
| `asked_for_clarification` | pass | present, expected present |

