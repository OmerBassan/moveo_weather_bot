# Evaluation results

- **Model:** `default (anthropic:claude-sonnet-5)`
- **Run:** 2026-09-27T22:21:46+00:00
- **Result:** 15/15 cases passed
- **Wall time:** 196s total, 13.0s per case
- **Tokens:** 298,817 in (261,986 from cache), 12,045 out

Every case runs through the same `run_turn` the `/chat` endpoint calls —
same tools, same scoring engine, same live NWS alerts. There is no
separate evaluation implementation, and no LLM judge: a judge that grades
an answer is another model output whose correctness would itself need
grading.

## Summary

| Case | Category | Result | Checks | Time |
| --- | --- | --- | --- | --- |
| `rank_midwest_winter` | ranking | **PASS** | 8/8 | 14.0s |
| `compare_miami_houston_hurricane` | comparison | **PASS** | 8/8 | 13.2s |
| `compare_miami_houston_flood` | comparison | **PASS** | 8/8 | 11.3s |
| `compare_miami_houston_both_hazards` | comparison | **PASS** | 9/9 | 14.5s |
| `measure_denver_snow` | measurement | **PASS** | 4/4 | 12.3s |
| `explain_dallas` | explanation | **PASS** | 8/8 | 32.3s |
| `followup_flood_only` | follow-up | **PASS** | 8/8 | 9.6s |
| `followup_top_component` | follow-up | **PASS** | 7/7 | 7.0s |
| `unknown_hub` | refusal | **PASS** | 4/4 | 6.5s |
| `out_of_scope_hazard` | refusal | **PASS** | 4/4 | 5.9s |
| `ambiguous_portland` | clarification | **PASS** | 4/4 | 5.9s |
| `portfolio_investment_shortlist` | investment | **PASS** | 9/9 | 26.4s |
| `portfolio_tier_is_not_todays_score` | investment | **PASS** | 8/8 | 13.0s |
| `measure_worst_snow_year` | measurement | **PASS** | 3/3 | 9.9s |
| `out_of_scope_hurricane_where_fema_models_none` | scope | **PASS** | 7/7 | 13.7s |

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

> For winter hazard, the 10 Midwest hubs rank as follows (highest risk first): 1) Milwaukee, WI (65.2, High), 2) Minneapolis, MN (64.1, High, 1.1 pts behind leader), 3) Detroit, MI (60.9, High, 4.3 pts behind leader), 4) Chicago, IL (60.2, High, 5.0 pts behind leader), 5) Columbus, OH (55.9, Moderate, 9.3 pts behind leader), 6) Omaha, NE (55.7, Moderate), 7) Indianapolis, IN (55.1, Moderate), 8) Kansas City, MO (54.5, Moderate), 9) Des Moines, IA (54.0, Moderate), 10) St. Louis, MO (48.6, Moderate, 16.6 pts behind leader).

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

> Miami, FL and Houston, TX are tied for hurricane risk, both scoring 60.0 (High band), with a gap of 0.0 between them. Both hubs carry the maximum FEMA hurricane baseline (100.0/100), which accounts for the entire score since there are no active NWS hurricane alerts for either right now. Forecast wind gusts over the next 72 hours are 26.50 mph for Houston and 15.00 mph for Miami, both well below the 39 mph threshold where the forecast component starts contributing, so neither adds to the score currently. Miami currently has active alerts for Coastal Flood Advisory, Coastal Flood Statement, and a Rip Current Statement, and Houston has an Air Quality Alert — but none of these are hurricane-hazard alerts, so they don't affect this comparison.

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

> Miami, FL ranks slightly higher for flood exposure, with a risk score of 72.9 (High band) versus Houston, TX at 71.2 (High band) -- a gap of 1.7 points.
> 
> Miami's score is driven mainly by its FEMA baseline (99.7/100, averaging inland flooding 99.7 and coastal flooding 99.6), plus historical rainfall of 8.7 days/year with precipitation >=1.0 in, and currently has active alerts: Coastal Flood Advisory (Minor), Coastal Flood Statement (Minor), and Rip Current Statement (Moderate).
> 
> Houston's score is also baseline-driven (91.6/100, inland flooding 100.0, coastal flooding 83.2), but its historical component is higher -- 24.1 days/year with precipitation >=1.0 in, maxing the historical scale at 100. Houston currently has no active flood alerts (only an unrelated Air Quality Alert).</answer>
> <parameter name="caveats">["The historical component's 12-days/year anchor mapping to a score of 100 is a prototype assumption, not a measured relationship.", "The forecast component's 1-3 inch scoring bounds over 72 hours are also prototype assumptions.", "Live alert conditions (like Miami's current coastal flood alerts) can shift day to day and are not part of the structural/baseline exposure."]

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('compare',) |
| `hub_coverage` | pass | 2 hub(s) assessed |
| `hazard` | pass | assessed ['flood'] |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 0 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |

