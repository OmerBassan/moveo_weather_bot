# Evaluation results

- **Model:** `default (anthropic:claude-sonnet-5)`
- **Run:** 2026-09-27T13:38:59+00:00
- **Result:** 11/11 cases passed
- **Wall time:** 119s total, 10.8s per case
- **Tokens:** 136,119 in (115,916 from cache), 8,217 out

Every case runs through the same `run_turn` the `/chat` endpoint calls —
same tools, same scoring engine, same live NWS alerts. There is no
separate evaluation implementation, and no LLM judge: a judge that grades
an answer is another model output whose correctness would itself need
grading.

## Summary

| Case | Category | Result | Checks | Time |
| --- | --- | --- | --- | --- |
| `rank_midwest_winter` | ranking | **PASS** | 8/8 | 12.5s |
| `compare_miami_houston_hurricane` | comparison | **PASS** | 8/8 | 13.6s |
| `compare_miami_houston_flood` | comparison | **PASS** | 8/8 | 12.5s |
| `compare_miami_houston_both_hazards` | comparison | **PASS** | 9/9 | 12.0s |
| `measure_denver_snow` | measurement | **PASS** | 4/4 | 12.4s |
| `explain_dallas` | explanation | **PASS** | 8/8 | 26.7s |
| `followup_flood_only` | follow-up | **PASS** | 8/8 | 10.2s |
| `followup_top_component` | follow-up | **PASS** | 7/7 | 6.3s |
| `unknown_hub` | refusal | **PASS** | 4/4 | 4.2s |
| `out_of_scope_hazard` | refusal | **PASS** | 4/4 | 4.0s |
| `ambiguous_portland` | clarification | **PASS** | 4/4 | 4.3s |

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

## Case detail

### `rank_midwest_winter` — PASS

> Which hubs in the Midwest are most exposed to winter disruption?

Intent: `rank`

**Answer**

> Among the 10 Midwest hubs, ranked by winter risk score (highest first): 1. Milwaukee, WI (65.2, High), 2. Minneapolis, MN (64.1, High, 1.1 pts behind the leader), 3. Detroit, MI (60.9, High), 4. Chicago, IL (60.2, High), 5. Columbus, OH (55.9, Moderate), 6. Omaha, NE (55.7, Moderate), 7. Indianapolis, IN (55.1, Moderate), 8. Kansas City, MO (54.5, Moderate), 9. Des Moines, IA (54.0, Moderate), 10. St. Louis, MO (48.6, Moderate, 16.6 pts behind the leader).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'rank', expected one of ('rank',) |
| `hub_coverage` | pass | 10 hub(s) assessed |
| `hazard` | pass | assessed ['winter'] |
| `score_integrity` | pass | 10 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_hurricane` — PASS

> Compare Miami and Houston in terms of hurricane exposure.

Intent: `compare`

**Answer**

> Houston, TX and Miami, FL both carry a hurricane risk score of 60.0 (High band), tied at rank 1 and 2 with a gap of 0.0 between them. Both scores are driven entirely by the FEMA NRI hurricane baseline (100.0/100, max possible), with no current contribution from active alerts or forecast wind: Houston's 72-hour forecast max gust is 19.60 mph and Miami's is 11.50 mph, both well below the 39 mph threshold where the forecast component starts to score. Miami currently has active NWS alerts (Coastal Flood Advisory, Rip Current Statement, Coastal Flood Statement) but none of these are hurricane alerts, so they don't add to the hurricane score. Houston has an Air Quality Alert, also not hurricane-related.

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

> Miami, FL ranks slightly higher for flood risk than Houston, TX: Miami scores 72.9 (High band) vs Houston's 71.2 (High band), a gap of 1.7 points.
> 
> For Miami, the score is driven mainly by its FEMA baseline (99.7/100, mean of inland=99.7 and coastal=99.6 flooding), contributing 62% of its score. Historical data shows 8.7 days/year with precipitation >=1.0 in (50 of 2089 observed days, 2.4%). It currently has 3 active flood-related alerts (Coastal Flood Advisory, Rip Current Statement, Coastal Flood Statement).
> 
> For Houston, the baseline is 91.6/100 (inland=100.0, coastal=83.2), 58% of its score. Historical data shows a higher frequency: 24.1 days/year with precipitation >=1.0 in (138 of 2089 observed days, 6.6%) -- this historical component contributes 42% of Houston's score. Houston currently has no active flood-related NWS alerts (only an unrelated Air Quality Alert).</answer>
> <parameter name="interpretation">Miami's edge comes largely from its baseline coastal/inland flood exposure and active coastal alerts, while Houston's risk is driven more by a higher historical frequency of heavy-precipitation days.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_both_hazards` — PASS

