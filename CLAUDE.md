# CLAUDE.md — Moveo Weather Risk Intelligence Agent

## 0. Mission

Build the Moveo take-home assignment as a strong, narrow, runnable vertical slice within a 24-hour window.

The assignment is a **Weather Risk Intelligence Agent** for a logistics company operating regional U.S. distribution hubs. The agent should answer questions about weather disruption exposure, compare/rank hubs, explain the result, support conversational follow-ups, expose an API, use structured LLM output, and include a small evaluation set.

The assignment explicitly prioritizes:

- clarity
- reasoning
- thoughtful design
- a narrow scope done well
- live demonstrability during the follow-up interview

Do NOT optimize for feature count or production completeness.

---

# 1. Core principle

## The LLM is NOT the risk model.

Use this separation:

```text
User natural language
        |
        v
+-------------------+
|   Agent / LLM     |
| intent + tool use |
+---------+---------+
          |
          v
+-------------------+
|  External tools   |
| NWS / history /   |
| FEMA NRI          |
+---------+---------+
          |
          v
+-------------------+
| Deterministic     |
| Risk Scoring      |
| Engine            |
+---------+---------+
          |
          v
+-------------------+
| Structured result |
| + evidence        |
| + assumptions     |
+---------+---------+
          |
          v
      User answer
```

The LLM may:

- understand user intent
- decide which tools are required
- determine which hazard is relevant
- ask for missing parameters
- synthesize retrieved evidence
- explain the deterministic score
- maintain conversational context

The LLM must NOT:

- invent risk scores
- choose arbitrary scoring weights at runtime
- override deterministic calculations
- fabricate weather observations
- present unsupported certainty
- turn subjective language into an undocumented scoring rule

---

# 2. Assignment requirements

Treat these as the core acceptance criteria.

### Required

1. Public APIs for weather and hazard data.
2. Deterministic scoring/ranking logic.
3. Chat interface.
4. Agent exposed through an API that the chat UI calls.
5. Structured output / JSON schema between the LLM and application code.
6. Conversational follow-up questions.
7. Small evaluation set and runnable evaluation.
8. Clear assumptions, uncertainty, and scope.
9. Source code and run instructions.
10. Short architecture/design document covering:
   - architecture
   - repository structure
   - data storage
   - scoring methodology
   - why an LLM is needed
   - system prompt
   - evaluation set/results
   - key tradeoffs

### Bonus

- Voice
- scheduled/webhook-triggered alerts when risk changes

### Priority

Do NOT implement bonus functionality until every required item works end-to-end.

---

# 3. 24-hour scope decision

The target MVP is intentionally small.

## Geography

Use approximately 10–20 representative U.S. hubs.

Each hub needs only:

```text
name
latitude
longitude
county_fips
region
```

Do not build a generalized logistics location-management system.

## Hazards

Start with a small set that produces useful examples:

- winter weather
- hurricane
- flooding
- optionally heat

Do not attempt to model all 18 FEMA hazards.

## Data families

Use three primary data families:

```text
NWS
    current weather / forecast / alerts

Historical weather provider
    historical weather statistics

FEMA NRI
    baseline hazard exposure
```

## UI

Use Streamlit.

Do NOT build React unless the required functionality is already complete.

## API

Use FastAPI.

The Streamlit UI must communicate with the agent through the FastAPI boundary.

## Persistence

Do NOT introduce PostgreSQL/Supabase unless a concrete requirement emerges.

Preferred prototype storage:

```text
data/hubs.json
config/*.yaml
optional local cache / SQLite
```

Static hub metadata is configuration, not a production database problem.

---

# 4. Recommended architecture

```text
                    +----------------------+
                    |     Streamlit UI     |
                    |     Chat frontend    |
                    +----------+-----------+
                               |
                               | POST /chat
                               v
                    +----------------------+
                    |       FastAPI        |
                    |       /chat API      |
                    +----------+-----------+
                               |
                               v
                    +----------------------+
                    |      AI Agent        |
                    | PydanticAI OR        |
                    | OpenAI Agents SDK    |
                    +----------+-----------+
                               |
              +----------------+----------------+
              |                |                |
              v                v                v
       +-------------+  +-------------+  +-------------+
       | NWS tools   |  | Historical  |  | FEMA NRI   |
       |             |  | weather     |  | tool       |
       +------+------+  +------+------+  +------+------+
              |                |                |
              +----------------+----------------+
                               |
                               v
                    +----------------------+
                    | Deterministic Risk   |
                    | Scoring Engine        |
                    +----------+-----------+
                               |
                               v
                    +----------------------+
                    | Structured response  |
                    | score + evidence +   |
                    | assumptions + source |
                    +----------+-----------+
                               |
                               v
                         Streamlit UI
```

