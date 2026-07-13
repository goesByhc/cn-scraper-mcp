"""CN Scraper MCP — Chinese web scraping tools for AI agents."""

import os
from pathlib import Path

__version__ = "0.1.0"

# ── auto-load .env if python-dotenv is installed ──────────

def _try_load_dotenv():
    """Try to load .env file from project root (optional dependency)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # python-dotenv not installed — user manages env vars manually

    # Look for .env in: project root (dev install) or cwd
    root = Path(__file__).resolve().parent.parent.parent  # src/cn_scraper_mcp → project root
    candidates = [root / ".env", Path.cwd() / ".env"]
    for p in candidates:
        if p.exists():
            load_dotenv(p)
            return

_try_load_dotenv()
