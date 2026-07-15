"""Deduplication, clustering, and sorting for aggregated search results.

Provides:
  - deduplicate      — rule-based title/URL similarity dedup within same platform
  - cluster_products  — group same-brand+model items, flag variants (capacity/color)
  - sort_by_relevance — rank by keyword match in title
  - sort_by_price     — rank by price (ascending/descending)
  - sort_by_popularity — rank by sales/likes/comments metrics
  - sort_by_time      — rank by published_at (newest first)
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any

# ═══════════════════════════════════════════════════════════════
# Similarity helpers
# ═══════════════════════════════════════════════════════════════


def _title_similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two title strings (0.0 – 1.0)."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ═══════════════════════════════════════════════════════════════
# deduplicate
# ═══════════════════════════════════════════════════════════════


def deduplicate(
    items: list[dict],
) -> list[dict]:
    """Remove duplicate items based on title similarity and URL identity.

    Rules:
      - Same URL (exact match) → always duplicate.
      - Same platform AND title similarity > 0.8 → duplicate.
      - When two items collide, the *first* occurrence is kept; later
        occurrences are removed.

    Each kept item gains two metadata fields:
      - ``_dedup_score``  — 1.0 for unique items; lower for items that
                            are the "best" representative of a group.
      - ``_dedup_reason`` — why the item was kept (e.g. "unique", "best_title").

    Args:
        items:       List of normalized item dicts (must have ``platform``, ``title``, ``url``).

    Returns:
        Deduplicated list with ``_dedup_score`` and ``_dedup_reason`` on each item.
    """
    if not items:
        return []

    n = len(items)
    kept: list[bool] = [True] * n
    # Track which item each duplicate is absorbed into
    best_of_group: list[int | None] = [None] * n

    for i in range(n):
        if not kept[i]:
            continue
        pi = items[i].get("platform", "")
        ti = items[i].get("title", "") or ""
        ui = items[i].get("url", "") or ""

        for j in range(i + 1, n):
            if not kept[j]:
                continue
            pj = items[j].get("platform", "")
            tj = items[j].get("title", "") or ""
            uj = items[j].get("url", "") or ""

            # 1) Same URL → duplicate
            if ui and uj and ui == uj:
                kept[j] = False
                best_of_group[j] = i
                continue

            # 2) Same platform + high title similarity
            if pi == pj and _title_similarity(ti, tj) > 0.8:
                kept[j] = False
                best_of_group[j] = i
                # (Keep the earlier item i)

    # Build result with metadata
    dup_count: dict[int, int] = {}
    for j, best in enumerate(best_of_group):
        if best is not None:
            dup_count[best] = dup_count.get(best, 0) + 1

    result: list[dict] = []
    for i in range(n):
        if not kept[i]:
            continue
        item = dict(items[i])
        cnt = dup_count.get(i, 0)
        if cnt > 0:
            item["_dedup_score"] = round(1.0 - 0.1 * cnt, 2)
            item["_dedup_reason"] = f"kept_as_best_of_{cnt + 1}_similar"
        else:
            item["_dedup_score"] = 1.0
            item["_dedup_reason"] = "unique"
        result.append(item)

    return result


# ═══════════════════════════════════════════════════════════════
# cluster_products
# ═══════════════════════════════════════════════════════════════

# Common spec variants that indicate different SKUs of the same product
_SPEC_PATTERNS: list[re.Pattern] = [
    re.compile(r"\d+\s*[GgT][Bb]", re.IGNORECASE),  # 128GB, 256GB, 1TB
    re.compile(r"\d+\s*[gGtT]"),                     # 128G, 1T
    re.compile(r"\d+\s*[升LlLMm]"),                  # 1.5L, 500ml
    re.compile(r"\d+\.?\d*\s*(?:寸|英寸|cm|mm|米)"),  # 6.7寸, 15cm
    re.compile(r"\d+\s*[Ww万]"),                     # 5000W, 5000万
    re.compile(r"\b(?:Pro|Max|Plus|Ultra|Lite|SE|Mini|S|X|XL)\b", re.IGNORECASE),
    re.compile(r"(?:标准版|旗舰版|尊享版|青春版|高配版|低配版|顶配版|入门版)"),
    re.compile(r"(?:黑色|白色|红色|蓝色|绿色|灰色|金色|银色|紫色|粉色|黄色|青色|橙色|棕色"
               r"|钛色|深空|午夜色|星光色|远峰蓝|苍岭绿|暗紫色)"),
    re.compile(r"(?:全新|二手|官翻|国行|港版|美版|日版|韩版|欧版)"),
    re.compile(r"\d+\s*[核芯]"),                      # 8核, A17芯片
    re.compile(r"\d+\s*[Gg]"),                        # remaining bare-G
]


def _base_title(title: str) -> str:
    """Strip common spec/variant substrings to obtain a base product identifier."""
    s = title
    for pat in _SPEC_PATTERNS:
        s = pat.sub("", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Remove stray punctuation at start/end
    s = s.strip(" ,-–—/|")
    return s


def _stable_hash(s: str, length: int = 8) -> str:
    """Return a short stable hex digest for *s*."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:length]


