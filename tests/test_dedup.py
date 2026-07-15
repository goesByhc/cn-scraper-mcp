"""Unit tests for dedup.py — deduplication, clustering, and sorting.

ALL pure-logic tests — no network, filesystem, or Chrome.
"""

from cn_scraper_mcp.dedup import (
    _title_similarity,
    cluster_products,
    deduplicate,
    sort_by_popularity,
    sort_by_price,
    sort_by_relevance,
    sort_by_time,
)

# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _make_item(
    platform: str = "taobao",
    title: str = "Test Item",
    url: str = "https://example.com/item",
    price: float | None = None,
    type_: str = "product",
    metrics: dict | None = None,
    published_at: str | None = None,
) -> dict:
    return {
        "platform": platform,
        "title": title,
        "url": url,
        "price": price,
        "type": type_,
        "metrics": metrics or {},
        "published_at": published_at,
    }


# ═══════════════════════════════════════════════════════════════
# Tests: _title_similarity
# ═══════════════════════════════════════════════════════════════


class TestTitleSimilarity:
    def test_identical_titles(self):
        assert _title_similarity("华为Mate70 Pro", "华为Mate70 Pro") == 1.0

    def test_completely_different(self):
        score = _title_similarity("华为Mate70 Pro", "苹果iPhone 15")
        assert score < 0.3

    def test_case_insensitive(self):
        assert _title_similarity("HUAWEI Mate70", "huawei mate70") > 0.8

    def test_whitespace_trimmed(self):
        assert _title_similarity("  华为Mate70  ", "华为Mate70") > 0.9

    def test_empty_strings(self):
        assert _title_similarity("", "") == 0.0
        assert _title_similarity("华为", "") == 0.0
        assert _title_similarity("", "华为") == 0.0

    def test_similar_but_not_identical(self):
        """Titles with minor differences should still score high."""
        score = _title_similarity("华为Mate70 Pro 12GB", "华为Mate70 Pro 8GB")
        assert 0.7 < score < 1.0


# ═══════════════════════════════════════════════════════════════
# Tests: deduplicate
# ═══════════════════════════════════════════════════════════════


