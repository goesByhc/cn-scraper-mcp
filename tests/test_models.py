"""Unit tests for unified data models — normalize functions and data classes.

ALL fixtures — no real network, filesystem, or Chrome.
"""

from cn_scraper_mcp.models import (
    ContentItem,
    ProductItem,
    SearchItem,
    TrendItem,
    normalize_douyin,
    normalize_douyin_hot,
    normalize_jd,
    normalize_pdd,
    normalize_taobao,
    normalize_weibo,
    normalize_weibo_hot,
    normalize_xiaohongshu,
    normalize_zhihu,
    normalize_zhihu_hot,
    normalize_zsxq,
)

# ═══════════════════════════════════════════════════════════════
# Fixtures — raw items matching each platform's real output shape
# ═══════════════════════════════════════════════════════════════


def _taobao_item() -> dict:
    return {
        "title": "华为Mate70 Pro 5G智能手机 12+256G 曜金黑",
        "price": "¥6999.00",
        "origPrice": "¥8999.00",
        "sales": "2.3万+",
        "id": "789012345678",
        "shop": "华为官方旗舰店",
        "url": "https://item.taobao.com/item.htm?id=789012345678",
    }


def _taobao_item_no_shop() -> dict:
    return {
        "title": "华为Mate70 Pro+ 16+512G 幻影紫",
        "price": "¥8999.00",
        "origPrice": "¥10999.00",
        "sales": "8900+",
        "id": "789012345680",
        "shop": "",
        "url": "https://item.taobao.com/item.htm?id=789012345680",
    }


def _taobao_item_no_priceshow() -> dict:
    return {
        "title": "Bare Item",
        "price": "99.99",
        "origPrice": "",
        "sales": "50+",
        "id": "222",
        "shop": None,
        "url": "https://item.taobao.com/item.htm?id=222",
    }


def _jd_item() -> dict:
    return {
        "sku": "100012345678",
        "name": "京东京造 沐光系列 无线蓝牙耳机 白色",
        "price": 299.0,
        "ad": False,
        "url": "https://item.jd.com/100012345678.html",
    }


def _jd_item_ad() -> dict:
    return {
        "sku": "100012345682",
        "name": "广告商品 促销爆款",
        "price": 99.0,
        "ad": True,
        "url": "https://item.jd.com/100012345682.html",
    }


def _jd_item_no_price() -> dict:
    return {
        "sku": "no_price_001",
        "name": "无价格商品",
        "price": None,
        "ad": False,
        "url": "https://item.jd.com/no_price_001.html",
    }


def _pdd_item() -> dict:
    return {
        "goodsId": "1234567890",
        "name": "超好用的儿童学习桌 可升降 实木",
        "price": 299.0,
        "sold": 15000,
        "url": "https://mobile.yangkeduo.com/goods2.html?goods_id=1234567890",
    }


def _pdd_item_string_price() -> dict:
    return {
        "goodsId": "str_001",
        "name": "字符串价格商品",
        "price": "199.00",
        "sold": 500,
        "url": "",
    }


def _pdd_item_no_goods_id() -> dict:
    return {
        "goodsId": "",
        "name": "无ID商品",
        "price": 50.0,
        "sold": 100,
        "url": "",
    }


def _xhs_item() -> dict:
    return {
        "title": "超好用的儿童学习桌推荐",
        "author": "宝妈小李",
        "likes": 12000,
        "noteId": "64a1b2c3d4e5f6a7b8c9d0e1",
        "href": "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e1?xsec_token=abc123",
        "xsec_token": "abc123token",
    }


def _xhs_item_no_author() -> dict:
    return {
        "title": "学习桌避坑指南",
        "author": "",
        "likes": 999,
        "noteId": "64a1b2c3d4e5f6a7b8c9d0e2",
        "href": "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e2",
        "xsec_token": "def456token",
    }


def _zhihu_item() -> dict:
    return {
        "title": "半导体行业投资趋势分析",
        "excerpt": "近年来半导体行业经历了巨大的变革...",
        "url": "https://www.zhihu.com/question/12345",
        "type": "answer",
        "votes": 3200,
        "comments": 180,
        "id": 12345,
    }


