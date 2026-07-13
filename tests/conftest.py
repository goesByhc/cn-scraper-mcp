"""Shared test fixtures and configuration.

ALL tests use Mock/unittest.mock — NO real network, filesystem, or Chrome.
"""

import json
import re
from io import BytesIO
from pathlib import Path

import pytest

# ── Markers ──────────────────────────────────────────────────────────────
# pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ── Helpers for building mock responses ──────────────────────────────────

def make_json_response(data: dict | list, status: int = 200):
    """Build an HTTP-mock response-like object that expose .read() → JSON."""
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return BytesIO(raw)


def json_body(data: dict | list) -> bytes:
    """Shorthand: return raw JSON bytes (for mock-return-value patching)."""
    return json.dumps(data, ensure_ascii=False).encode("utf-8")