### `compare_miami_houston_both_hazards` — PASS

> Compare Miami and Houston in terms of hurricane and flood exposure.

Intent: `compare`

**Answer**

> Hurricane: Houston, TX and Miami, FL are tied on hurricane risk, both scoring 60.0 ("High" band), driven entirely by their FEMA baseline (both 100/100 max hurricane exposure), with no current alerts scoring for this hazard at either hub. Forecast wind gusts over the next 72h are 26.50 mph for Houston and 15.00 mph for Miami, but neither crosses the 39 mph threshold that would add points.
> 
> Flood: Miami, FL scores higher, 72.9 ("High"), vs Houston, TX at 71.2 ("High") — a gap of 1.7 points. Miami's baseline is 99.7/100 (mean of inland 99.7, coastal 99.6) and its historical component shows 8.7 qualifying precipitation days/year. Houston's baseline is lower (91.6/100, inland 100.0/coastal 83.2) but its historical component is higher — 24.1 qualifying days/year, which is the maximum score (100) on that measure. Miami currently has an active Coastal Flood Advisory adding a small current-conditions boost; Houston has no active flood alerts.

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

> In 2025, Denver, CO had snowfall on 44 of 365 observed days, or 12.1% of the year. For context, 10 of those days (2.7%) met the higher "disruptive snowfall" threshold of at least 1.0 inch of fresh snow.

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

> Dallas, TX's overall risk isn't uniformly high across hazards — it breaks down like this:
> 
> - Flood: 74.8, band "High" — the biggest driver. It's built from a FEMA NRI inland flooding baseline of 99.6/100 (60% of the score) plus a historical component that's maxed out at 100 (15.6 qualifying-rain days/year vs. a 12-day anchor, 40% of the score). No active NWS flood alerts and forecast precipitation is only 0.97 in over the next 72h, so none of this score comes from current conditions. Dallas ranks 1 of 10 South hubs for flood risk.
> - Winter: 51.2, band "Moderate" — driven almost entirely (98%) by the FEMA baseline (cold wave index 99.9/100), not by recent snowfall history (only 0.9 qualifying snow days/year) or current forecast (0 inches of snow expected).
> - Hurricane: 44.1, band "Moderate" — entirely baseline-driven (FEMA hurricane index 73.6/100), with no historical component used for this hazard and no current wind alerts (forecast gust 31.1 mph).
> 
> So "high" mainly applies to flood risk; winter and hurricane sit at Moderate. Flood is also the only hazard where Dallas is flagged "Invest" tier; winter and hurricane are "Low" tier.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'rank', 'compare') |
| `hub_coverage` | pass | 1 hub(s) assessed |
| `score_integrity` | pass | 3 score(s) verified |
| `ranking_matches_engine` | pass | 3 hazard ranking(s) match |
| `disclosure` | pass | 7 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline', 'fema', 'flood', 'historical'] |

### `followup_flood_only` — PASS

> What about flooding only?

Intent: `compare`

**Answer**

> For flooding, Miami, FL ranks higher with a risk score of 72.9 (High band) versus Houston, TX at 71.2 (High band) — a gap of 1.7 points. Both are driven mainly by their FEMA baseline: Miami's flood baseline is 99.7/100 (mean of inland 99.7, coastal 99.6), while Houston's is 91.6/100 (inland 100.0, coastal 83.2). Historically, Houston sees more qualifying heavy-precipitation days: 24.1 days/year (vs. a 12-day anchor) contributing 30.0 points, compared to Miami's 8.7 days/year contributing 21.8 points. Currently, Miami has an active Coastal Flood Advisory adding 6.2 points to its score, while Houston has no active flood alerts, contributing 0 from current conditions. Forecast precipitation over the next 72 hours is 0.49 in for Miami and 0.04 in for Houston.

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

Intent: `compare`

**Answer**

> For both hubs, the baseline component contributed the most. For Miami, FL, baseline contributed 44.8 pts (62% of its 72.9 score). For Houston, TX, baseline contributed 41.2 pts (58% of its 71.2 score).</answer>
> <parameter name="caveats">[]</parameter>
> <parameter name="interpretation">[]

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'compare', expected one of ('explain', 'compare', 'rank') |
| `score_integrity` | pass | 2 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 2 assumption(s), 0 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['baseline'] |

### `unknown_hub` — PASS