## Important boundary

The agent should call typed Python tools.

Do NOT introduce MCP just because it is fashionable.

MCP may be useful later, but this assignment does not require another protocol layer.

Preferred prototype:

```text
Agent
  -> Python function tool
  -> small API wrapper
  -> external API
```

not:

```text
Agent
  -> MCP
  -> MCP server
  -> Python wrapper
  -> REST API
```

Use the simplest architecture that demonstrates the engineering point.

---

# 5. Agent framework decision

## Preferred options

### Option A — PydanticAI

Primary candidate.

Why:

- Python-native
- Pydantic-based structured output
- typed tool calls
- validation
- good fit for the assignment
- keeps the implementation small

Repository:

https://github.com/pydantic/pydantic-ai

Structured output docs:

https://github.com/pydantic/pydantic-ai/blob/main/docs/output.md

Use Pydantic models for the agent's final response contract.

Possible final response model:

```python
class AgentResponse(BaseModel):
    answer: str
    hubs: list[HubAssessment]
    evidence: list[Evidence]
    assumptions: list[str]
    uncertainty: list[str]
```

### Option B — OpenAI Agents SDK

Valid alternative if it proves faster in implementation.

Repository:

https://github.com/openai/openai-agents-python

Documentation:

https://github.com/openai/openai-agents-python/blob/main/docs/index.md

The current SDK provides:

- agents
- function tools
- guardrails
- sessions
- tracing
- structured interaction primitives

Choose one framework only.

Do NOT build a framework abstraction layer.

## Not selected

LangGraph is known and capable, but it is not the default choice for this assignment because the workflow is simple and the extra graph abstraction is not required.

Reference examples only:

https://github.com/eliharoun/langgraph-weather-agent

https://github.com/filipp2000/langgraph-mcp-weather-agent

Use these for inspiration if useful; do not copy their architecture wholesale.

---

# 6. External API decisions

## 6.1 NWS — primary live U.S. weather source

Use the National Weather Service API for:

- current/near-current weather information
- forecast
- alerts

Docs:

https://www.weather.gov/documentation/services-web-api

API:

https://api.weather.gov

Alerts docs:

https://www.weather.gov/documentation/services-web-alerts

Important implementation notes:

- NWS requires a meaningful `User-Agent`.
- It is open/free data.
- NWS applies rate limits.
- `/points/{lat},{lon}` resolves a location to the correct forecast grid.
- Forecast endpoints provide short-term forecast data.
- Alerts are available directly from the API.

Do not scrape weather.gov HTML.

Reference Python client:

https://github.com/spdustin/nws-api-client-python

Scope of the repo:

- Python client/helpers for the NWS API.
- Can save implementation time around endpoint access/parsing.
- It is optional; a small `httpx` wrapper is also acceptable and may be simpler.

Decision:

Prefer either:
1. a tiny custom `httpx` client, or
2. the NWS client above if it clearly reduces implementation time.

Do not spend time adapting a large third-party abstraction if 100 lines of direct API code is cleaner.

---

# 7. Historical weather

## Preferred prototype option: Open-Meteo

Historical API docs:

https://open-meteo.com/en/docs/historical-weather-api

Repository:

https://github.com/open-meteo/open-meteo

Historical OpenAPI definition:

https://github.com/open-meteo/open-meteo/blob/main/openapi/historical-weather.yml

Useful variables include:

- snowfall
- snow depth
- precipitation
- temperature
- wind
- weather code

This is especially useful for questions such as:

> What percentage of days in Denver last year had snowfall?

Conceptually:

```text
snow_days = count(daily snowfall > 0)
percentage = snow_days / days_in_period * 100
```

### Open-Meteo constraint

Document the licensing/commercial-use limitation clearly.

Use Open-Meteo for the take-home only as an explicitly scoped prototype choice.

State in the design document that production deployment would require verification of commercial licensing/data requirements, or a different provider.

## Alternative: NOAA Climate Data Online

Docs:

https://www.ncei.noaa.gov/cdo-web/webservices/v2

Use NOAA when stronger observational provenance is more important than prototype speed.

NOAA requires a token and introduces more setup overhead.

Decision:

- Open-Meteo = fastest prototype path.
- NOAA CDO = more conservative production-oriented alternative.

Do not implement both unless there is excess time.

---

# 8. FEMA National Risk Index

This is the core hazard baseline.

Main FEMA page:

