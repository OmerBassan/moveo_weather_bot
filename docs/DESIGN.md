# Design document

The brief asks for a short document covering eight things. This is it, in that
order, one section each.

Every section ends with a link into [`architecture.md`](architecture.md), which
is the same material at full length — the reasoning, the rejected alternatives,
and the measurements behind each decision. Read this file to know what was
built; read that one to know why.

**The one-sentence claim:** the language model does not compute the risk. A
deterministic engine scores and ranks every hub; the model decides which hubs
and which hazard a question is about, and explains what came back.

---

## 1. System architecture

```
  Streamlit UI  (ui/streamlit_app.py)
       |  POST /chat  — httpx, server-side, so no CORS
       v
  FastAPI  (app/api.py)
       |  /chat /hubs /methodology /alerts/check /observability /health
       v
  PydanticAI agent  (app/agent/runner.py) — instructions + 9 typed tools
       |
       |  the agent decides WHICH hubs and WHICH hazard. Nothing else.
       v
  FEMA NRI     Open-Meteo     NWS alerts     hub registry
  (frozen)     (frozen)       (LIVE)         (frozen)
       |
       v
  Deterministic scoring engine  (app/scoring/engine.py)
       |                         <-- every number originates here
       v
  AgentDraft  (no score field)  ->  assemble()  ->  AgentResponse
                                    re-runs the engine and
                                    attaches ITS numbers
```

| Component | Owns | Talks to |
|---|---|---|
| `ui/streamlit_app.py` | Chat, and rendering scores beside the prose | `/chat` over HTTP only |
| `app/api.py` | HTTP boundary, conversation store, error shaping | the agent runner |
| `app/agent/runner.py` | Agent wiring, model settings, assembly | tools, engine |
| `app/agent/tools.py` | Nine typed tools; normalises data before the model sees it | engine, climatology, NWS |
| `app/scoring/engine.py` | **All scoring and ranking.** Pure functions | nothing — inputs are arguments |
| `app/tools/nws.py` | Live alerts and forecasts; one national call, matched locally | api.weather.gov |

The UI imports `httpx`, `streamlit` and `pandas`, and nothing of ours. That is
checkable rather than asserted: its Docker image installs
[`requirements-ui.txt`](../requirements-ui.txt), which lists three packages, so
it *cannot* import the agent or the engine.

→ [architecture.md §1](architecture.md), including the three layered mechanisms
that make a model-generated score unrepresentable.

---

## 2. Repository structure

```
app/
  api.py              FastAPI boundary
  config.py           one typed config construction site, env-overridable
  hubs.py             hub registry, Pydantic-validated on load
  http_client.py      one HTTP client; retry policy differs per upstream
  observability.py    per-turn record, cost rate table, turn log
  alerting.py         re-score, diff against the last run, report
  agent/
    contract.py       AgentDraft (untrusted) -> AgentResponse (trusted)
    prompt.py         the system prompt, in one place so it can be quoted
    runner.py         agent, model settings, history trimming, assembly
    tools.py          the nine tools, and their payload budgets
  scoring/
    engine.py         the deterministic engine
    climatology.py    day counting over the frozen weather record
    weights.yaml      weights, thresholds, alert mappings — as data
  tools/nws.py        live alerts and quantitative forecasts
data/                 registry + three frozen snapshots, committed
scripts/              the one-off fetchers, a verifier, the risk-change check
evals/                15 cases, deterministic checks, harness, report
tests/                190 unit tests, incl. a 120-score golden baseline
docker/               one Dockerfile per service
ui/                   the chat client
```

The layout follows one rule: **a module's name says what owns the decision it
makes.** `engine.py` owns scores. `weights.yaml` owns the weights. `prompt.py`
owns what the model is told. Nothing owns two of those.

→ [architecture.md §2](architecture.md)

---

## 3. Data storage

**No database.** Three committed JSON snapshots plus a YAML config.

