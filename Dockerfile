FROM python:3.13-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends curl openssh-client && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/caracal-hub
COPY hub/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hub/ .
RUN chmod 755 bootstrap/*.sh bootstrap/*.py

# Test stage: docker build --target test .  (used by CI, not part of the runtime image)
FROM base AS test
WORKDIR /src
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY hub/ hub/
COPY dev/ dev/
COPY tests/ tests/
RUN for f in hub/bootstrap/*.sh; do bash -n "$f"; done \
 && python -m pytest -q -p no:cacheprovider tests

FROM base AS runtime
ARG NODE_IMAGE=ghcr.io/gelluithor/caracal-node
# CARACAL_NODE_IMAGE: default image of the CARACAL nodes (can be changed in the UI)
ENV CARACAL_HUB_DATA=/var/lib/caracal-hub CARACAL_NODE_IMAGE=$NODE_IMAGE
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS http://127.0.0.1:8090/api/health || exit 1
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8090","--proxy-headers","--forwarded-allow-ips","*"]