https://www.fema.gov/flood-maps/products-tools/national-risk-index

Technical documentation:

https://www.fema.gov/sites/default/files/documents/fema_national-risk-index_technical-documentation.pdf

County FeatureServer:

https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer

NRI ArcGIS data directory:

https://www.arcgis.com/home/item.html?id=1cb56c682f6f4ce08a07ff372a7908b0&sublayer=0

As of September 2026, the surfaced NRI data is based on the December 2025 v1.20.0 release.

The NRI covers 18 natural hazards and exposes values/scores/ratings around:

- Risk Index
- Expected Annual Loss
- Social Vulnerability
- Community Resilience

For this assignment, use only the hazard dimensions needed for the selected MVP.

Examples:

```text
Winter:
    Winter Weather / Cold Wave

Hurricane:
    Hurricane

Flood:
    Inland Flooding / Coastal Flooding
```

Important limitation:

NRI is a nationwide comparative/indexing dataset, not a perfect site-specific disruption model.

Do NOT describe its score as ground truth.

---

# 9. Commercial weather APIs — alternatives only

These were considered but are NOT the default implementation.

## WeatherAPI

https://www.weatherapi.com/docs/

Potential value:

- simple weather API
- hosted service
- commercial positioning

Reason not selected:

- historical coverage/free tier does not naturally solve the assignment's historical comparison as cleanly as Open-Meteo/NOAA.

## Tomorrow.io

https://www.tomorrow.io/weather-api/

Potential value:

- weather intelligence
- forecast/risk-oriented API
- logistics-oriented features

Reason not selected for the prototype:

- adds dependence on a commercial provider when public U.S. data is sufficient for a one-day assignment.

Keep these as production alternatives, not prototype requirements.

---

# 10. Open-source weather/MCP inspirations

## Open-Meteo MCP server

https://github.com/cyanheads/open-meteo-mcp-server

Scope:

- MCP server exposing Open-Meteo functionality
- useful reference for weather tool decomposition
- useful if we later choose to expose weather through MCP

Decision:

Do not make MCP mandatory for the prototype.

Use it to inspect good weather-tool boundaries if useful.

## Other Open-Meteo MCP examples

https://github.com/cmer81/open-meteo-mcp

https://github.com/ominiverdi/open-meteo-mcp-lite

Scope:

- alternative MCP implementations around Open-Meteo
- reference material only

Do not copy architecture just because it exists.

---

# 11. Chat UI

Use Streamlit.

Docs:

https://docs.streamlit.io/develop/api-reference/chat

Deployment:

https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app

Why:

- fastest way to get a functional chat UI
- easy live demonstration
- enough polish for a take-home
- allows effort to stay focused on the agent/data/scoring design

Do NOT build:

- custom React frontend
- authentication system
- complex CSS design system
- multi-page product shell

unless the core system is already finished.

---

# 12. Backend

Use FastAPI.

Docs:

https://fastapi.tiangolo.com/

The chat UI should call:

```http
POST /chat
```

Example request:

```json
{
  "message": "Which hubs in the Midwest are most exposed to winter disruption?",
  "conversation_id": "demo-session-1"
}
```

Example response:

```json
{
  "answer": "....",
  "hubs": [],
  "evidence": [],
  "assumptions": [],
  "uncertainty": []
}
```

The exact response schema may evolve, but the principle is fixed:

The UI is a client of the API, not directly coupled to the model implementation.

---

# 13. Data model

Keep the hub registry tiny.

Example:

```json
{
  "name": "Dallas",
  "latitude": 32.7767,
  "longitude": -96.797,
  "county_fips": "48113",
  "region": "South"
}
```

Potential hub set:

- Dallas
- Houston
- Miami
- Denver
- Chicago
- Minneapolis
- Detroit
- Atlanta
- Phoenix
- Seattle
- New York
- Boston
- etc.

Do not optimize geographic coverage too early.

Pick cities that make the demo questions meaningful.

---

# 14. Tool design

Target approximately five core tools.

```text
get_hub(name)

get_current_weather(hub)

get_weather_alerts(hub)

get_historical_weather(hub, start_date, end_date)

get_hazard_profile(hub)
```

Then the deterministic engine:

```text
calculate_risk(...)
```

is application/business logic, not an LLM judgment.

Each tool should:

- have a clear input schema
- have a clear output schema
- return normalized data
- expose source/provenance where possible
- fail clearly
- avoid returning huge raw API payloads to the LLM

Normalize external APIs before they reach the agent.

---

# 15. Deterministic scoring methodology

Use a transparent 0–100 model.

Do not pretend the weights are scientifically discovered.

