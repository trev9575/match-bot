# Stats service: parses MatchZy demos, stores stats, serves the website + API.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cs2stats ./cs2stats
COPY backfill ./backfill
ENV PYTHONUNBUFFERED=1 DEMO_DIR=/demos DB_PATH=/data/stats.db BACKFILL_DB=/app/backfill/stats.db PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')"
CMD ["python", "-m", "cs2stats.server"]