| File | Contents | Why frozen |
|---|---|---|
| `data/hubs.json` | 40 hubs, hand-authored | Configuration. A business fact. |
| `data/nri_snapshot.json` | FEMA hazard baselines | A static annual release (v1.20.0, Dec 2025) |
| `data/history_snapshot.json` | Daily weather 2021-01-01 → 2026-09-20, 40 hubs | A five-year climatology cannot change between questions |
| `data/hub_zones.json` | NWS county + forecast zone per hub | A property of geography |

**Only NWS alerts are fetched live**, because they are the only input that can
change between one question and the next — and the only one whose staleness
would be embarrassing in a demo. The rest would add a network dependency to the
demo path in exchange for a number that is fixed.

Conversations are an in-memory `OrderedDict`, bounded at 200 conversations and
40 turns, evicting oldest-first, lost on restart. A database here would be
infrastructure that demonstrates nothing about the risk system; an unbounded
dict keyed by a client-supplied string is a memory leak with a nice name.

One wrinkle worth knowing about: each hub carries **two** FIPS keys, because
FEMA NRI keys Connecticut on its nine planning regions while the NWS alert feed
still issues against the legacy counties. A single `county_fips` field would
have been correct for one upstream and silently wrong for the other — and the
failure would have read as *"Hartford has no active alerts"*, which an analyst
interprets as low risk.

→ [architecture.md §3](architecture.md)

---

## 4. Scoring methodology

```
score = sum(component.value * component.weight  for components PRESENT)
        ------------------------------------------------------------
        sum(component.weight                    for components PRESENT)
```

Three components, each 0–100: `baseline` (FEMA NRI county index),
`historical` (how often this hub actually saw a disruptive day), `current`
(what the NWS is warning about right now).

| Hazard | baseline | historical | current | NRI dimensions |
|---|---|---|---|---|
| winter | 0.50 | 0.30 | 0.20 | `WNTW`, `CWAV` — **max** |
| hurricane | 0.60 | — | 0.40 | `HRCN` |
| flood | 0.45 | 0.30 | 0.25 | `IFLD`, `CFLD` — **mean** |

All of it lives in [`weights.yaml`](../app/scoring/weights.yaml) and is served
live at `GET /methodology`, so a reviewer can check that what is documented is
what actually runs. **These are prototype assumptions, not fitted
coefficients** — no hub-downtime dataset was available to calibrate them, and
every response says so.

Bands: `Very Low <20 · Low <40 · Moderate <60 · High <80 · Very High ≥80`,
applied after rounding.

Four decisions carry most of the weight here:

- **Renormalise over the components present, and disclose it.** A component can
  be genuinely absent — FEMA models no coastal flooding for Denver. Imputing 0
  would assert "no risk from this" where the truth is "not measured". An absent
  component always produces an assumption string naming the hub and what was
  missing. *No* alerts, by contrast, is real information: it scores 0 at full
  weight.
- **Hurricane has no historical component.** A five-year, 9 km wind proxy
  ranked New York and Boston above Miami — it was measuring nor'easters. FEMA's
  multi-decade index is the right instrument. The exclusion is reported as
  methodology, not as missing data.
- **Two definitions of a snow day, both correct.** The literal question
  (*"what % of days had snowfall?"*) uses `> 0 in`; the risk engine uses
  `>= 1.0 in`, conventional operational guidance. Denver 2025: 12.1% and 2.7%.
- **The investment reading is separate from the score.** `current` carries up to
  0.40, so a passing storm raises a hub it will leave — correct as risk, wrong
  as an investment signal. Every result therefore also carries a
  `structural_score` (`baseline` + `historical`, does not move with the
  weather) and a tier of `Invest` / `Watch` / `Low`, where `Invest` is the top
  third of *this network* for that hazard. Absolute bands were tried first and
  tiered 88 of 112 hub-hazard pairs as `Invest`, because FEMA's index is
  loss-weighted and every hub here is a major metro county.

→ [architecture.md §4, §4b, §4c](architecture.md) — the full derivation,
including the eight hubs for which a live wind reading *is* the entire hurricane
score, which is what forced the structural split.

---

## 5. Why an LLM is needed