Treat them as prototype assumptions and document them.

A reasonable initial structure:

```text
Risk Score =
    45% baseline hazard exposure
    30% historical exposure
    25% current/forecast conditions
```

Hazard-specific example:

### Winter

```text
50% FEMA winter/cold exposure
30% historical snowfall frequency
20% current/forecast severity
```

### Hurricane + Flood

```text
35% FEMA hurricane
35% FEMA flood
30% current/forecast warning/severity
```

### Heat

```text
40% FEMA heat-wave baseline
30% historical extreme-heat frequency
30% current/forecast severity
```

These are starting assumptions, not facts.

Keep weights in config, for example:

```yaml
winter:
  nri: 0.50
  historical: 0.30
  current: 0.20
```

Benefits:

- easy to explain
- reproducible
- testable
- easy to modify
- clearly separates business logic from model output

---

# 16. Why the LLM is needed

The system must make a strong case for why this is an agent and not a dashboard.

Deterministic code can already answer:

- What is the score?
- How many snowfall days occurred?
- Which hub has the larger score?
- Which hazard component contributed most?

The LLM adds:

- natural-language intent understanding
- tool selection
- conversational follow-up
- comparative questions
- explanation synthesis
- clarification
- context across turns

Example:

```text
User:
"Compare Miami and Houston."

Agent:
identify relevant hazards
    -> retrieve hurricane/flood evidence
    -> call deterministic scoring
    -> synthesize comparison

User:
"What about just flooding?"

Agent:
use conversation context
    -> narrow hazard
    -> retrieve only needed evidence
    -> calculate / return deterministic comparison
```

The LLM is the interface/orchestration layer around the decision system.

---

# 17. Structured output

Use Pydantic models for agent output.

Minimum conceptual contract:

```python
class HubAssessment(BaseModel):
    hub: str
    risk_score: float
    risk_band: str
    main_drivers: list[str]
    evidence: list[str]

class AgentResponse(BaseModel):
    answer: str
    assessments: list[HubAssessment]
    assumptions: list[str]
    uncertainty: list[str]
```

The schema can change, but the rules do not:

1. The code validates the result.
2. Scores come from deterministic functions.
3. Evidence must map to actual retrieved data.
4. Uncertainty must be explicit where relevant.

---

# 18. Evaluation plan

Create a small fixed evaluation set.

Suggested:

```text
1. Which hubs in the Midwest are most exposed to winter disruption?
2. Compare Miami and Houston for hurricane exposure.
3. Compare Miami and Houston for flood exposure.
4. What percentage of days in Denver in 2025 had snowfall?
5. Why is Dallas's weather risk high?
6. Follow-up: "What about flooding only?"
7. Follow-up: "Which component contributed most?"
8. Ask about an unsupported / unknown hub.
9. Ask for a hazard outside the implemented scope.
10. Ask for an unsupported level of precision.
```

Evaluate at least:

```text
- valid structured output
- correct tool selection
- deterministic score integrity
- numerical answer tolerance
- evidence/source presence
- assumptions/uncertainty disclosure
- follow-up context handling
```

Use pytest and/or Pydantic Evals if useful.

Pydantic Evals reference:

https://github.com/pydantic/pydantic-ai

The evaluation system should be small, runnable, and understandable.

Do NOT spend hours constructing an academic benchmark.

---

# 19. Governance / audibility inspiration from Moveo

We are not trying to clone Moveo.

Use their publicly described architecture as inspiration for engineering principles.

Moveo's public architecture materials emphasize a hybrid approach combining deterministic control with specialized AI reasoning, plus validation, governance, auditability, tool/system integration, and agent coordination.

Relevant public references:

Moveo architecture:
https://moveo.ai/blog/moveo-ai-architecture

Moveo A2A + MCP:
https://moveo.ai/platform/agent-to-agent

Moveo governance:
https://moveo.ai/platform/truepath-governance

Moveo memory layer:
https://moveo.ai/platform/memory-layer

The useful inspiration for this assignment is:

```text
LLM intelligence
        +
deterministic control
        +
typed tools
        +
validation
        +
auditable outputs
```

This assignment does NOT require implementing Moveo's platform concepts.

Do not add A2A, multiple agents, MCP, governance services, or persistent memory unless there is a concrete reason and the core MVP is already complete.

---

# 20. Research policy — IMPORTANT

## Claude must NOT web-search autonomously.

Do not browse the web, use browser tools, perform online research, or silently verify current information unless the user explicitly authorizes it.

This is a deliberate workflow decision for this assignment.

When current, external, niche, or uncertain information is required:

