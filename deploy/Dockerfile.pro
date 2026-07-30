# TradingAgents Pro service image (paper trader + dashboard).
# The base repo's Dockerfile remains untouched for the stock workflow.
#
# NOTE: run a SINGLE uvicorn worker — the SSE broadcaster and session
# store are in-process; multiple workers would silently split the stream.
FROM node:22-slim AS frontend

WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
# build emits to ../tradingagents/... in the repo; here we redirect into
# a local dist and copy it into the python build context explicitly
RUN npx tsc -b && npx vite build --outDir /fe/dist

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.lock .
# reproducible builds: exact pinned dependency set first (SEC-02),
# then the project itself without re-resolving
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
COPY --from=frontend /fe/dist/ tradingagents/pro/dashboard/static/
# sourcemaps stay out of the wheel (CI keeps them as artifacts)
RUN find tradingagents/pro/dashboard/static -name "*.map" -delete \
    && pip install --no-cache-dir --no-deps .

FROM python:3.12-slim

# P2-01: SQLite event store on LOCAL disk (GCS FUSE at /data cannot hold
# SQLite locks); Litestream replicates it to the bucket when
# LITESTREAM_REPLICA_URL is set (see deploy/entrypoint-pro.sh).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADINGAGENTS_PRO_DATA=/data \
    TRADINGAGENTS_PRO_DB=/tmp/pro.db

COPY --from=litestream/litestream:0.3 /usr/local/bin/litestream /usr/local/bin/litestream
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# claude-cli provider: the pipeline shells out to the `claude` binary
# (Claude Code print mode, tradingagents/llm_clients/claude_cli_client.py).
# Node comes from the SAME node:22-slim image the frontend stage already
# builds with — copying the runtime binary + npm avoids apt (bookworm's
# nodejs is v18, and NodeSource would drag in curl/gnupg) and adds no new
# base image. Added weight: ~110MB node binary + ~15MB npm +
# ~60MB @anthropic-ai/claude-code (installed to /usr/local, so `claude`
# is on PATH for the `trader` user). Headless auth is the
# CLAUDE_CODE_OAUTH_TOKEN env/secret (see scripts/deploy_cloud_run.sh).
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

RUN useradd --create-home trader && mkdir -p /data && chown trader /data
USER trader
# P2-04: bake the embedding model into the image so cold boots never
# depend on the HF Hub (fallback would silently degrade to hashing)
RUN python -c "from model2vec import StaticModel; \
    StaticModel.from_pretrained('minishlab/potion-base-8M')"
VOLUME /data

# Paper trading is the only mode this image runs by default (Constraint 5).
# Live mode additionally requires a checkpointer, human approval, and a real
# venue transport — none of which exist in this image by design.
EXPOSE 8600
# service loop + dashboard in one process (single worker required:
# the SSE broadcaster is in-process). Without an LLM key the loop
# self-disables and the dashboard serves in monitor mode.
COPY deploy/entrypoint-pro.sh /entrypoint-pro.sh
CMD ["/entrypoint-pro.sh"]