def _zhihu_item_no_excerpt() -> dict:
    return {
        "title": "诺奖经济",
        "excerpt": "",
        "url": "https://www.zhihu.com/question/77777",
        "type": "question",
        "votes": 800,
        "comments": 42,
        "id": 77777,
    }


def _weibo_item() -> dict:
    return {
        "id": "5123456789012345",
        "text": "华为Mate70真是太厉害了！拍照效果惊艳",
        "user": "数码爱好者",
        "user_id": "1234567890",
        "attitudes": 2300,
        "comments": 156,
        "reposts": 89,
        "created_at": "Mon Jul 13 19:32:20 +0800 2026",
        "url": "https://weibo.com/1234567890/5123456789012345",
    }


def _weibo_item_no_created_at() -> dict:
    return {
        "id": "5123456789012346",
        "text": "分享一下@华为终端的新品体验",
        "user": "科技小明",
        "user_id": "1234567891",
        "attitudes": 1200,
        "comments": 45,
        "reposts": 32,
        "created_at": "",
        "url": "https://weibo.com/1234567891/5123456789012346",
    }


def _douyin_item() -> dict:
    return {
        "title": "华为发布会",
        "author": "@华为官方",
        "views": "50.5万",
        "duration": "03:21",
        "date": "2026-07-13",
    }


def _douyin_item_no_author() -> dict:
    return {
        "title": "华为手机评测",
        "author": "",
        "views": "12.3万",
        "duration": "08:15",
        "date": "2026-07-12",
    }


def _zsxq_item() -> dict:
    return {
        "topic_id": "100001",
        "title": "今日复盘：半导体板块分析",
        "text": "今天半导体板块整体走强，重点关注设备龙头...",
        "author": "投资笔记",
        "author_id": "88801",
        "created_at": "2026-07-12T20:30:00+08:00",
        "likes": 42,
        "comments_count": 15,
        "readers": 320,
        "is_article": False,
        "article_url": None,
        "has_images": True,
        "comments": [],
    }


def _zsxq_item_no_title() -> dict:
    return {
        "topic_id": "100002",
        "title": "",
        "text": "简短思考",
        "author": "投资笔记",
        "author_id": "88801",
        "created_at": "2026-07-12T18:00:00+08:00",
        "likes": 23,
        "comments_count": 8,
        "readers": 150,
        "is_article": False,
        "article_url": None,
        "has_images": False,
        "comments": [],
    }


# ── Hot list fixtures ────────────────────────────────────────


def _douyin_hot_item() -> dict:
    return {
        "word": "华为Mate80发布",
        "hot_value": 9800000,
        "position": 1,
        "label": "热1",
    }


def _douyin_hot_item_no_label() -> dict:
    return {
        "word": "周杰伦新歌",
        "hot_value": 8500000,
        "position": 2,
        "label": "",
    }


def _zhihu_hot_item() -> dict:
    return {
        "title": "华为发布Mate70系列",
        "url": "https://www.zhihu.com/questions/99999",
        "hot_metric": "1024 万热度",
        "excerpt": "华为今日正式发布Mate70系列手机...",
    }


def _zhihu_hot_item_no_metric() -> dict:
    return {
        "title": "诺奖经济",
        "url": "https://www.zhihu.com/questions/77777",
        "hot_metric": "",
        "excerpt": "2026年诺贝尔经济学奖揭晓...",
    }


def _weibo_hot_item() -> dict:
    return {
        "rank": 1,
        "word": "中国首个禁售燃油车省份确认",
        "num": 1105077,
        "url": "https://s.weibo.com/weibo?q=中国首个禁售燃油车省份确认",
        "note": "中国首个禁售燃油车省份确认",
        "label": "爆",
    }


def _weibo_hot_item_no_label() -> dict:
    return {
        "rank": 3,
        "word": "华为Mate70发布会",
        "num": 980000,
        "url": "https://s.weibo.com/weibo?q=华为Mate70发布会",
        "note": "华为Mate70系列新品发布",
        "label": "",
    }


# ═══════════════════════════════════════════════════════════════
# Data classes — basic instantiation
# ═══════════════════════════════════════════════════════════════