1. Stop.
2. Tell the user exactly what needs verification.
3. Ask the user to research it externally using an AI assistant such as:
   - ChatGPT
   - Perplexity
4. Ask the user to paste the relevant findings/links/results into the conversation.
5. Then analyze, reconcile, and apply the supplied research.

Use the user's external research as input, not as unquestioned truth.

## Example request to the user

Use language like:

> "I need to verify the current free-tier/license/rate-limit details for X. Please check this with ChatGPT or Perplexity and paste the relevant answer and source links here. I will use that information to make the implementation decision."

Or:

> "This depends on the current API behavior. Please ask ChatGPT/Perplexity to verify the current documentation and bring back the relevant endpoint/rate-limit details."

## Important distinction

Claude may reason from:

- repository code already present locally
- files already supplied by the user
- documentation/results the user pasted
- known facts already established in this CLAUDE.md

Claude must not independently turn an uncertain/current fact into an implementation decision by browsing without permission.

---

# 21. How to handle external research from the user

When the user brings research from ChatGPT/Perplexity:

1. Extract the actual factual claims.
2. Separate documented facts from recommendations/opinions.
3. Look for contradictions.
4. Do not blindly copy external recommendations.
5. Convert useful findings into an explicit engineering decision.
6. Record major decisions in this file or the design document when appropriate.

Example:

```text
Research:
Perplexity says provider X has Y requests/day.

Decision:
Use provider X only for optional prototype functionality because
the limit may be insufficient for batch evaluation.

Reason:
The core evaluation should not depend on a fragile external quota.
```

---

# 22. Outsourcing philosophy

The 24-hour deadline changes what "good engineering" means.

Prefer:

```text
Mature SDK
    over
custom implementation

Public API
    over
scraping / building data infrastructure

Typed tool
    over
LLM free-form reasoning

Streamlit
    over
React

FastAPI
    over
custom HTTP server

Pydantic
    over
manual JSON parsing
```

The value should come from system design, integration, scoring logic, evaluation, and reasoning — not rebuilding commodity infrastructure.

---

# 23. What NOT to build

Do not build these unless the MVP is already finished:

- custom frontend framework
- authentication
- user accounts
- production database
- Kubernetes
- microservices
- event bus
- Kafka
- vector database
- RAG pipeline
- multi-agent swarm
- MCP layer
- A2A protocol
- custom ML model
- voice interface
- complex observability platform
- enterprise deployment infrastructure

The demo must be easy to run.

---

# 24. Deployment strategy

Preferred goal:

A live URL for the UI.

Fastest practical structure:

```text
Streamlit
    |
    v
FastAPI backend
    |
    v
Agent + tools
```

If deployment of the backend becomes a blocker:

- prioritize a reliable local demo
- keep deployment instructions explicit
- do not lose several hours fighting infrastructure

The assignment says deployment is preferred, not mandatory.

---

# 25. Repository structure

Target something close to:

```text
weather-risk-agent/
|
├── app/
│   ├── api.py
│   ├── agent.py
│   ├── models.py
│   ├── tools/
│   │   ├── nws.py
│   │   ├── historical_weather.py
│   │   └── fema.py
│   ├── scoring/
│   │   ├── config.yaml
│   │   └── risk.py
│   └── services/
│       └── hub_registry.py
|
├── ui/
│   └── streamlit_app.py
|
├── data/
│   └── hubs.json
|
├── evals/
│   ├── cases.json
│   └── run_evals.py
|
├── tests/
│   ├── test_scoring.py
│   ├── test_tools.py
│   └── test_agent.py
|
├── docs/
│   └── architecture.md
|
├── .env.example
├── requirements.txt
├── README.md
└── CLAUDE.md
```

Keep this flexible. Do not create empty abstraction folders merely to look architectural.

---

# 26. Suggested implementation order

Use this order unless there is a concrete reason not to.

## Step 1 — Lock hub registry

Create 10–20 hubs with coordinates/FIPS.

## Step 2 — Implement external data wrappers

First:

- NWS
- historical weather
- FEMA

Normalize all outputs.

## Step 3 — Implement deterministic scoring

Test this heavily before involving the LLM.

## Step 4 — Implement typed agent tools

Expose only the normalized functions the agent actually needs.

## Step 5 — Add structured agent output

Pydantic model / JSON schema.

## Step 6 — Add FastAPI

Create `/chat`.

## Step 7 — Add Streamlit

Minimal but clean chat interface.

## Step 8 — Add conversation history

Only enough to support follow-up questions.

## Step 9 — Add evaluation

Run fixed evaluation cases.

