# Deployment

The assignment says a live URL is preferred, not mandatory, and that the demo
must be runnable live. This document covers both: `docker compose up` always
works, and one hosted path is described end to end.

## What the topology needs

Two services, one repository:

```
Streamlit UI  --HTTPS-->  FastAPI backend  --HTTPS-->  api.weather.gov
                                            + Anthropic
```

**No CORS configuration is required.** The UI calls the backend with `httpx`
from its own Python process, server-side. Browser CORS rules do not apply to
that request. (They would if the UI made the call from browser JavaScript; it
does not.)

**Only the backend needs a secret.** `ANTHROPIC_API_KEY` lives on the backend
only. The frontend needs one non-secret variable, `WRA_API_URL`.

---

## Option A — local, always works

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
docker compose up --build
# http://localhost:8501
```

The UI waits on the API's health check, so it cannot start into a connection
error. This is the fallback if any hosted deployment is unavailable at demo
time, and it is the one path with no external dependency beyond the two APIs.

---

## Option B — hosted: Streamlit Community Cloud + Render

Chosen for the lowest setup cost that still yields a public URL. Neither
service requires a cloud billing account.

### 1. Backend on Render

- New **Web Service**, connected to this GitHub repo.
- Runtime **Docker**, Dockerfile path `docker/api.Dockerfile`, context `.`
- Environment variable `ANTHROPIC_API_KEY` — entered in the dashboard, never
  committed. If using a Blueprint, declare it with `sync: false`.
- Render injects `$PORT`; the Dockerfile's `CMD` already reads it.
- Health check path: `/health`

### 2. Frontend on Streamlit Community Cloud

- Deploy from the same GitHub repo.
- Main file path: `ui/streamlit_app.py`
- Requirements file: **`requirements-ui.txt`** — two packages. Pointing it at
  the full `requirements.txt` would install pydantic-ai and the whole backend
  for a client that imports neither.
- Secrets (`secrets.toml`) — the backend URL is not a secret, but this is where
  Streamlit Cloud sets variables:
  ```toml
  WRA_API_URL = "https://your-backend.onrender.com"
  ```
  The UI reads `WRA_API_URL` from the environment; on Streamlit Cloud set it as
  an app secret so it reaches `os.environ`.

### 3. The one thing that will bite you

**Render's free tier sleeps after 15 minutes of no inbound traffic and takes
about a minute to wake.** A cold backend during a live demo looks like a broken
system.

Before the interview:

```bash
curl https://your-backend.onrender.com/health
```

Wait for `{"status":"ok"}`, then load the UI. Do this ~5 minutes ahead. The
`/health` endpoint exists partly for this.

---

## Option C — most reliable, more setup: Cloud Run backend

Worth it only if the demo must not have a wake-up step.

- Build and push `docker/api.Dockerfile` to Artifact Registry.
- Deploy as a Cloud Run service; store the key in Secret Manager and expose it
  as an environment variable.
- **`--min-instances=1`** keeps one instance warm, removing scale-from-zero
  latency. Set it 15–30 minutes before the demo and back to 0 afterwards.

Free allowance is 180,000 vCPU-seconds, 360,000 GiB-seconds and 2M requests per
billing account per month — comfortably above demo traffic — but a **billing
account with a payment method is required**, and `--min-instances=1` bills
outside the request-based free tier while it is warm.

Cold-start latency for a Python image is not a published constant; it depends
on image pull, imports and region. Do not promise a number.

---

## Options considered and rejected

| Platform | Why not |
|---|---|
| **Railway** | The Free plan is $1/month of resource credit; the $5 is a one-time 30-day trial. Not enough for two continuously running services. |
| **Fly.io** | No general free tier for new accounts — a 2-hour / 7-day trial only. |
| **Hugging Face Spaces** | Docker compute Spaces require a paid PRO plan for personal accounts, and free hardware sleeps. |
| **Azure Container Apps** | Comparable free grant to Cloud Run but more Azure-specific configuration for no additional benefit here. |
| **Streamlit Cloud for both** | It hosts Streamlit apps, not arbitrary Docker backends. Frontend only. |

---

## Secret hygiene

`ANTHROPIC_API_KEY` must never appear in:

- the frontend service's environment (it has no use for it),
- a Dockerfile `ENV` or build argument,
- `docker-compose.yml` (it reads from `.env`, which is gitignored),
- Streamlit secrets or any browser-visible code.

`.env` is in `.gitignore`. `.env.example` documents the variables with no
values.