class TestSearchItemBasic:
    def test_instantiate_minimal(self):
        item = SearchItem(platform="test", id="1", type="product", title="T")
        assert item.platform == "test"
        assert item.id == "1"
        assert item.title == "T"
        assert item.author is None
        assert item.url is None
        assert item.published_at is None
        assert item.metrics == {}
        assert item.raw == {}

    def test_instantiate_full(self):
        item = SearchItem(
            platform="test",
            id="abc",
            type="content",
            title="Hello",
            author="author1",
            url="https://example.com",
            published_at="2026-01-01",
            metrics={"views": 100},
            raw={"_raw": "data"},
        )
        assert item.author == "author1"
        assert item.url == "https://example.com"
        assert item.published_at == "2026-01-01"
        assert item.metrics["views"] == 100
        assert item.raw["_raw"] == "data"

    def test_schema_version(self):
        item = SearchItem(platform="t", id="1", type="product", title="x")
        assert item.schema_version == "1.0"

    def test_to_dict_default_excludes_raw(self):
        item = SearchItem(
            platform="t", id="1", type="product", title="x",
            raw={"_internal": 42},
        )
        d = item.to_dict()
        assert "raw" not in d
        assert d["platform"] == "t"

    def test_to_dict_include_raw(self):
        item = SearchItem(
            platform="t", id="1", type="product", title="x",
            raw={"_internal": 42},
        )
        d = item.to_dict(include_raw=True)
        assert "raw" in d
        assert d["raw"]["_internal"] == 42

    def test_to_dict_keeps_schema_version(self):
        item = SearchItem(platform="t", id="1", type="product", title="x")
        d = item.to_dict()
        assert d["schema_version"] == "1.0"


class TestProductItemBasic:
    def test_inherits_search_item(self):
        item = ProductItem(platform="taobao", id="1", type="product", title="T")
        assert isinstance(item, SearchItem)
        assert item.type == "product"

    def test_defaults(self):
        item = ProductItem(platform="taobao", id="1", type="product", title="T")
        assert item.price is None
        assert item.orig_price is None
        assert item.currency == "CNY"
        assert item.shop is None
        assert item.image_url is None

    def test_custom_currency(self):
        item = ProductItem(
            platform="taobao", id="1", type="product", title="T",
            currency="USD",
        )
        assert item.currency == "USD"

    def test_to_dict_excludes_raw(self):
        item = ProductItem(
            platform="taobao", id="1", type="product", title="T",
            price=99.9, raw={"x": 1},
        )
        d = item.to_dict()
        assert "raw" not in d
        assert d["price"] == 99.9


class TestContentItemBasic:
    def test_inherits_search_item(self):
        item = ContentItem(platform="weibo", id="1", type="content", title="T")
        assert isinstance(item, SearchItem)
        assert item.type == "content"

    def test_defaults(self):
        item = ContentItem(platform="weibo", id="1", type="content", title="T")
        assert item.excerpt is None
        assert item.tags == []

    def test_tags_list(self):
        item = ContentItem(
            platform="zhihu", id="1", type="content", title="T",
            tags=["科技", "投资"],
        )
        assert item.tags == ["科技", "投资"]

    def test_to_dict_excludes_raw(self):
        item = ContentItem(
            platform="weibo", id="1", type="content", title="T",
            excerpt="excerpt", raw={"x": 1},
        )
        d = item.to_dict()
        assert "raw" not in d
        assert d["excerpt"] == "excerpt"


class TestTrendItemBasic:
    def test_instantiate_minimal(self):
        item = TrendItem(platform="weibo", rank=1, word="test")
        assert item.platform == "weibo"
        assert item.rank == 1
        assert item.word == "test"
        assert item.hot_metric is None
        assert item.url is None
        assert item.label is None

    def test_instantiate_full(self):
        item = TrendItem(
            platform="weibo", rank=1, word="热搜话题",
            hot_metric="1105077", url="https://s.weibo.com/weibo?q=test",
            label="爆",
        )
        assert item.hot_metric == "1105077"
        assert item.url == "https://s.weibo.com/weibo?q=test"
        assert item.label == "爆"

    def test_to_dict(self):
        item = TrendItem(platform="weibo", rank=1, word="test")
        d = item.to_dict()
        assert d["platform"] == "weibo"
        assert d["rank"] == 1
        assert d["word"] == "test"
        assert d["schema_version"] == "1.0"