## Step 10 — Write design document

Document:

- architecture
- choices
- scoring
- why LLM
- assumptions
- uncertainty
- limitations
- evaluation
- tradeoffs

## Step 11 — Deploy if time allows

Prefer live URL, but never sacrifice the runnable local demo to chase deployment.

## Step 12 — Bonus only if everything is stable

Alerts / webhook.

Voice remains lowest priority.

---

# 27. Decision log

Current decisions:

### Architecture
Single-agent + typed tools + deterministic risk engine.

### Agent framework
PydanticAI is preferred; OpenAI Agents SDK is the alternative.

### UI
Streamlit.

### API
FastAPI.

### Live weather
NWS.

### Historical weather
Open-Meteo prototype; NOAA CDO as a more conservative alternative.

### Hazard baseline
FEMA National Risk Index.

### Database
None initially. JSON/config + optional local cache.

### MCP
Not required for MVP.

### A2A / multi-agent
Not required for MVP.

### Voice
Skip.

### Alerts
Bonus only after the required system works.

### Scoring
Transparent deterministic weighted score, configurable and documented.

### LLM role
Intent understanding, tool orchestration, conversational context, explanation synthesis.

### Research workflow
No autonomous web research by Claude. Ask the user to use ChatGPT/Perplexity for external/current research and paste findings back.

---

# 28. Definition of done

The MVP is done when a reviewer can:

1. Start the project with documented commands.
2. Open the chat UI.
3. Ask:
   - "Which hubs in the Midwest are most exposed to winter disruption?"
   - "Compare Miami and Houston in terms of hurricane and flood exposure."
   - "What percentage of days in Denver last year had snowfall?"
   - "Why is Dallas's weather disruption risk high?"
4. See the agent retrieve real external data.
5. See a deterministic score.
6. Ask a follow-up question.
7. See structured/validated output.
8. Run the evaluation suite.
9. Read the architecture document and understand the tradeoffs.

Anything beyond this is secondary.

---


# 30. Existing internal project to reuse as a design reference

A particularly strong source of implementation patterns already exists in the user's GKG/GDELT analyst project:

```text
C:\Users\Omer Bassan\Desktop\Projects\DATA PROJECT\apps\analyst\analyst
```

This project is more important to inspect than generic GitHub weather-agent examples because its architecture already addresses several of the exact concerns in this assignment:

- deterministic analysis/ranking
- LLM reasoning on top of deterministic results
- structured LLM ↔ code contracts
- grounded natural-language explanations
- deterministic validation
- evaluation without relying entirely on an LLM judge
- typed configuration and provider abstraction

The new weather-risk implementation should **reuse the patterns and lessons** from this project where appropriate, while not copying domain-specific GKG/GDELT logic.

## 30.1 Structured output contract

### `plan.py`

Path:

```text
apps/analyst/analyst/plan.py
```

Pattern to reuse:

```text
LLM
  -> Pydantic "draft" schema
  -> conversion
  -> frozen/trusted dataclass
  -> rest of application
```

Why it matters here:

The assignment explicitly requires a structured output format between the LLM and application code.

This project already demonstrates the useful security/trust boundary:

```text
untrusted model-generated structure
        ↓
validation / normalization
        ↓
trusted application structure
```

Use this as inspiration for the weather agent's Pydantic response contract.

Do not assume the exact classes should be copied. Preserve the underlying pattern.

---

## 30.2 Reasoning/evidence contract

### `finding.py`

Path:

```text
apps/analyst/analyst/finding.py
```

This is a useful model for the assignment's requirement that the agent should "explain its reasoning clearly."

The `Finding` structure separates concepts such as:

```text
claim
magnitude
evidence_steps
interpretation
caveat
```

The important design lesson is:

> A factual claim should be represented separately from interpretation and caveat, rather than allowing all three to collapse into one piece of LLM-generated prose.

For Weather Risk Intelligence, use a similar conceptual separation where useful:

```text
score / factual result
evidence
interpretation
assumptions
uncertainty
```

This should make the output more auditable and reduce the chance that speculative text is mistaken for observed data.

---

## 30.3 Deterministic ranking + LLM explanation

### `discovery.py`

Path:

```text
apps/analyst/analyst/discovery.py
```

This is the closest internal analog to the assignment.

Existing pattern:

```text
SQL computes ranking deterministically
        ↓
LLM triages/selects from ranked results
        ↓
LLM explains why the result deserves attention
```

This maps almost directly onto the weather-risk problem:

```text
Weather + hazard data
        ↓
deterministic risk calculation
        ↓
rank / compare hubs
        ↓
LLM explains the result
```

