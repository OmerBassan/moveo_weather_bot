# Deployment

**This is not deployed.** The brief calls a live URL preferred, not mandatory,
and the time it would have taken went into the scoring engine and the
evaluation instead. The demo runs locally in one command — see
[`README.md`](../README.md) — and [`docs/demo.mp4`](demo.mp4) is a recording of
it working, so nothing about reviewing this depends on a URL being up.

This document exists because "we didn't deploy" is not the same as "we didn't
think about deploying". What follows is what the topology requires, and what I
would do.

## What the topology requires

```
Streamlit UI  ──►  FastAPI backend  ──►  api.weather.gov
                                     └─► Anthropic
```

Two facts about that shape are worth stating, because both are easy to get
wrong:

**No CORS configuration is needed.** The UI calls the backend with `httpx` from
its own Python process, server-side — `ui/streamlit_app.py:195`. Browser CORS
rules never see that request. They would if the UI called the API from browser
JavaScript, which is a good reason not to.

**Only the backend holds a secret.** `ANTHROPIC_API_KEY` belongs to the API
service alone. The frontend needs `WRA_API_URL`, which is not a secret — it
defaults to `http://127.0.0.1:8000` and is read once, at
`ui/streamlit_app.py:40`.

## What deploying would involve

The two images already exist and are built to be hosted, which is most of the
work: [`docker/api.Dockerfile`](../docker/api.Dockerfile) reads `$PORT` from the
environment, so any platform that injects one can run it unmodified, and the
snapshots are baked in, so the container needs no network at startup beyond the
live NWS call.

For the backend, any host that runs a Dockerfile: point it at
`docker/api.Dockerfile` with build context `.`, set `ANTHROPIC_API_KEY` in the
platform's secret store, and use `/health` as the health check — it reports hub
count and load state, not just liveness.

For the frontend, Streamlit Community Cloud runs `ui/streamlit_app.py` directly.
Point it at **`requirements-ui.txt`**, not `requirements.txt`: the UI needs
three packages, and installing the full backend for a client that imports
neither the agent nor the engine would quietly destroy the one boundary this
architecture is built on.

## The operational caveat that actually matters

Free hosting tiers generally sleep when idle and take time to wake. A cold
backend during a live demo is indistinguishable from a broken one.

So if this is ever demoed from a hosted URL, hit the backend first and wait for
it to answer before opening the UI:

```bash
curl https://<backend-host>/health     # wait for {"status":"ok", ...}
```

That is part of why `/health` exists.

I am deliberately not listing free-tier limits, prices or cold-start times for
specific platforms. Those change faster than a document like this gets updated,
and a confident wrong number about someone's pricing is worse than no number.
Whoever deploys this should check the current terms of the platform they pick.

## Secret handling

`.env` is gitignored; [`.env.example`](../.env.example) documents the variables
with no values. `docker-compose.yml` reads the key from the environment and
fails loudly if it is unset, rather than starting a service that will break on
its first question. The key is never a Dockerfile `ENV`, never a build argument,
and never reaches the frontend service.