# ═══════════════════════════════════════════════════════════════
# normalize_taobao
# ═══════════════════════════════════════════════════════════════


class TestNormalizeTaobao:
    def test_normal_item(self):
        item = normalize_taobao(_taobao_item())
        assert isinstance(item, ProductItem)
        assert item.platform == "taobao"
        assert item.id == "789012345678"
        assert item.title == "华为Mate70 Pro 5G智能手机 12+256G 曜金黑"
        assert item.price == 6999.0
        assert item.orig_price == 8999.0
        assert item.currency == "CNY"
        assert item.shop == "华为官方旗舰店"
        assert item.image_url is None
        assert item.url == "https://item.taobao.com/item.htm?id=789012345678"
        assert item.metrics["sales"] == "2.3万+"
        assert item.type == "product"
        assert item.author is None
        assert item.published_at is None

    def test_no_shop(self):
        item = normalize_taobao(_taobao_item_no_shop())
        assert item.shop is None

    def test_shop_none(self):
        item = normalize_taobao(_taobao_item_no_priceshow())
        assert item.shop is None

    def test_price_strips_yuan_sign(self):
        item = _taobao_item()
        item["price"] = "¥6999.00"
        result = normalize_taobao(item)
        assert result.price == 6999.0

    def test_orig_price_strips_yuan_sign(self):
        item = _taobao_item()
        item["origPrice"] = "¥8999.00"
        result = normalize_taobao(item)
        assert result.orig_price == 8999.0

    def test_price_none(self):
        item = _taobao_item()
        item["price"] = None
        result = normalize_taobao(item)
        assert result.price is None

    def test_raw_included(self):
        raw = _taobao_item()
        item = normalize_taobao(raw)
        assert item.raw is raw  # same dict reference

    def test_to_dict_no_raw_by_default(self):
        item = normalize_taobao(_taobao_item())
        d = item.to_dict()
        assert "raw" not in d

    def test_to_dict_include_raw(self):
        item = normalize_taobao(_taobao_item())
        d = item.to_dict(include_raw=True)
        assert "raw" in d


# ═══════════════════════════════════════════════════════════════
# normalize_jd
# ═══════════════════════════════════════════════════════════════


class TestNormalizeJd:
    def test_normal_item(self):
        item = normalize_jd(_jd_item())
        assert isinstance(item, ProductItem)
        assert item.platform == "jd"
        assert item.id == "100012345678"
        assert item.title == "京东京造 沐光系列 无线蓝牙耳机 白色"
        assert item.price == 299.0
        assert item.orig_price is None
        assert item.currency == "CNY"
        assert item.shop is None
        assert item.image_url is None
        assert item.url == "https://item.jd.com/100012345678.html"
        assert item.metrics["ad"] is False

    def test_ad_item(self):
        item = normalize_jd(_jd_item_ad())
        assert item.metrics["ad"] is True

    def test_no_price(self):
        item = normalize_jd(_jd_item_no_price())
        assert item.price is None

    def test_raw_included(self):
        raw = _jd_item()
        item = normalize_jd(raw)
        assert item.raw is raw


# ═══════════════════════════════════════════════════════════════
# normalize_pdd
# ═══════════════════════════════════════════════════════════════


class TestNormalizePdd:
    def test_normal_item(self):
        item = normalize_pdd(_pdd_item())
        assert isinstance(item, ProductItem)
        assert item.platform == "pdd"
        assert item.id == "1234567890"
        assert item.title == "超好用的儿童学习桌 可升降 实木"
        assert item.price == 299.0
        assert item.orig_price is None
        assert item.currency == "CNY"
        assert item.shop is None
        assert item.image_url is None
        assert item.url == "https://mobile.yangkeduo.com/goods2.html?goods_id=1234567890"
        assert item.metrics["sales"] == 15000

    def test_string_price(self):
        item = normalize_pdd(_pdd_item_string_price())
        assert item.price == 199.0

    def test_no_goods_id(self):
        item = normalize_pdd(_pdd_item_no_goods_id())
        assert item.id == ""

    def test_raw_included(self):
        raw = _pdd_item()
        item = normalize_pdd(raw)
        assert item.raw is raw