def cluster_products(items: list[dict]) -> list[dict]:
    """Group product items by brand+model, flagging capacity/colour variants.

    For each item:
      - ``cluster_id``   — short hash of the base product identifier.
      - ``is_variant``   — True if the cluster contains >1 item (i.e. there
                           are other variants of the same base product).

    Non-product items (type != "product") receive a unique cluster_id and
    ``is_variant=False``.

    Args:
        items: List of normalized item dicts.

    Returns:
        Same list with ``cluster_id`` and ``is_variant`` added to each item.
    """
    if not items:
        return []

    # Separate products from non-products
    product_indices: list[int] = []
    non_product_indices: list[int] = []
    for i, item in enumerate(items):
        if item.get("type") == "product":
            product_indices.append(i)
        else:
            non_product_indices.append(i)

    # Build base-title → indices mapping for products only
    groups: dict[str, list[int]] = {}
    for i in product_indices:
        title = items[i].get("title", "") or ""
        bt = _base_title(title)
        if not bt:
            bt = title  # fallback to raw title
        groups.setdefault(bt, []).append(i)

    # Assign cluster_id and is_variant
    result: list[dict] = [dict(item) for item in items]

    for bt, indices in groups.items():
        cid = _stable_hash(bt)
        is_var = len(indices) > 1
        for idx in indices:
            result[idx]["cluster_id"] = cid
            result[idx]["is_variant"] = is_var

    # Non-products get unique cluster_id
    for idx in non_product_indices:
        title = items[idx].get("title", "") or ""
        cid = _stable_hash(f"{title}_{idx}")
        result[idx]["cluster_id"] = cid
        result[idx]["is_variant"] = False

    return result


# ═══════════════════════════════════════════════════════════════
# Sort functions
# ═══════════════════════════════════════════════════════════════


def sort_by_relevance(items: list[dict], query: str) -> list[dict]:
    """Sort items by relevance to *query* (descending).

    Scoring heuristics:
      - +3  for exact phrase match in title (case-insensitive)
      - +2  for query appearing at the start of the title
      - +1  per query token found anywhere in the title

    Items without a title are placed at the end.
    """
    if not query or not query.strip():
        return list(items)

    q = query.lower().strip()
    tokens = q.split()

    def _score(item: dict) -> float:
        title = (item.get("title") or "").lower()
        if not title:
            return -999.0

        s = 0.0
        if q in title:
            s += 3.0
        if title.startswith(q):
            s += 2.0
        for tok in tokens:
            if tok in title:
                s += 1.0
        return s

    return sorted(items, key=_score, reverse=True)


def sort_by_price(items: list[dict], ascending: bool = True) -> list[dict]:
    """Sort items by ``price`` field.

    Items without a price (None or missing) are placed at the end.
    """
    def _key(item: dict) -> tuple[int, float]:
        p = item.get("price")
        if isinstance(p, (int, float)) and p is not None:
            return (0, float(p))
        return (1, 0.0)

    return sorted(items, key=_key, reverse=not ascending)


def sort_by_popularity(items: list[dict]) -> list[dict]:
    """Sort items by popularity metrics (descending).

    Uses ``metrics`` dict — looks for ``sales``, ``likes``, ``comments``,
    ``views``, ``votes``, ``reposts``, ``attitudes``, ``readers``.

    Items with no metrics are placed at the end.
    """
    _metric_keys = (
        "sales", "likes", "comments", "views", "votes",
        "reposts", "attitudes", "readers",
    )

    def _score(item: dict) -> float:
        metrics: dict[str, Any] = item.get("metrics", {}) or {}
        total = 0.0
        for k in _metric_keys:
            v = metrics.get(k, 0)
            try:
                total += float(v)
            except (TypeError, ValueError):
                pass
        return total

    return sorted(items, key=_score, reverse=True)


def sort_by_time(items: list[dict]) -> list[dict]:
    """Sort items by ``published_at`` field (newest first).

    Items without ``published_at`` are placed at the end.
    Uses string comparison — assumes ISO-8601 or similar sortable format.
    """
    def _key(item: dict) -> tuple[int, str]:
        ts = item.get("published_at")
        if isinstance(ts, str) and ts:
            return (1, ts)
        return (0, "")

    # Sort descending: newest (larger timestamp string) first
    return sorted(items, key=_key, reverse=True)
