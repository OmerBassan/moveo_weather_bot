# Weather Risk Intelligence Agent

Ranks 40 US distribution hubs by weather disruption exposure and tiers them
for annual resilience investment, so an analyst can decide where a limited
budget goes.

It separates a hub's **structural** exposure -- what an upgrade could act on --
from the **transient** weather that moves its score this week, because a storm
passing through raises a hub it will leave.

**The language model does not compute the risk.** A deterministic engine scores
and ranks every hub from FEMA hazard baselines, a five-year weather
reanalysis and live National Weather Service alerts. The model interprets the
question, chooses the tools, and explains the result. Its output schema has no
score field and no rank field, so a model-generated number cannot be
represented — there is no code path by which one reaches a user.

See [`docs/architecture.md`](docs/architecture.md) for the design, the scoring
methodology, the system prompt, the evaluation results and the tradeoffs.

## What is and isn't here

Against the brief's optional items, stated up front rather than left to be
discovered:

| | |
|---|---|
| **Deployed URL** | **Not done.** The brief calls it "preferred, but not mandatory". It runs locally in one command (below) or under Docker Compose, and both paths are verified. [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) describes a hosted path end to end. |
| **Voice** | **Not done.** Listed as a bonus. The effort went into the deterministic engine and the evaluation instead. |
| **Scheduled / webhook alerting** | **Done** — the other bonus. See [Risk-change alerting](#risk-change-alerting-the-bonus). |
| **Demo recording** | [`docs/demo.mp4`](docs/demo.mp4) — 3 minutes, silent. Stands in for a hosted URL. |

The recording covers the first five turns of a single conversation: the Midwest
winter ranking, why Minneapolis places where it does, the two-threshold snowfall
answer, a Minneapolis/Chicago comparison, and the refusal when asked to drop the
historical component and re-weight the score.

Where each required item lives, for a reviewer who wants to go straight to it:

| Requirement | Where |
|---|---|
| Public APIs for weather and hazard data | FEMA NRI + Open-Meteo reanalysis, fetched once into [`data/`](data/); live NWS alerts and forecasts in [`app/tools/nws.py`](app/tools/nws.py) |
| Deterministic scoring / ranking | [`app/scoring/engine.py`](app/scoring/engine.py), weights as data in [`weights.yaml`](app/scoring/weights.yaml) |
| Chat interface | [`ui/streamlit_app.py`](ui/streamlit_app.py) — an HTTP client of `/chat`, importing nothing else of ours |
| Agent behind an API the UI calls | `POST /chat` in [`app/api.py`](app/api.py); see [The API](#the-api) |
| Enforced JSON schema between LLM and code | [`app/agent/contract.py`](app/agent/contract.py): `AgentDraft` (untrusted) → `AgentResponse` (trusted) |
| Conversational follow-ups | Per-conversation message history in `app/api.py`, trimmed in [`app/agent/runner.py`](app/agent/runner.py) |
| Evaluation set and a way to run it | 15 cases in [`evals/`](evals/), `python -m evals.run_evals`, results in [`evals/results.md`](evals/results.md) |
| Assumptions, uncertainty, scoping | Separate fields on every response, surfaced in the UI; [§9](docs/architecture.md) for limitations |

Conversations are held in memory, so restarting the API clears them. Known
limitations are listed in full in [§9 of the design document](docs/architecture.md).

---

## Run it

### With Docker (two services, one command)

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
docker compose up --build
```

Then open **http://localhost:8501**. The UI waits for the API's health check,
so it will not start into a connection error.

### Without Docker

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt         # macOS / Linux

echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# terminal 1
.venv/Scripts/python.exe -m uvicorn app.api:app --port 8000

# terminal 2
.venv/Scripts/python.exe -m streamlit run ui/streamlit_app.py
```

> On Windows, write `.env` with a UTF-8 editor. PowerShell's `>` redirect
> produces UTF-16, which `python-dotenv` cannot read.

An `ANTHROPIC_API_KEY` is the only required secret. Everything else has a
working default in [`app/config.py`](app/config.py); see
[`.env.example`](.env.example).

---

## Ask it

The four questions from the brief, all of which work:

```
Which hubs in the Midwest are most exposed to winter disruption?
Compare Miami and Houston in terms of hurricane and flood exposure.
What percentage of days in Denver last year had snowfall?
Why is the Dallas hub's weather disruption risk high?
```

And the question the brief's framing is really about — which hubs deserve this
year's resilience budget:

```
Which hubs should we prioritise for resilience investment this year?
```

Then a follow-up, which uses conversation context:

```
What about flooding only?
Which component contributed most?
```

And the paths where it should decline rather than guess:

```
How exposed is our Reykjavik hub?      -> out of scope, not a hub we cover
Which hubs face the worst wildfire?    -> out of scope, hazard not modelled
How risky is Portland?                 -> asks which Portland, ME or OR
```

---

## The API

The UI is a client of the API, not of the agent. `POST /chat` is the whole
surface it uses:

```bash
curl -s http://localhost:8000/chat -H 'content-type: application/json' -d '{
  "message": "Which hubs in the Midwest are most exposed to winter disruption?",
  "conversation_id": "demo-1"
}'
```

The response is a declared Pydantic model, so its shape is in the OpenAPI
schema at `http://localhost:8000/docs` rather than implied by whatever the
handler happened to build:

```json
{
  "answer": "...",
  "intent": "rank",
  "interpretation_of_question": "...",
  "assessments": [{"hub": "Minneapolis, MN", "hazard": "winter",
                   "risk_score": 68.4, "risk_band": "High", "rank": 1,
                   "main_drivers": ["..."], "evidence": ["..."]}],
  "interpretation": [], "assumptions": [], "uncertainty": [], "sources": [],
  "clarification_question": null, "out_of_scope_reason": null,
  "conversation_id": "demo-1", "usage": {}
}
```

`risk_score`, `risk_band` and `rank` are joined in from the engine after the
model has answered. The model chooses *which* hubs and *which* hazard; it never
supplies a number.

The rest of the surface: `GET /hubs` (the registry), `GET /methodology` (weights
and thresholds, the same data the agent reads), `POST /alerts/check`,
`GET /observability`, `GET /health`.

---

## Risk-change alerting (the bonus)

Re-scores all 40 hubs across all three hazards, diffs against the stored
baseline, and reports what moved.

```bash
# detect and print; --dry-run leaves the baseline untouched
python -m scripts.check_risk_changes --dry-run

# POST the changes to a webhook
python -m scripts.check_risk_changes --webhook https://hooks.example.com/...

# or trigger it over HTTP
curl -X POST 'http://localhost:8000/alerts/check?min_delta=3'
```

Scheduled with cron or Task Scheduler:

```cron
0 */6 * * *  cd /srv && python -m scripts.check_risk_changes --webhook $HOOK
```

Exit codes are meaningful: `0` no changes, `1` changes found, `2` could not
run. A webhook delivery failure does **not** advance the baseline, so the next
run reports the same changes rather than losing them.

Sample output:

```
2 risk change(s), including 1 band crossing(s)

  ! Miami, FL — flood risk increased 14.9 points to 72.9 (Moderate → High)
      active: Coastal Flood Advisory (Minor)
  - Dallas, TX — winter risk decreased 8.6 points to 51.2
```

**This feature is only coherent because the scores are deterministic.** Run the
check twice in calm weather and all 120 scores are identical, so a diff means
the inputs changed. Had the model produced the number, every run would drift
and every run would look like a change.

---

## Observability

Every answered turn is logged as one line and appended to `data/turns.jsonl`:

```
[0b69c0cc] compare in 14.2s | 2 hub(s) | tools: list_hubs, rank_hubs_by_risk(flood)
           | 15375+12977c in / 754 out | $0.0409
```

Read it back without shell access:

```bash
curl -s 'http://localhost:8000/observability?limit=10'
```

which returns recent turns plus running totals — turn count, error count,
total and mean cost, median and slowest duration, token totals.

Cost is computed server-side from one rate table in `app/observability.py`, so
the figure a reader quotes does not depend on which client rendered it, and the
rates used are stored with each turn — a recorded cost is only auditable if the
rate that produced it was recorded too.

Set `WRA_LOG_LEVEL=DEBUG` for more, `WRA_TURN_LOG` to relocate the file.

---

## Verify it

```bash
# 178 unit tests: scoring arithmetic, renormalisation, registry, tool payloads,
# a 120-score golden baseline, the alerting diff, and the UI's rendering
.venv/Scripts/python.exe -m pytest tests/ -q

# 15 evaluation cases through the real pipeline (costs API calls, ~3 min)
.venv/Scripts/python.exe -m evals.run_evals

# the same suite against a different model
.venv/Scripts/python.exe -m evals.run_evals --model anthropic:claude-haiku-4-5-20251001

# re-check the frozen data: null coverage, grid-cell offsets, hub registry
.venv/Scripts/python.exe -m scripts.verify_snapshots
```

Results land in [`evals/results.md`](evals/results.md). Sonnet 5 currently
passes 15/15. Every score, every ranking and the cross-hazard investment
ordering are recomputed independently from the engine and compared -- there is
no LLM judge.

---

## Repository

```
app/
  api.py              FastAPI: /chat, /hubs, /methodology, /alerts/check,
                      /observability, /health
  config.py           the one typed configuration construction site
  hubs.py             hub registry, validated on load
  http_client.py      one HTTP client, retry policy per upstream
  observability.py    per-turn record, cost rate table, turn log
  agent/
    contract.py       AgentDraft (untrusted) -> AgentResponse (trusted)
    prompt.py         the system prompt, verbatim
    runner.py         agent wiring, model settings, assembly
    tools.py          the nine typed tools the agent may call
  alerting.py         re-score, diff against the last run, report
  scoring/
    engine.py         the deterministic risk engine. No LLM reaches it.
                      Scores, the structural/transient split, and the
                      cross-hazard investment portfolio.
    climatology.py    day counting over the frozen weather record
    weights.yaml      weights and thresholds, as data
  tools/
    nws.py            live NWS alerts, one call for the whole country

data/                 hub registry + three frozen snapshots (committed)
scripts/              one-off fetchers that built those snapshots, a verifier,
                      and the risk-change check
evals/                15 cases, deterministic checks, harness, report
tests/                178 unit tests, incl. a 120-score golden baseline
docs/                 architecture.md (the design document), DEPLOYMENT.md
docker/               one Dockerfile per service
ui/streamlit_app.py   the chat UI. A client of /chat; imports nothing of ours.
```

---

## Rebuilding the data

The three snapshots are committed, so nothing below is needed to run the
system. They are one-off build steps because none of their inputs change
between user questions.

```bash
python -m scripts.fetch_nri        # FEMA National Risk Index, 1 request
python -m scripts.fetch_zones      # NWS county + forecast zone per hub
python -m scripts.fetch_history    # Open-Meteo 2021-2026, ~9 min (rate limited)
python -m scripts.verify_snapshots # coverage matrix + grid-cell offsets
```

---

## Data sources

- **FEMA National Risk Index** v1.20.0 (December 2025) — county hazard baselines.
- **NOAA / National Weather Service** — live active alerts.
- **[Weather data by Open-Meteo.com](https://open-meteo.com/)** — daily ECMWF
  IFS reanalysis, 2021-01-01 to 2026-09-20 (five complete years plus 2026 to
  date), CC BY 4.0. The free API tier is **non-commercial use only**;
  production use requires a paid subscription. See the tradeoffs section of the
  design document.
