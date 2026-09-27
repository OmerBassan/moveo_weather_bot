# The Streamlit frontend. Deliberately built from requirements-ui.txt, not the
# full requirements: this image is a pure HTTP client and has no reason to
# carry pydantic-ai, the scoring engine or the 2.9 MB climatology snapshot.
# It also must never be able to reach an API key.
FROM python:3.12-slim

WORKDIR /srv

COPY requirements-ui.txt .
RUN pip install --no-cache-dir -r requirements-ui.txt

COPY ui/ ui/

ENV PYTHONUNBUFFERED=1 \
    PORT=8501 \
    WRA_API_URL=http://api:8000

EXPOSE 8501

CMD ["sh", "-c", "streamlit run ui/streamlit_app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