Important lesson:

Do NOT ask the LLM:

> "Which hub is riskiest?"

and accept its judgment.

Instead:

```text
calculate scores
rank deterministically
        ↓
give the ranked facts to the LLM
        ↓
let the LLM explain / compare / contextualize
```

This is one of the strongest existing patterns to carry into the assignment.

---

## 30.4 Deterministic validation gate

### `validator.py`

Path:

```text
apps/analyst/analyst/validator.py
```

This is relevant to the requirement that the system should not be "only LLM output."

The important pattern:

```text
LLM proposes output/action
        ↓
deterministic validator
        ↓
accept / reject / repair
```

Apply the same thinking to the weather agent.

Potential examples:

```text
LLM says:
risk_score = 87
        ↓
application verifies:
actual deterministic score = 64
        ↓
reject / overwrite / repair
```

Or:

```text
LLM selects:
Miami + hurricane + flood
        ↓
application checks:
are these supported entities/hazards?
        ↓
continue only if valid
```

Never rely on the LLM to validate its own numerical conclusions.

---

## 30.5 Grounded natural-language narration

### `narrator.py`

Path:

```text
apps/analyst/analyst/narrator.py
```

This is another strong pattern for the weather project.

Existing conceptual flow:

```text
executed rows
    ↓
grounded prose
```

with numeric claims traceable back to actual rows and with factual claims separated from speculative hypotheses.

For Weather Risk Intelligence, preserve the analogous rule:

```text
retrieved weather/hazard facts
        +
deterministic score components
        ↓
narrator / LLM
        ↓
human-readable explanation
```

The final answer should be grounded in actual tool outputs.

For example, an answer such as:

> "Dallas is high risk because its winter baseline is elevated and the current forecast contains..."

should be traceable to actual returned measurements/indices.

Do not let the model invent supporting facts simply because they would make the explanation sound plausible.

---

## 30.6 Multi-step investigation pattern

### `investigator.py`

Path:

```text
apps/analyst/analyst/investigator.py
```

This is relevant to questions like:

> "Why is the Dallas hub's weather disruption risk high?"

That question may require several measurements.

A useful mental model is:

```text
User question
    ↓
decompose into relevant measurements
    ↓
execute each measurement
    ↓
collect structured evidence
    ↓
synthesize answer
```

For example:

```text
Why is Dallas high?
    ├── baseline winter hazard exposure
    ├── historical snowfall
    ├── recent/current alerts
    └── current forecast severity
            ↓
      deterministic score
            ↓
       explanation
```

Do NOT automatically introduce a complex multi-agent system just because the internal project has investigation logic.

Reuse the **decomposition → measurement → synthesis** pattern, keeping the implementation as simple as the assignment permits.

---

## 30.7 Evaluation architecture

The internal evaluation system is particularly relevant and should be inspected before inventing a new evaluation framework.

### `eval/golden.py`

Path:

```text
apps/analyst/analyst/eval/golden.py
```

Useful pattern:

```text
golden case
    =
question
+ expected outcome
+ optional reference answer
```

This maps cleanly to the weather assignment's small evaluation set.

### `eval/harness.py`

Path:

```text
apps/analyst/analyst/eval/harness.py
```

Useful concept:

Run the **real application pipeline** against evaluation cases instead of creating a separate fake evaluation implementation.

### `eval/checks.py`

Path:

```text
apps/analyst/analyst/eval/checks.py
```

Especially relevant because the project already supports deterministic checks around correctness/groundedness.

Prefer checks such as:

```text
- expected hub appears
- expected hazard appears
- numerical value is within tolerance
- returned score equals deterministic calculation
- required evidence exists
- unsupported claims are not present
```

over relying entirely on:

```text
LLM judge says response is good
```

### `eval/report.py`

Path:

```text
apps/analyst/analyst/eval/report.py
```

Useful pattern:

Turn evaluation results into an easy-to-read report that can be included in the take-home submission.

---

## 30.8 Configuration / wiring

### `config.py`

Path:

```text
apps/analyst/analyst/config.py
```

Worth reusing the pattern of:

- typed configuration
- environment-variable overrides
- explicit configuration boundaries

Do not copy the entire existing configuration system if it creates unnecessary dependencies.

### `llm.py`

Path:

```text
apps/analyst/analyst/llm.py
```

Worth skimming for:

- provider abstraction
- LLM configuration
- separation between model access and application logic

However, this is explicitly lower priority because PydanticAI already provides much of the provider/model abstraction we need.

Do NOT recreate an LLM abstraction merely because this project has one.

