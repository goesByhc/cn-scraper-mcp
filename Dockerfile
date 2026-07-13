# ── cn-scraper-mcp Docker image ──────────────────────────────────
# Build:  docker build -t cn-scraper-mcp .
# Run:    docker run -i --rm cn-scraper-mcp
# ─────────────────────────────────────────────────────────────────

FROM python:3.13-slim

LABEL org.opencontainers.image.title="cn-scraper-mcp"
LABEL org.opencontainers.image.description="MCP server for scraping Chinese web platforms — Taobao, JD, Xiaohongshu, and more"
LABEL org.opencontainers.image.source="https://github.com/goesByhc/cn-scraper-mcp"

# ── System deps: Chromium for CDP engines (JD, XHS, PDD) ──────
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-common \
    chromium-sandbox \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Tell Python CDP engines where to find Chromium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage --headless=new"
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# ── Install the package from local source ──────────────────────
WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/

# Install and verify the CLI entry point is wired up
RUN pip install --no-cache-dir -e . \
    && cn-scraper-mcp --help 2>&1 | head -1 || true

# ── Cookie / profile directories ───────────────────────────────
# Mount your host cookie directory at runtime:
#   -v ~/.cn-scraper-cookies:/root/.cn-scraper-cookies
#   -v ~/.jd_login_profile:/root/.jd_login_profile
RUN mkdir -p /root/.cn-scraper-cookies /root/.jd_login_profile

# ── Entrypoint ─────────────────────────────────────────────────
# Launches the MCP server over stdio — this is what AI agents
# (Codex, Claude Code, Cursor, Trae, Reasonix, Hermes) connect to.
#
# For Xvfb-wrapped headful mode (needed if JD requires a real
# display), set the XVFB_WRAPPER env var at runtime:
#   docker run -i --rm -e XVFB_WRAPPER=1 cn-scraper-mcp
# This wraps the command with `xvfb-run --auto-servernum`.
ENTRYPOINT ["sh", "-c", "if [ \"$XVFB_WRAPPER\" = \"1\" ]; then exec xvfb-run --auto-servernum cn-scraper-mcp; else exec cn-scraper-mcp; fi"]