> Compare Miami and Houston in terms of hurricane and flood exposure.

Intent: `compare`

**Answer**

> Hurricane: Houston, TX and Miami, FL both score 60.0 (High band), tied — both driven entirely by the FEMA baseline (100/100 max hurricane index), with no active hurricane alerts and low forecast wind gusts (19.6 mph Houston, 11.5 mph Miami).
> 
> Flood: Miami, FL ranks slightly higher at 72.9 (High) vs Houston, TX at 71.2 (High), a gap of 1.7 points. Miami's score is led by a higher FEMA flood baseline (99.7 vs 91.6) plus an active Coastal Flood Advisory, while Houston's score leans more on historical rainfall frequency (24.1 qualifying days/year vs Miami's 8.7 days/year).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood', 'hurricane'] |
| `score_integrity` | pass | 4 score(s) verified |
| `ranking_matches_engine` | pass | 2 hazard ranking(s) match |
| `disclosure` | pass | 4 assumption(s), 4 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['hurricane'] |

### `measure_denver_snow` — PASS

> What percentage of days in Denver last year had snowfall?

Intent: `measure`

**Answer**

> In 2025, Denver, CO had snowfall on 44 of 365 observed days, or 12.1% of days.

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

> Dallas, TX's risk profile is mixed by hazard, not uniformly "high":
> 
> - Flood: score 74.8 (High band), ranked 1 of 10 in the South. Driven mainly by the FEMA NRI inland flooding baseline (99.6/100, 60% of the score) plus historical rainfall frequency (15.6 days/year with ≥1.0 in precipitation, scoring 100/100, 40% of the score). No active alerts currently contribute.
> - Winter: score 51.2 (Moderate), ranked 2 of 10 in the South. Almost entirely driven by the FEMA baseline (cold wave/winter weather max 99.9/100, 98% of the score); actual historical snow days are low (0.9 days/year with ≥1 in snowfall) and current forecast/alerts contribute nothing.
> - Hurricane: score 44.1 (Moderate), ranked 9 of 10 in the South (i.e., low within the region). Driven solely by the FEMA hurricane baseline (73.6/100); no historical component is used for this hazard, and current conditions add nothing.
> 
> So if "high" refers to overall exposure, it's the flood hazard that stands out (High band, top of the region), while winter and hurricane are both Moderate.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'rank', 'compare') |
| `hub_coverage` | pass | 1 hub(s) assessed |
| `score_integrity` | pass | 3 score(s) verified |
| `ranking_matches_engine` | pass | 3 hazard ranking(s) match |
| `disclosure` | pass | 7 assumption(s), 4 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline', 'fema', 'flood', 'historical'] |

### `followup_flood_only` — PASS

> What about flooding only?

Intent: `compare`

**Answer**

> Miami, FL ranks 1st for flood risk with a score of 72.9 (High band); Houston, TX ranks 2nd at 71.2 (High band), a gap of 1.7 points. For Miami, the baseline FEMA flood score (99.7/100) contributes 62% of its score, historical precipitation days (8.7 days/yr with >=1in precip, 30% of score) contribute 30%, and current conditions (active Coastal Flood Advisory, forecast 0.41in over 72h) contribute 9%. For Houston, the baseline (91.6/100) contributes 58%, historical precipitation days (24.1 days/yr with >=1in precip, notably higher than Miami's) contribute 42%, and there is no current alert/forecast contribution (0%) since Houston has no active flood alert and only 0.12in forecast over 72h.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare', 'rank') |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |

### `followup_top_component` — PASS

> Which component contributed most?

Intent: `explain`

**Answer**

> For both hubs, the top driver of the flood score is the "baseline" component (FEMA NRI baseline). Miami: baseline contributes 44.8 pts, 62% of its 72.9 score. Houston: baseline contributes 41.2 pts, 58% of its 71.2 score.</answer>
> <parameter name="interpretation">[]</parameter>
> <parameter name="caveats">["Baseline is the largest single component for both, but historical precipitation frequency is a close second, especially for Houston (42% of its score)."]

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'compare', 'rank') |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 0 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-2025 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline', 'historical'] |

### `unknown_hub` — PASS

> How exposed is our Reykjavik hub to winter weather?

Intent: `out_of_scope`

**Answer**

> There is no Reykjavik hub in this network. This system covers 40 distribution hubs across the US (Midwest, Northeast, South, West regions), and Reykjavik, Iceland is not among them.

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

> This system does not cover wildfire risk. It only models three hazards: winter storms, hurricanes, and flooding.

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

> I need more detail before I can answer.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'clarify', expected one of ('clarify',) |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['portland'] |
| `asked_for_clarification` | pass | present, expected present |