# ═══════════════════════════════════════════════════════════════
# normalize_xiaohongshu
# ═══════════════════════════════════════════════════════════════


class TestNormalizeXiaohongshu:
    def test_normal_item(self):
        item = normalize_xiaohongshu(_xhs_item())
        assert isinstance(item, ContentItem)
        assert item.platform == "xiaohongshu"
        assert item.id == "64a1b2c3d4e5f6a7b8c9d0e1"
        assert item.title == "超好用的儿童学习桌推荐"
        assert item.author == "宝妈小李"
        assert item.url == "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e1?xsec_token=abc123"
        assert item.metrics["likes"] == 12000
        assert item.excerpt is None
        assert item.tags == []
        assert item.published_at is None
        assert item.type == "content"

    def test_no_author(self):
        item = normalize_xiaohongshu(_xhs_item_no_author())
        assert item.author is None

    def test_raw_included(self):
        raw = _xhs_item()
        item = normalize_xiaohongshu(raw)
        assert item.raw is raw


# ═══════════════════════════════════════════════════════════════
# normalize_zhihu
# ═══════════════════════════════════════════════════════════════


class TestNormalizeZhihu:
    def test_normal_item(self):
        item = normalize_zhihu(_zhihu_item())
        assert isinstance(item, ContentItem)
        assert item.platform == "zhihu"
        assert item.id == "12345"
        assert item.title == "半导体行业投资趋势分析"
        assert item.author is None
        assert item.url == "https://www.zhihu.com/question/12345"
        assert item.metrics["votes"] == 3200
        assert item.metrics["comments"] == 180
        assert item.excerpt == "近年来半导体行业经历了巨大的变革..."
        assert item.tags == ["answer"]

    def test_no_excerpt(self):
        item = normalize_zhihu(_zhihu_item_no_excerpt())
        assert item.excerpt is None

    def test_tags_from_type(self):
        item = normalize_zhihu(_zhihu_item_no_excerpt())
        assert item.tags == ["question"]

    def test_no_type_no_tags(self):
        raw = _zhihu_item()
        raw["type"] = ""
        item = normalize_zhihu(raw)
        assert item.tags == []


# ═══════════════════════════════════════════════════════════════
# normalize_weibo
# ═══════════════════════════════════════════════════════════════


class TestNormalizeWeibo:
    def test_normal_item(self):
        item = normalize_weibo(_weibo_item())
        assert isinstance(item, ContentItem)
        assert item.platform == "weibo"
        assert item.id == "5123456789012345"
        assert item.title == "华为Mate70真是太厉害了！拍照效果惊艳"
        assert item.author == "数码爱好者"
        assert item.url == "https://weibo.com/1234567890/5123456789012345"
        assert item.published_at == "Mon Jul 13 19:32:20 +0800 2026"
        assert item.metrics["attitudes"] == 2300
        assert item.metrics["comments"] == 156
        assert item.metrics["reposts"] == 89
        assert item.excerpt is None
        assert item.tags == []
        assert item.type == "content"

    def test_no_created_at(self):
        item = normalize_weibo(_weibo_item_no_created_at())
        assert item.published_at is None

    def test_raw_included(self):
        raw = _weibo_item()
        item = normalize_weibo(raw)
        assert item.raw is raw


# ═══════════════════════════════════════════════════════════════
# normalize_douyin
# ═══════════════════════════════════════════════════════════════


class TestNormalizeDouyin:
    def test_normal_item(self):
        item = normalize_douyin(_douyin_item())
        assert isinstance(item, ContentItem)
        assert item.platform == "douyin"
        assert item.id == "华为发布会"  # title as id
        assert item.title == "华为发布会"
        assert item.author == "@华为官方"
        assert item.url is None
        assert item.published_at == "2026-07-13"
        assert item.metrics["views"] == "50.5万"
        assert item.metrics["duration"] == "03:21"
        assert item.excerpt is None
        assert item.tags == []
        assert item.type == "content"

    def test_no_author(self):
        item = normalize_douyin(_douyin_item_no_author())
        assert item.author is None