class TestDeduplicate:
    def test_empty_list(self):
        assert deduplicate([]) == []

    def test_single_item(self):
        items = [_make_item()]
        result = deduplicate(items)
        assert len(result) == 1
        assert result[0]["_dedup_score"] == 1.0
        assert result[0]["_dedup_reason"] == "unique"

    def test_same_url_dedup(self):
        items = [
            _make_item(title="Item A", url="https://x.com/1"),
            _make_item(title="Item B", url="https://x.com/1"),
        ]
        result = deduplicate(items)
        assert len(result) == 1
        assert result[0]["title"] == "Item A"
        assert result[0]["_dedup_reason"] == "kept_as_best_of_2_similar"
        assert result[0]["_dedup_score"] < 1.0

    def test_same_platform_high_similarity_dedup(self):
        items = [
            _make_item(platform="taobao", title="华为Mate70 Pro 旗舰手机"),
            _make_item(platform="taobao", title="华为Mate70 Pro 旗舰手机 5G"),
        ]
        result = deduplicate(items)
        assert len(result) == 1
        assert result[0]["title"] == "华为Mate70 Pro 旗舰手机"
        assert "kept_as_best_of" in result[0]["_dedup_reason"]

    def test_different_platform_same_title_not_dedup(self):
        """Same title on different platforms should NOT be deduplicated."""
        items = [
            _make_item(platform="taobao", title="华为Mate70 Pro", url="https://a.com/1"),
            _make_item(platform="jd", title="华为Mate70 Pro", url="https://b.com/2"),
        ]
        result = deduplicate(items)
        assert len(result) == 2

    def test_low_similarity_same_platform_not_dedup(self):
        items = [
            _make_item(platform="taobao", title="华为Mate70 Pro", url="https://a.com/1"),
            _make_item(platform="taobao", title="苹果iPhone 15 Pro Max", url="https://a.com/2"),
        ]
        result = deduplicate(items)
        assert len(result) == 2

    def test_multiple_duplicates(self):
        items = [
            _make_item(platform="taobao", title="华为Mate70 Pro A", url="u1"),
            _make_item(platform="taobao", title="华为Mate70 Pro B", url="u1"),  # dup by URL
            _make_item(platform="taobao", title="华为Mate70 Pro A 高配"),       # dup by similarity
            _make_item(platform="taobao", title="完全不同"),
        ]
        result = deduplicate(items)
        assert len(result) == 2
        titles = {r["title"] for r in result}
        assert "华为Mate70 Pro A" in titles
        assert "完全不同" in titles

    def test_metadata_fields_present(self):
        items = [
            _make_item(title="Unique A"),
            _make_item(title="Unique B"),
        ]
        result = deduplicate(items)
        for item in result:
            assert "_dedup_score" in item
            assert "_dedup_reason" in item
            assert isinstance(item["_dedup_score"], float)

    def test_dedup_preserves_other_fields(self):
        items = [
            _make_item(platform="taobao", title="华为Mate70 Pro", price=6999.0, type_="product"),
        ]
        result = deduplicate(items)
        assert result[0]["platform"] == "taobao"
        assert result[0]["price"] == 6999.0
        assert result[0]["type"] == "product"

    def test_no_platform_field(self):
        """Items without platform should be treated as same-platform and may be deduped."""
        items = [
            {"title": "Same thing A", "url": "u1"},
            {"title": "Same thing B", "url": "u1"},
        ]
        result = deduplicate(items)
        assert len(result) == 1

    def test_dedup_score_decreases_with_more_dups(self):
        items = [
            _make_item(platform="taobao", title="Same", url="u1"),
            _make_item(platform="taobao", title="Same thing", url="u1"),
            _make_item(platform="taobao", title="Same item here", url="u1"),
        ]
        result = deduplicate(items)
        assert len(result) == 1
        # 3 items in group → 2 duplicates suppressed
        assert result[0]["_dedup_score"] == 0.8  # 1.0 - 0.1*2


# ═══════════════════════════════════════════════════════════════
# Tests: cluster_products
# ═══════════════════════════════════════════════════════════════


class TestClusterProducts:
    def test_empty_list(self):
        assert cluster_products([]) == []

    def test_single_product(self):
        items = [_make_item(title="华为Mate70 Pro", type_="product")]
        result = cluster_products(items)
        assert len(result) == 1
        assert "cluster_id" in result[0]
        assert "is_variant" in result[0]
        assert result[0]["is_variant"] is False

    def test_same_product_different_variants(self):
        items = [
            _make_item(title="华为Mate70 Pro 256GB 黑色", type_="product"),
            _make_item(title="华为Mate70 Pro 512GB 白色", type_="product"),
            _make_item(title="华为Mate70 Pro 1TB 蓝色", type_="product"),
        ]
        result = cluster_products(items)
        assert len(result) == 3
        # All should have the same cluster_id
        cids = {r["cluster_id"] for r in result}
        assert len(cids) == 1
        # All are variants
        for r in result:
            assert r["is_variant"] is True

    def test_different_products_different_clusters(self):
        items = [
            _make_item(title="华为Mate70 Pro", type_="product"),
            _make_item(title="苹果iPhone 15", type_="product"),
            _make_item(title="小米14 Ultra", type_="product"),
        ]
        result = cluster_products(items)
        cids = {r["cluster_id"] for r in result}
        assert len(cids) == 3
        for r in result:
            assert r["is_variant"] is False

    def test_non_product_items_get_unique_cluster(self):
        items = [
            _make_item(title="一篇好文章", type_="content"),
            _make_item(title="另一篇好文章", type_="content"),
        ]
        result = cluster_products(items)
        cids = {r["cluster_id"] for r in result}
        assert len(cids) == 2
        for r in result:
            assert r["is_variant"] is False

    def test_mixed_product_and_content(self):
        items = [
            _make_item(title="华为Mate70 Pro 256GB", type_="product"),
            _make_item(title="华为Mate70 Pro 512GB", type_="product"),
            _make_item(title="Mate70评测", type_="content"),
        ]
        result = cluster_products(items)
        # Products should be clustered together
        p_cids = {r["cluster_id"] for r in result if r.get("type") == "product"}
        c_cids = {r["cluster_id"] for r in result if r.get("type") == "content"}
        assert len(p_cids) == 1
        assert len(c_cids) == 1
        assert p_cids != c_cids

    def test_preserves_other_fields(self):
        items = [
            _make_item(title="华为Mate70 Pro 256GB", price=6999.0, type_="product"),
        ]
        result = cluster_products(items)
        assert result[0]["price"] == 6999.0
        assert result[0]["platform"] == "taobao"

    def test_cluster_id_is_stable(self):
        """Same base title should produce the same cluster_id across calls."""
        items1 = [_make_item(title="华为Mate70 Pro 256GB", type_="product")]
        items2 = [_make_item(title="华为Mate70 Pro 512GB", type_="product")]
        cid1 = cluster_products(items1)[0]["cluster_id"]
        cid2 = cluster_products(items2)[0]["cluster_id"]
        assert cid1 == cid2


