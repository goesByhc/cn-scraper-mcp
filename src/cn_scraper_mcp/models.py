"""统一数据模型 — 将 8 个平台返回的异构数据标准化为上层的通用结构。

Schema Version: 1.0
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SearchItem:
    """通用搜索条目 — 所有平台搜索/内容返回的基础模型。

    所有可能为空的字段一律用 None，不用空字符串。
    """

    platform: str
    id: str
    type: str  # "product" | "content" | "user"
    title: str
    author: str | None = None
    url: str | None = None
    published_at: str | None = None
    metrics: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    schema_version: str = "1.0"

    def to_dict(self, include_raw: bool = False) -> dict:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d


@dataclass
class ProductItem(SearchItem):
    """电商商品条目 — Taobao / JD / PDD 搜索产物的标准化模型。"""

    price: float | None = None
    orig_price: float | None = None
    currency: str = "CNY"
    shop: str | None = None
    image_url: str | None = None


@dataclass
class ContentItem(SearchItem):
    """内容平台条目 — 小红书 / 知乎 / 微博 / 抖音 / 知识星球 的标准化模型。"""

    excerpt: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class TrendItem:
    """热榜条目 — 所有平台热搜/热榜的标准化模型。"""

    platform: str
    rank: int
    word: str
    hot_metric: str | None = None
    url: str | None = None
    label: str | None = None

    schema_version: str = "1.0"

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
# 标准化转换函数
# ═══════════════════════════════════════════════════════════════


def _safe_float(value: str | float | int | None) -> float | None:
    """安全地将值转为 float，失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lstrip("¥￥").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


# ── E-commerce → ProductItem ─────────────────────────────────


def normalize_taobao(raw_item: dict) -> ProductItem:
    """将 Taobao MTOP 原始条目转为 ProductItem。"""
    price = _safe_float(raw_item.get("price"))
    orig_price = _safe_float(raw_item.get("origPrice"))
    return ProductItem(
        platform="taobao",
        id=str(raw_item.get("id", "")),
        type="product",
        title=raw_item.get("title", ""),
        price=price,
        orig_price=orig_price,
        currency="CNY",
        shop=raw_item.get("shop") or None,
        image_url=None,
        url=raw_item.get("url") or None,
        metrics={"sales": raw_item.get("sales", "")},
        raw=raw_item,
    )


def normalize_jd(raw_item: dict) -> ProductItem:
    """将 JD CDP 原始条目转为 ProductItem。"""
    return ProductItem(
        platform="jd",
        id=raw_item.get("sku", ""),
        type="product",
        title=raw_item.get("name", ""),
        price=_safe_float(raw_item.get("price")),
        orig_price=None,
        currency="CNY",
        shop=None,
        image_url=None,
        url=raw_item.get("url") or None,
        metrics={"ad": raw_item.get("ad", False)},
        raw=raw_item,
    )


def normalize_pdd(raw_item: dict) -> ProductItem:
    """将 PDD CDP 原始条目转为 ProductItem。"""
    return ProductItem(
        platform="pdd",
        id=raw_item.get("goodsId", ""),
        type="product",
        title=raw_item.get("name", ""),
        price=_safe_float(raw_item.get("price")),
        orig_price=None,
        currency="CNY",
        shop=None,
        image_url=None,
        url=raw_item.get("url") or None,
        metrics={"sales": raw_item.get("sold", 0)},
        raw=raw_item,
    )


# ── Content platforms → ContentItem ──────────────────────────


def normalize_xiaohongshu(raw_item: dict) -> ContentItem:
    """将小红书 CDP 原始条目转为 ContentItem。"""
    return ContentItem(
        platform="xiaohongshu",
        id=raw_item.get("noteId", ""),
        type="content",
        title=raw_item.get("title", ""),
        author=raw_item.get("author") or None,
        url=raw_item.get("href") or None,
        metrics={"likes": raw_item.get("likes", 0)},
        excerpt=None,
        tags=[],
        raw=raw_item,
    )


def normalize_zhihu(raw_item: dict) -> ContentItem:
    """将知乎 API 原始条目转为 ContentItem。"""
    item_type = raw_item.get("type", "")
    return ContentItem(
        platform="zhihu",
        id=str(raw_item.get("id", "")),
        type="content",
        title=raw_item.get("title", ""),
        author=None,
        url=raw_item.get("url") or None,
        metrics={"votes": raw_item.get("votes", 0), "comments": raw_item.get("comments", 0)},
        excerpt=raw_item.get("excerpt") or None,
        tags=[item_type] if item_type else [],
        raw=raw_item,
    )


def normalize_weibo(raw_item: dict) -> ContentItem:
    """将微博 API 原始条目转为 ContentItem。"""
    return ContentItem(
        platform="weibo",
        id=raw_item.get("id", ""),
        type="content",
        title=raw_item.get("text", ""),
        author=raw_item.get("user") or None,
        url=raw_item.get("url") or None,
        published_at=raw_item.get("created_at") or None,
        metrics={
            "attitudes": raw_item.get("attitudes", 0),
            "comments": raw_item.get("comments", 0),
            "reposts": raw_item.get("reposts", 0),
        },
        excerpt=None,
        tags=[],
        raw=raw_item,
    )


def normalize_douyin(raw_item: dict) -> ContentItem:
    """将抖音 CDP 原始条目转为 ContentItem。"""
    return ContentItem(
        platform="douyin",
        id=raw_item.get("title", ""),  # 抖音搜索没有独立的 ID 字段
        type="content",
        title=raw_item.get("title", ""),
        author=raw_item.get("author") or None,
        url=None,
        published_at=raw_item.get("date") or None,
        metrics={
            "views": raw_item.get("views", ""),
            "duration": raw_item.get("duration", ""),
        },
        excerpt=None,
        tags=[],
        raw=raw_item,
    )


def normalize_zsxq(raw_item: dict) -> ContentItem:
    """将知识星球 API 原始条目转为 ContentItem。"""
    return ContentItem(
        platform="zsxq",
        id=raw_item.get("topic_id", ""),
        type="content",
        title=raw_item.get("title", ""),
        author=raw_item.get("author") or None,
        url=None,
        published_at=raw_item.get("created_at") or None,
        metrics={
            "likes": raw_item.get("likes", 0),
            "comments": raw_item.get("comments_count", 0),
            "readers": raw_item.get("readers", 0),
        },
        excerpt=raw_item.get("text") or None,
        tags=[],
        raw=raw_item,
    )


# ── Hot lists → TrendItem ────────────────────────────────────


def normalize_douyin_hot(raw_item: dict, rank: int) -> TrendItem:
    """将抖音热榜条目转为 TrendItem。"""
    return TrendItem(
        platform="douyin",
        rank=rank,
        word=raw_item.get("word", ""),
        hot_metric=str(raw_item.get("hot_value", "")),
        url=None,
        label=raw_item.get("label") or None,
    )


def normalize_zhihu_hot(raw_item: dict, rank: int) -> TrendItem:
    """将知乎热榜条目转为 TrendItem。"""
    return TrendItem(
        platform="zhihu",
        rank=rank,
        word=raw_item.get("title", ""),
        hot_metric=raw_item.get("hot_metric") or None,
        url=raw_item.get("url") or None,
        label=None,
    )


def normalize_weibo_hot(raw_item: dict) -> TrendItem:
    """将微博热搜条目转为 TrendItem。"""
    return TrendItem(
        platform="weibo",
        rank=raw_item.get("rank", 0),
        word=raw_item.get("word", ""),
        hot_metric=str(raw_item.get("num", "")),
        url=raw_item.get("url") or None,
        label=raw_item.get("label") or None,
    )