---

# 31. Internal project priority vs. external repos

When looking for implementation ideas, prefer sources in this order:

```text
1. Existing internal GKG/GDELT analyst project
2. Official documentation for the selected APIs/frameworks
3. Small mature open-source libraries
4. Generic weather-agent example repositories
```

The reason is simple:

The internal project already embodies the architecture principles the assignment explicitly tests.

External weather-agent repositories are mainly useful for:

- API wrappers
- endpoint patterns
- MCP tool definitions
- quick reference implementations

They are NOT the architectural source of truth for this project.

---

# 32. Concrete reuse map

Think of the final Weather Risk Agent as:

```text
Existing Analyst Project Pattern
                |
                | Adapt
                v
+---------------------------------------------+
|          Weather Risk Intelligence          |
+---------------------------------------------+

plan.py
    ↓
Pydantic draft / trusted output boundary

finding.py
    ↓
fact / evidence / interpretation / caveat

discovery.py
    ↓
deterministic ranking
    +
LLM explanation

validator.py
    ↓
deterministic validation gate

narrator.py
    ↓
grounded natural-language explanation

investigator.py
    ↓
multi-step measurement + synthesis

eval/golden.py
    ↓
weather evaluation cases

eval/harness.py
    ↓
real pipeline execution

eval/checks.py
    ↓
deterministic correctness / groundedness checks

eval/report.py
    ↓
human-readable evaluation report

config.py
    ↓
typed environment configuration

llm.py
    ↓
skim only; replace with PydanticAI model handling
```

This is an inspiration/translation map, not an instruction to copy files wholesale.

---

# 33. Important implementation rule for the internal project

Before writing new abstractions, check whether the existing project already solved the same architectural problem.

Ask:

```text
"Does apps/analyst/analyst/ already contain a proven pattern for this?"
```

especially for:

- output schemas
- trusted/untrusted boundaries
- evidence representation
- deterministic validators
- ranking
- grounded narration
- evaluations
- configuration

Do not copy domain-specific SQL, GKG/GDELT logic, or analyst-specific business concepts into the weather project.

Reuse the **architecture patterns**, not the domain implementation.

---

# 34. Suggested first inspection before coding

Before starting implementation, inspect these files in this order:

```text
1. plan.py
2. finding.py
3. discovery.py
4. validator.py
5. narrator.py
6. investigator.py
7. eval/golden.py
8. eval/harness.py
9. eval/checks.py
10. eval/report.py
11. config.py
12. llm.py
```

The purpose of this inspection is to answer:

```text
How should our weather agent represent:
    plans?
    trusted output?
    evidence?
    deterministic ranking?
    validation?
    grounded explanations?
    evaluations?
```

Only after that should new abstractions be introduced.

---

# 35. Updated architectural philosophy

The strongest architecture for this assignment is therefore not:

```text
LLM + weather APIs
```

It is:

```text
                +---------------------+
                | Natural-language UI |
                +----------+----------+
                           |
                           v
                +---------------------+
                | Agent / orchestration|
                | PydanticAI           |
                +----------+----------+
                           |
                           v
                +---------------------+
                | Typed tool calls     |
                +----------+----------+
                           |
          +----------------+----------------+
          |                |                |
          v                v                v
        NWS          Historical weather    FEMA NRI
          |                |                |
          +----------------+----------------+
                           |
                           v
                +---------------------+
                | Deterministic       |
                | scoring/ranking     |
                +----------+----------+
                           |
                           v
                +---------------------+
                | Validation / trusted|
                | structured result   |
                +----------+----------+
                           |
                           v
                +---------------------+
                | Grounded narrator   |
                | / LLM explanation   |
                +---------------------+
                           |
                           v
                    Human-readable
                       answer
```

This combines the assignment's explicit requirements with patterns already demonstrated in the GKG/GDELT analyst project.

The objective is not to prove that we can build an agent from scratch.

The objective is to show that we can recognize reusable architecture, adapt it to a new problem, and spend the limited 24-hour budget on the parts that actually differentiate the solution.


# 29. Final working rule

When deciding whether to implement something, ask:

> "Does this improve the credibility of the weather-risk decision system within the 24-hour constraint?"

If yes, consider it.

If it is merely infrastructure, visual polish, or framework complexity, prefer the simplest existing solution.

The goal is not to demonstrate how many technologies we can use.

The goal is to demonstrate that we can turn:

```text
ambiguous business problem
        ->
real data
        ->
deterministic decisioning
        ->
LLM orchestration
        ->
auditable explanation
        ->
working product
```

quickly, clearly, and responsibly.