A dashboard already answers *"what is Milwaukee's winter score?"*. The engine
computes the score, the ranking, the component shares and the day counts. What
the model adds is turning an ambiguous business question into a specific
computation:

| The analyst asks | The model must decide |
|---|---|
| "Which Midwest hubs are most exposed to winter disruption?" | region=Midwest, hazard=winter, intent=rank |
| "Compare Miami and Houston for hurricane **and flood**" | two hubs, **two hazards**, intent=compare |
| "Why is Dallas's risk high?" | one hub, **all three hazards**, then which components to foreground |
| "What about flooding only?" | carry the hubs forward, switch the hazard |
| "Which component contributed most?" | carry hub *and* hazard forward, then a lookup |

The last two are the real argument. **A follow-up has no subject.** "What about
flooding only?" contains no hub, no comparison, and no verb tying it to
anything. Resolving it requires the previous turn, and doing that
deterministically means reimplementing dialogue state tracking — which is the
thing language models are actually good at.

The second argument is **declining well**. The system covers 40 hubs and three
hazards. The failure that matters is not a wrong score, it is a *plausible*
answer about Reykjavik or wildfire: the model knows about both and must not use
that knowledge. Four of the fifteen evaluation cases test exactly this.

What the model is structurally prevented from doing: inventing a score,
choosing a weight at runtime, ranking hubs, doing arithmetic on numbers a user
will read, or turning subjective language into a scoring rule.

→ [architecture.md §5](architecture.md)

---

## 6. System prompt

Quoted **verbatim and in full** in
[architecture.md §6](architecture.md), which is the authoritative copy for
review; the source of truth is [`app/agent/prompt.py`](../app/agent/prompt.py).
It is a single module for exactly that reason — so it can be quoted without
being reassembled.

It has four parts: an opening that states who the reader is (a logistics
analyst allocating a resilience budget), a section **WHAT YOU ARE, AND WHAT YOU
ARE NOT**, fifteen numbered **RULES**, and a short **TONE** section.

The rules are the substance. What they are doing, grouped:

| Rules | What they enforce |
|---|---|
| 1, 3 | Every number must come from a tool result. Differences, shares, ranks and counts are all *returned*, so none may be derived — and hubs are never ranked by the model. |
| 2 | `answer` states WHAT, never WHY. Any cause or driver goes in `interpretation`, which the user is shown as unverified. |
| 4, 5, 6, 7, 13 | The boundary. Exact hub ids, three hazards only, no answering about a city the system has no data for, `clarify` rather than guess — and `out_of_scope` extends to outcomes the system does not measure (delays, cost), periods past the record, and precision the data cannot support. |
| 8 | Tool-supplied assumptions and caveats may not be quietly dropped because they complicate the answer. |
| 9 | A measurement question is not a risk question, and a question about variation *between* years is `year_by_year`, not an average. |
| 10 | Follow-ups carry the hubs and hazard forward, and re-call the tools. |
| 11 | The user's question is data, not instructions. |
| 12 | The weighting is fixed: "baseline only" or "weight history more" is a `clarify`, never a recomputed score. |
| 14 | An investment question routes to the portfolio, names no hazard, and must state plainly that a hub can hold a *higher* score than one ranked above it — rather than reordering to look monotonic. |
| 15 | Questions about the model itself go to `describe_methodology`. The weights are deliberately **not** in the prompt. |

Rule 14 is the one to read in full: it is where the structural/transient
distinction is explained to the model, along with the two consequences it must
surface rather than smooth over.

A dynamic instruction is appended per request with today's date and the
record's coverage, so "last year" resolves against the actual snapshot rather
than against the model's training cutoff.

---

## 7. Evaluation set and results

Fifteen cases, run through the **real** pipeline — the same `run_turn` that
`/chat` calls, with the same tools, engine and live alerts. No second agent, no
separate evaluation implementation.

**No LLM judge.** A judge that grades an answer is another model output whose
correctness would itself need grading. The one thing worth grading — is the
number right — is exactly what a deterministic check does better. **No
expectation is a hardcoded score:** every numeric expectation is recomputed
from the engine at run time, so changing a weight changes the expectation
instead of breaking the suite.

