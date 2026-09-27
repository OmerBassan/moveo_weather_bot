# The FastAPI backend: agent, tools, scoring engine and the frozen snapshots.
# This is the only image that needs ANTHROPIC_API_KEY.
FROM python:3.12-slim

WORKDIR /srv

# Dependencies first so a code change does not invalidate the wheel layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The snapshots are committed data, not build artefacts: the image is
# self-contained and needs no network at start-up beyond the live NWS call.
COPY app/ app/
COPY data/ data/

# NEVER bake the key in. Provided at run time by the platform's secret store.
ENV PYTHONUNBUFFERED=1 \
    PYDANTIC_AI_NO_BANNER=1 \
    PORT=8000

EXPOSE 8000

# Cloud Run and Render both inject $PORT; the default covers local use.
CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