# ═══════════════════════════════════════════════════════════════
# normalize_zsxq
# ═══════════════════════════════════════════════════════════════


class TestNormalizeZsxq:
    def test_normal_item(self):
        item = normalize_zsxq(_zsxq_item())
        assert isinstance(item, ContentItem)
        assert item.platform == "zsxq"
        assert item.id == "100001"
        assert item.title == "今日复盘：半导体板块分析"
        assert item.author == "投资笔记"
        assert item.url is None
        assert item.published_at == "2026-07-12T20:30:00+08:00"
        assert item.metrics["likes"] == 42
        assert item.metrics["comments"] == 15
        assert item.metrics["readers"] == 320
        assert item.excerpt == "今天半导体板块整体走强，重点关注设备龙头..."
        assert item.tags == []
        assert item.type == "content"

    def test_no_title(self):
        item = normalize_zsxq(_zsxq_item_no_title())
        assert item.title == ""

    def test_raw_included(self):
        raw = _zsxq_item()
        item = normalize_zsxq(raw)
        assert item.raw is raw


# ═══════════════════════════════════════════════════════════════
# normalize_douyin_hot
# ═══════════════════════════════════════════════════════════════


class TestNormalizeDouyinHot:
    def test_normal_item(self):
        item = normalize_douyin_hot(_douyin_hot_item(), rank=1)
        assert isinstance(item, TrendItem)
        assert item.platform == "douyin"
        assert item.rank == 1
        assert item.word == "华为Mate80发布"
        assert item.hot_metric == "9800000"
        assert item.url is None
        assert item.label == "热1"

    def test_no_label(self):
        item = normalize_douyin_hot(_douyin_hot_item_no_label(), rank=2)
        assert item.label is None

    def test_rank_override(self):
        """rank param should override position in raw dict."""
        item = normalize_douyin_hot(_douyin_hot_item(), rank=5)
        assert item.rank == 5


# ═══════════════════════════════════════════════════════════════
# normalize_zhihu_hot
# ═══════════════════════════════════════════════════════════════


class TestNormalizeZhihuHot:
    def test_normal_item(self):
        item = normalize_zhihu_hot(_zhihu_hot_item(), rank=1)
        assert isinstance(item, TrendItem)
        assert item.platform == "zhihu"
        assert item.rank == 1
        assert item.word == "华为发布Mate70系列"
        assert item.hot_metric == "1024 万热度"
        assert item.url == "https://www.zhihu.com/questions/99999"
        assert item.label is None

    def test_no_metric(self):
        item = normalize_zhihu_hot(_zhihu_hot_item_no_metric(), rank=3)
        assert item.hot_metric is None

    def test_rank_override(self):
        item = normalize_zhihu_hot(_zhihu_hot_item(), rank=10)
        assert item.rank == 10


# ═══════════════════════════════════════════════════════════════
# normalize_weibo_hot
# ═══════════════════════════════════════════════════════════════


class TestNormalizeWeiboHot:
    def test_normal_item(self):
        item = normalize_weibo_hot(_weibo_hot_item())
        assert isinstance(item, TrendItem)
        assert item.platform == "weibo"
        assert item.rank == 1
        assert item.word == "中国首个禁售燃油车省份确认"
        assert item.hot_metric == "1105077"
        assert item.url == "https://s.weibo.com/weibo?q=中国首个禁售燃油车省份确认"
        assert item.label == "爆"

    def test_no_label(self):
        item = normalize_weibo_hot(_weibo_hot_item_no_label())
        assert item.label is None

    def test_to_dict(self):
        item = normalize_weibo_hot(_weibo_hot_item())
        d = item.to_dict()
        assert d["platform"] == "weibo"
        assert d["rank"] == 1
        assert d["schema_version"] == "1.0"


# ═══════════════════════════════════════════════════════════════
# Cross-platform consistency
# ═══════════════════════════════════════════════════════════════