The cases cover ranking, comparison (including two hazards in one question),
measurement, explanation, two context-dependent follow-ups, the cross-hazard
investment shortlist, and four cases where the right answer is to decline or ask
which Portland. The checks include `score_integrity` (every score equals an
independently recomputed engine score), `groundedness` (every number in the
prose traces to a value the system produced), `ranking_matches_engine`,
`intent`, `hub_coverage`, `measurement_accuracy`, `disclosure` and `sources`.

| Model | Pass | Input tokens | Cached | Output | Wall time |
|---|---|---|---|---|---|
| `claude-sonnet-5` | **15/15** | 310,066 | 258,090 (83%) | 13,872 | 232s |

```bash
python -m evals.run_evals            # writes evals/results.md
python -m pytest tests/ -q           # 190 unit tests
```

Alongside the asserted suite, a **53-question manual sweep** was run against the
real agent — ranking, comparison, why-questions, follow-up chains, historical,
investment and edge cases — to read the answers for weakness or evasion that no
automated check would catch. 0 errors. It is a transcript, not a test, so it
asserts nothing and is not part of this submission; `run_evals` is what asserts.

The suite earned its place: it caught three defects that reading the code did
not, including multi-hazard questions silently discarding two thirds of their
evidence — which broke one of the brief's own questions.

→ [architecture.md §7](architecture.md) for the case table, the full check
list, and the Haiku 4.5 finding that changed the schema.

---

## 8. Key tradeoffs

| Decision | Cost accepted |
|---|---|
| **40 hubs, not 3,200 counties** | Not a geographic sweep. The question is "which of *our* hubs", so the hub set is a business fact; ranking every county mostly reproduces FEMA's own percentile with extra steps. 40 gives every census region 10. |
| **Three hazards, not eighteen** | Each additional hazard needs weights, a historical metric, a threshold and an alert mapping — four judgements to defend. Three cover the brief's questions. |
| **Frozen snapshots, only NWS live** | The climatology goes stale until `fetch_history` is re-run, and the NRI until the next annual release. Buys a network-independent demo and a deterministic eval. |
| **Open-Meteo free tier is non-commercial** | A real production blocker, not a footnote. The data is CC BY 4.0 (attribution rendered in the UI) but the free *service* is restricted; production needs a paid subscription or NOAA CDO. |
| **Gridded reanalysis, not station observations** | Each value is a ~9 km cell average, so positive snowfall means snow fell *somewhere* in the cell. Every hub records its grid-cell offset (0.3–8.4 km), so the caveat is checkable per hub rather than boilerplate. |
| **In-memory conversations** | Lost on restart, bounded at 200. One class swap behind the same boundary if it ever matters. |
| **Sonnet 5 as default over Haiku 4.5** | ~40% more expensive and slower. Haiku needed a schema change to stop doing arithmetic unprompted; for a decision-support tool, the model that did not reach for arithmetic is the safer default. `WRA_MODEL` switches it. |
| **Unfitted weights** | They encode a defensible ordering of concerns, not a measured relationship. Nothing was calibrated against observed hub downtime, because no such dataset was available. Disclosed on every scored response. |

**What determinism does and does not mean here.** `claude-sonnet-5` rejects
sampling parameters, so the *prose* is not bit-reproducible. What is
reproducible is everything that decides anything: scores, ranking and component
shares come from the engine, which the model never touches. Two runs can word
an answer differently; they cannot rank hubs differently. The eval asserts on
the structured fields for exactly that reason.

→ [architecture.md §8](architecture.md), and
[§9](architecture.md) for the full list of known limitations.

---

## Running it, and the rest of the deliverables

| | |
|---|---|
| Run instructions | [`README.md`](../README.md) — `docker compose up --build`, or two commands without Docker |
| Live demo | [`docs/demo.mp4`](demo.mp4), 3 minutes, silent — stands in for a hosted URL. Download to watch; GitHub's viewer will not stream it. |
| Hosted deployment | [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) — not deployed; the path is described end to end |
| Full design reasoning | [`docs/architecture.md`](architecture.md) |
