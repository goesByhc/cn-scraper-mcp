from unittest.mock import MagicMock

from cn_scraper_mcp.engines.douban import DoubanEngine


def test_search_normalizes_subjects():
    engine = DoubanEngine()
    engine.http = MagicMock()
    engine.http.get_json.return_value = (200, {"subjects": [{"id": 1, "title": "<b>流浪地球</b>", "rating": {"value": 8.5}}]})
    result = engine.search("流浪地球")
    assert result["count"] == 1
    assert result["items"][0]["title"] == "流浪地球"
    assert result["items"][0]["rating"] == 8.5


def test_subject_and_reviews():
    engine = DoubanEngine()
    engine.http = MagicMock()
    engine.http.get_json.side_effect = [
        (200, {"id": 1, "title": "电影", "summary": "简介", "rating": {"value": 9}}),
        (200, {"reviews": [{"id": 2, "title": "很好", "summary": "<p>值得看</p>", "author": {"name": "用户"}}]}),
    ]
    assert engine.subject("1")["summary"] == "简介"
    assert engine.reviews("1")["reviews"][0]["content"] == "值得看"