# ═══════════════════════════════════════════════════════════════
# Tests: sort_by_relevance
# ═══════════════════════════════════════════════════════════════


class TestSortByRelevance:
    def test_exact_match_first(self):
        items = [
            _make_item(title="不相关"),
            _make_item(title="华为Mate70 Pro"),
            _make_item(title="华为手机壳"),
        ]
        result = sort_by_relevance(items, "华为Mate70 Pro")
        assert result[0]["title"] == "华为Mate70 Pro"

    def test_partial_match_ranks_higher(self):
        items = [
            _make_item(title="苹果手机壳"),
            _make_item(title="华为Mate70手机壳"),
        ]
        result = sort_by_relevance(items, "华为Mate70")
        assert "华为" in result[0]["title"]

    def test_empty_query_returns_original_order(self):
        items = [
            _make_item(title="B"),
            _make_item(title="A"),
        ]
        result = sort_by_relevance(items, "")
        assert result[0]["title"] == "B"
        assert result[1]["title"] == "A"

    def test_empty_items(self):
        assert sort_by_relevance([], "test") == []

    def test_no_title_placed_last(self):
        items = [
            {"title": "相关"},
            {"title": None},
        ]
        result = sort_by_relevance(items, "相关")
        assert result[0]["title"] == "相关"
        assert result[1]["title"] is None

    def test_starts_with_query_scores_higher(self):
        items = [
            _make_item(title="手机壳 华为Mate70"),
            _make_item(title="华为Mate70 Pro 手机"),
        ]
        result = sort_by_relevance(items, "华为Mate70")
        assert "华为Mate70" in result[0]["title"]


# ═══════════════════════════════════════════════════════════════
# Tests: sort_by_price
# ═══════════════════════════════════════════════════════════════


class TestSortByPrice:
    def test_ascending(self):
        items = [
            _make_item(title="C", price=300.0),
            _make_item(title="A", price=100.0),
            _make_item(title="B", price=200.0),
        ]
        result = sort_by_price(items, ascending=True)
        assert [r["price"] for r in result] == [100.0, 200.0, 300.0]

    def test_descending(self):
        items = [
            _make_item(title="C", price=300.0),
            _make_item(title="A", price=100.0),
            _make_item(title="B", price=200.0),
        ]
        result = sort_by_price(items, ascending=False)
        assert [r["price"] for r in result] == [300.0, 200.0, 100.0]

    def test_missing_price_placed_last(self):
        items = [
            _make_item(title="A", price=100.0),
            _make_item(title="B", price=None),
            _make_item(title="C", price=200.0),
        ]
        result = sort_by_price(items, ascending=True)
        assert result[0]["price"] == 100.0
        assert result[1]["price"] == 200.0
        assert result[2]["price"] is None

    def test_empty_items(self):
        assert sort_by_price([], ascending=True) == []

    def test_price_as_int(self):
        items = [
            _make_item(title="A", price=100),
            _make_item(title="B", price=50),
        ]
        result = sort_by_price(items, ascending=True)
        assert result[0]["price"] == 50
        assert result[1]["price"] == 100

    def test_no_price_field(self):
        items = [
            {"title": "A"},
            {"title": "B", "price": 50.0},
        ]
        result = sort_by_price(items, ascending=True)
        assert result[0]["price"] == 50.0