> How exposed is our Reykjavik hub to winter weather?

Intent: `out_of_scope`

**Answer**

> There is no Reykjavik hub in this network — the system covers 40 distribution hubs across the United States, and Reykjavik is not one of them. I can't provide a winter exposure figure for it.

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

> This system does not cover wildfire risk. It only scores three hazards: winter storms, hurricanes, and flooding, across 40 distribution hubs. I can't provide a wildfire risk ranking.

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

> There are two Portland hubs in the network — Portland, ME and Portland, OR — and they would have different risk profiles.

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

> The portfolio tool groups all 40 hubs into three tiers by structural (persistent) exposure. 28 hubs fall in the "Invest" tier — the top of that group, in order, is: Boston, MA (rank 1, structural score 93.7, driven by winter/hurricane/flood), New York, NY (rank 2, structural 98.8, hurricane/flood), Houston, TX (rank 3, structural 100.0, hurricane/flood), Miami, FL (rank 4, structural 100.0, hurricane/flood), New Orleans, LA (rank 5, structural 99.7, hurricane/flood), then Hartford CT, Newark NJ, Salt Lake City UT, Portland ME, Dallas TX, and 18 more hubs continuing down to Charlotte, NC at rank 28.
> 
> Note: New York, NY currently carries a higher live risk_score (83.5, "Very High") than Boston, MA (56.3) despite ranking below it — Boston still ranks first because the tier ordering is built on structural exposure, not the current live score.
> 
> Beyond Invest, 3 hubs sit in a "Watch" tier (Providence RI, Phoenix AZ, Las Vegas NV) and 9 in "Low" (Columbus OH, Pittsburgh PA, Omaha NE, Indianapolis IN, Des Moines IA, Los Angeles CA, Portland OR, Seattle WA, Sacramento CA).

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

> No. Boston, MA sits at rank 1 in the Invest tier, but its current risk_score is 56.3 ("Moderate"). New York, NY at rank 2 carries a higher current risk_score of 83.5 ("Very High"). Several other hubs further down the Invest list also have higher risk_scores than Boston's 56.3, e.g. Salt Lake City, UT (78.2), Portland, ME (68.1), and Dallas, TX (74.8).

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'portfolio', expected one of ('portfolio', 'explain', 'methodology') |
| `score_integrity` | pass | 40 score(s) verified |
| `portfolio_matches_engine` | pass | 40 hubs in the engine's order |
| `tiers_grouped` | pass | tier blocks in order: ['Invest', 'Watch', 'Low'] |
| `disclosure` | pass | 24 assumption(s), 2 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast, Open-Meteo ECMWF IFS reanalysis, 2021-01-01 to 2026-09-20 |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['current'] |

### `measure_worst_snow_year` — PASS

> What was Minneapolis's worst year for snow disruption, and how unusual was that compared with a normal year?

Intent: `measure`

**Answer**

> Minneapolis, MN's worst complete year for snow disruption (days with at least 1.0 inch of fresh snow) was 2023, with 16 such days. Across the 5 complete years in the record (2021-2025), the mean was 10.0 days per year, so 2023 came in 60% above the average year. Other years: 2021 had 8 days, 2022 had 12, 2024 had 6, and 2025 had 8. (2026 is a partial year with 4 days so far and is excluded from this comparison.)

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'measure', expected one of ('measure',) |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['2023'] |

### `out_of_scope_hurricane_where_fema_models_none` — PASS

> Should we invest in hurricane resilience at Boise?

Intent: `explain`

**Answer**

> Boise, ID currently has a hurricane risk score of 0.0 ("Very Low" band). FEMA's National Risk Index does not model hurricane exposure for Boise's county at all, so there is no structural baseline component to score. The only input available is current conditions: no active NWS hurricane-related alerts and a forecast max wind gust of 21.90 mph over the next 72 hours (well below the 39 mph threshold where scoring begins). Because no structural component could be measured, Boise is not assigned an investment tier for hurricane risk.

| Check | Result | Detail |
| --- | --- | --- |
| `intent` | pass | got 'explain', expected one of ('explain', 'portfolio', 'out_of_scope') |
| `score_integrity` | pass | 1 score(s) verified |
| `ranking_matches_engine` | pass | 1 hazard ranking(s) match |
| `disclosure` | pass | 3 assumption(s), 3 uncertainty note(s) |
| `sources` | pass | FEMA National Risk Index v1.20.0 (December 2025), NWS active alerts, NWS quantitative gridpoint forecast |
| `groundedness` | pass | every figure traces to a tool result |
| `expected_phrasing` | pass | found ['does not model', 'no structural'] |

