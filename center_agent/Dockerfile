FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY center_agent ./center_agent

ENV PYTHONUNBUFFERED=1
# One agent serves exactly one center; CENTER/PORT/DATA come from compose.
CMD ["sh", "-c", "python -m center_agent.server --center ${CENTER} --port ${PORT:-9100} --data ${DATA_DIR:-/data}/${CENTER}"]
