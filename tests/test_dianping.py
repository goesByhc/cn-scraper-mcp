from unittest.mock import MagicMock

from cn_scraper_mcp.engines.dianping import DianpingEngine


def test_search_extracts_shop_ids_and_names():
    engine = DianpingEngine()
    engine.http = MagicMock()
    engine.http.get_text.return_value = (200, '<a href="/shop/123"><h4>好吃店</h4></a>')
    result = engine.search("火锅", city="上海")
    assert result["city"] == "上海"
    assert result["items"][0]["id"] == "123"
    assert result["items"][0]["name"] == "好吃店"


def test_shop_reads_json_ld_and_reviews():
    engine = DianpingEngine()
    engine.http = MagicMock()
    engine.http.get_text.side_effect = [
        (200, '<script type="application/ld+json">{"name":"店铺","aggregateRating":{"ratingValue":4.5}}</script>'),
        (200, '<div class="review-words">很好吃</div>'),
    ]
    assert engine.shop("123")["rating"] == 4.5
    assert engine.reviews("123")["reviews"][0]["content"] == "很好吃"

