# Weather Risk Intelligence Agent

Ranks 40 US distribution hubs by weather disruption exposure, so an analyst can
decide where a limited resilience budget goes.

**The language model does not compute the risk.** A deterministic engine scores
and ranks every hub from FEMA hazard baselines, a five-year weather
reanalysis and live National Weather Service alerts. The model interprets the
question, chooses the tools, and explains the result. Its output schema has no
score field and no rank field, so a model-generated number cannot be
represented — there is no code path by which one reaches a user.

See [`docs/architecture.md`](docs/architecture.md) for the design, the scoring
methodology, the system prompt, the evaluation results and the tradeoffs.

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

## Verify it

```bash
# 54 unit tests: scoring arithmetic, renormalisation, registry, token budgets
.venv/Scripts/python.exe -m pytest tests/ -q

# 10 evaluation cases through the real pipeline (costs API calls, ~100s)
.venv/Scripts/python.exe -m evals.run_evals

# the same suite against a different model
.venv/Scripts/python.exe -m evals.run_evals --model anthropic:claude-haiku-4-5-20251001

# re-check the frozen data: null coverage, grid-cell offsets, hub registry
.venv/Scripts/python.exe -m scripts.verify_snapshots
```

Results land in [`evals/results.md`](evals/results.md). Both Sonnet 5 and
Haiku 4.5 currently pass 10/10.

---

## Repository

```
app/
  api.py              FastAPI: /chat, /hubs, /methodology, /health
  config.py           the one typed configuration construction site
  hubs.py             hub registry, validated on load
  http_client.py      one HTTP client, retry policy per upstream
  agent/
    contract.py       AgentDraft (untrusted) -> AgentResponse (trusted)
    prompt.py         the system prompt, verbatim
    runner.py         agent wiring, model settings, assembly
    tools.py          the five typed tools the agent may call
  scoring/
    engine.py         the deterministic risk engine. No LLM reaches it.
    climatology.py    day counting over the frozen weather record
    weights.yaml      weights and thresholds, as data
  tools/
    nws.py            live NWS alerts, one call for the whole country

data/                 hub registry + three frozen snapshots (committed)
scripts/              one-off fetchers that built those snapshots, and a verifier
evals/                10 cases, deterministic checks, harness, report
tests/                54 unit tests
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
python -m scripts.fetch_history    # Open-Meteo 2021-2025, ~9 min (rate limited)
python -m scripts.verify_snapshots # coverage matrix + grid-cell offsets
```

---

## Data sources

- **FEMA National Risk Index** v1.20.0 (December 2025) — county hazard baselines.
- **NOAA / National Weather Service** — live active alerts.
- **[Weather data by Open-Meteo.com](https://open-meteo.com/)** — historical
  reanalysis, CC BY 4.0. The free API tier is **non-commercial use only**;
  production use requires a paid subscription. See the tradeoffs section of the
  design document.