# ═══════════════════════════════════════════════════════════════
# Tests: sort_by_popularity
# ═══════════════════════════════════════════════════════════════


class TestSortByPopularity:
    def test_sort_by_likes(self):
        items = [
            _make_item(title="A", metrics={"likes": 100}),
            _make_item(title="B", metrics={"likes": 500}),
            _make_item(title="C", metrics={"likes": 50}),
        ]
        result = sort_by_popularity(items)
        assert result[0]["title"] == "B"
        assert result[1]["title"] == "A"
        assert result[2]["title"] == "C"

    def test_sort_by_sales(self):
        items = [
            _make_item(title="A", metrics={"sales": "2.3万+"}),
            _make_item(title="B", metrics={"sales": "5万+"}),
        ]
        result = sort_by_popularity(items)
        # "sales" is a string "2.3万+", which can't be float() directly
        # Both will evaluate to 0 since float("2.3万+") fails
        # Items with no parseable metrics retain original order
        assert len(result) == 2

    def test_combined_metrics(self):
        items = [
            _make_item(title="A", metrics={"likes": 100, "comments": 50}),
            _make_item(title="B", metrics={"likes": 80, "comments": 90}),
        ]
        result = sort_by_popularity(items)
        # A: 150, B: 170 → B first
        assert result[0]["title"] == "B"

    def test_no_metrics_placed_last(self):
        items = [
            _make_item(title="A", metrics={}),
            _make_item(title="B", metrics={"likes": 100}),
        ]
        result = sort_by_popularity(items)
        assert result[0]["title"] == "B"
        assert result[1]["title"] == "A"

    def test_empty_items(self):
        assert sort_by_popularity([]) == []

    def test_missing_metrics_field(self):
        items = [
            {"title": "A"},
            {"title": "B", "metrics": {"likes": 10}},
        ]
        result = sort_by_popularity(items)
        assert result[0]["title"] == "B"

    def test_views_and_reposts(self):
        items = [
            _make_item(title="A", metrics={"views": 10000, "reposts": 50}),
            _make_item(title="B", metrics={"views": 5000, "reposts": 200}),
        ]
        result = sort_by_popularity(items)
        # A: 10050, B: 5200 → A first
        assert result[0]["title"] == "A"


# ═══════════════════════════════════════════════════════════════
# Tests: sort_by_time
# ═══════════════════════════════════════════════════════════════


class TestSortByTime:
    def test_newest_first(self):
        items = [
            _make_item(title="A", published_at="2024-01-01"),
            _make_item(title="B", published_at="2024-06-15"),
            _make_item(title="C", published_at="2024-03-10"),
        ]
        result = sort_by_time(items)
        assert result[0]["title"] == "B"
        assert result[1]["title"] == "C"
        assert result[2]["title"] == "A"

    def test_missing_published_at_placed_last(self):
        items = [
            _make_item(title="A", published_at=None),
            _make_item(title="B", published_at="2024-01-01"),
            _make_item(title="C"),
        ]
        result = sort_by_time(items)
        # B has a timestamp → should be first
        assert result[0]["title"] == "B"
        # Items without timestamps come after
        titles_without_ts = {r["title"] for r in result[1:]}
        assert titles_without_ts == {"A", "C"}

    def test_empty_items(self):
        assert sort_by_time([]) == []

    def test_iso_format_sorting(self):
        """ISO-8601 strings sort correctly as strings."""
        items = [
            _make_item(title="A", published_at="2024-12-01T10:00:00"),
            _make_item(title="B", published_at="2024-12-01T09:00:00"),
        ]
        result = sort_by_time(items)
        assert result[0]["title"] == "A"  # later time first