class TestCrossPlatformConsistency:
    """Verify that all normalize functions produce compatible structures."""

    def test_all_ecommerce_yield_productitem(self):
        for normalize_fn in [normalize_taobao, normalize_jd, normalize_pdd]:
            raw = {
                "id": "1", "title": "T", "price": "10.00",
                "sku": "1", "name": "T",
                "goodsId": "1",
                "shop": "", "ad": False, "sold": 0,
                "url": "",
                "origPrice": "", "sales": "",
            }
            result = normalize_fn(raw)
            assert isinstance(result, ProductItem), f"{normalize_fn.__name__} did not return ProductItem"
            assert result.type == "product"

    def test_all_content_yield_contentitem(self):
        for normalize_fn in [
            normalize_xiaohongshu, normalize_zhihu, normalize_weibo,
            normalize_douyin, normalize_zsxq,
        ]:
            raw = {
                "id": "1", "title": "T", "text": "T",
                "noteId": "1", "topic_id": "1",
                "type": "",
                "author": "", "user": "",
                "votes": 0, "comments": 0, "comments_count": 0,
                "attitudes": 0, "reposts": 0,
                "likes": 0, "readers": 0,
                "views": "", "duration": "",
                "url": "", "href": "",
                "excerpt": "", "created_at": "",
                "date": "", "sales": "",
            }
            result = normalize_fn(raw)
            assert isinstance(result, ContentItem), f"{normalize_fn.__name__} did not return ContentItem"
            assert result.type == "content"

    def test_all_hot_yield_trenditem(self):
        for normalize_fn, args in [
            (normalize_douyin_hot, {"rank": 1}),
            (normalize_zhihu_hot, {"rank": 1}),
            (normalize_weibo_hot, {}),
        ]:
            raw = {
                "word": "test", "hot_value": 100,
                "title": "test", "hot_metric": "100",
                "num": 100, "rank": 1,
                "url": "", "label": "",
            }
            result = normalize_fn(raw, **args)
            assert isinstance(result, TrendItem), f"{normalize_fn.__name__} did not return TrendItem"

    def test_all_items_are_searchitem_subclass(self):
        """ContentItem and ProductItem should be SearchItem subclasses."""
        p = normalize_taobao(_taobao_item())
        c = normalize_weibo(_weibo_item())
        assert isinstance(p, SearchItem)
        assert isinstance(c, SearchItem)

    def test_no_empty_string_for_nullable_fields(self):
        """All nullable fields should use None, not empty string."""
        p = normalize_taobao(_taobao_item())
        assert p.author is None  # not ""
        assert p.published_at is None  # not ""

        c = normalize_xiaohongshu(_xhs_item_no_author())
        assert c.author is None  # not ""

        c2 = normalize_weibo(_weibo_item_no_created_at())
        assert c2.published_at is None  # not ""


# ═══════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_taobao_price_comma(self):
        """Price with comma like '1,299.00' should parse correctly."""
        raw = _taobao_item()
        raw["price"] = "¥1,299.00"
        item = normalize_taobao(raw)
        assert item.price == 1299.0

    def test_taobao_price_invalid(self):
        """Non-numeric price should fall through as None."""
        raw = _taobao_item()
        raw["price"] = "N/A"
        item = normalize_taobao(raw)
        assert item.price is None

    def test_jd_price_zero(self):
        raw = _jd_item()
        raw["price"] = 0.0
        item = normalize_jd(raw)
        assert item.price == 0.0

    def test_pdd_sold_zero(self):
        raw = _pdd_item()
        raw["sold"] = 0
        item = normalize_pdd(raw)
        assert item.metrics["sales"] == 0

    def test_empty_title(self):
        raw = _taobao_item()
        raw["title"] = ""
        item = normalize_taobao(raw)
        assert item.title == ""

    def test_missing_id(self):
        raw = _taobao_item()
        raw["id"] = ""
        item = normalize_taobao(raw)
        assert item.id == ""

    def test_zhihu_id_is_always_string(self):
        raw = _zhihu_item()
        raw["id"] = 12345  # int
        item = normalize_zhihu(raw)
        assert isinstance(item.id, str)
        assert item.id == "12345"

    def test_trenditem_schema_version(self):
        item = TrendItem(platform="test", rank=1, word="w")
        assert item.schema_version == "1.0"
