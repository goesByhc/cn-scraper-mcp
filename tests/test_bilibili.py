"""Unit tests for Bilibili's public HTTP API engine."""

from unittest.mock import MagicMock

import pytest

from cn_scraper_mcp.engines.bilibili import BilibiliEngine
from cn_scraper_mcp.errors import PlatformError, RateLimitError


@pytest.fixture
def engine():
    value = BilibiliEngine()
    value.http = MagicMock()
    return value


def test_search_parses_video_items_and_cleans_html(engine):
    engine.http.get_json.return_value = (200, {
        "code": 0,
        "message": "OK",
        "data": {
            "numResults": 123,
            "result": [{
                "aid": 123,
                "bvid": "BV1xx411c7mD",
                "title": "<em class=\"keyword\">阿根廷</em>夺冠",
                "description": "比赛集锦 &amp; 颁奖",
                "author": "足球作者",
                "mid": 456,
                "duration": "04:12",
                "play": 1000,
                "video_review": 88,
                "pubdate": 1700000000,
                "pic": "//i0.hdslb.com/test.jpg",
            }],
        },
    })

    result = engine.search("阿根廷", limit=10)

    assert result["total"] == 123
    assert result["count"] == 1
    assert result["items"][0] == {
        "bvid": "BV1xx411c7mD",
        "aid": "123",
        "title": "阿根廷夺冠",
        "description": "比赛集锦 & 颁奖",
        "author": "足球作者",
        "author_id": "456",
        "duration": "04:12",
        "views": 1000,
        "danmaku": 88,
        "comments": 0,
        "likes": 0,
        "published_at": 1700000000,
        "cover": "https://i0.hdslb.com/test.jpg",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD",
    }


def test_popular_uses_owner_and_stat_fields(engine):
    engine.http.get_json.return_value = (200, {
        "code": 0,
        "data": {
            "no_more": False,
            "list": [{
                "aid": 321,
                "bvid": "BV1ab411c7mE",
                "title": "热门视频",
                "owner": {"name": "UP主", "mid": 9},
                "stat": {"view": 50, "danmaku": 4, "reply": 3, "like": 20},
            }],
        },
    })

    result = engine.popular(limit=5)

    assert result["count"] == 1
    assert result["items"][0]["author"] == "UP主"
    assert result["items"][0]["views"] == 50
    assert result["items"][0]["comments"] == 3
    assert result["items"][0]["likes"] == 20


def test_video_detail_adds_full_stats_and_pages(engine):
    engine.http.get_json.return_value = (200, {
        "code": 0,
        "data": {
            "aid": 123,
            "bvid": "BV1xx411c7mD",
            "title": "视频详情",
            "desc": "完整简介",
            "owner": {"name": "作者", "mid": 456},
            "stat": {
                "view": 100,
                "danmaku": 5,
                "reply": 6,
                "like": 7,
                "favorite": 8,
                "coin": 9,
                "share": 10,
            },
            "pages": [{"cid": 111, "page": 1, "part": "第一集"}],
        },
    })

    result = engine.get_video("BV1xx411c7mD")

    assert result["favorites"] == 8
    assert result["coins"] == 9
    assert result["shares"] == 10
    assert result["pages"][0]["cid"] == 111


def test_comments_resolve_aid_and_return_next_cursor(engine):
    engine.http.get_json.side_effect = [
        (200, {"code": 0, "data": {"aid": 123}}),
        (200, {
            "code": 0,
            "data": {
                "cursor": {"next": 2, "is_end": False, "all_count": 99},
                "replies": [{
                    "rpid_str": "9001",
                    "member": {"uname": "评论者", "mid": "42"},
                    "content": {"message": "好看"},
                    "like": 12,
                    "rcount": 3,
                    "ctime": 1700000000,
                }],
            },
        }),
    ]

    result = engine.get_comments("BV1xx411c7mD", limit=10, cursor="")

    assert result["aid"] == "123"
    assert result["total"] == 99
    assert result["next_cursor"] == "2"
    assert result["pagination"] == "cursor"
    assert result["comments"][0]["content"] == "好看"
    second_call = engine.http.get_json.call_args_list[1]
    assert second_call.kwargs["params"]["oid"] == "123"
    assert second_call.kwargs["params"]["next"] == "0"
    assert second_call.kwargs["headers"] == {
        "Referer": "https://www.bilibili.com/video/BV1xx411c7mD"
    }


def test_comments_fall_back_to_legacy_page_api_on_minus_352(engine):
    engine.http.get_json.side_effect = [
        (200, {"code": 0, "data": {"aid": 123}}),
        (200, {"code": -352, "message": "-352"}),
        (200, {
            "code": 0,
            "data": {
                "page": {"num": 1, "size": 3, "count": 8},
                "replies": [{
                    "rpid": 9,
                    "member": {"uname": "用户", "mid": 10},
                    "content": {"message": "降级成功"},
                }],
            },
        }),
    ]

    result = engine.get_comments("BV1xx411c7mD", limit=3)

    assert result["comments"][0]["content"] == "降级成功"
    assert result["pagination"] == "page"
    assert result["next_cursor"] == "2"
    assert "/x/v2/reply" in engine.http.get_json.call_args_list[2].args[0]


def test_risk_control_code_maps_to_rate_limit(engine):
    engine.http.get_json.return_value = (200, {"code": -412, "message": "请求被拦截"})
    with pytest.raises(RateLimitError):
        engine.popular()


def test_minus_352_maps_to_rate_limit(engine):
    engine.http.get_json.return_value = (200, {"code": -352, "message": "-352"})
    with pytest.raises(RateLimitError):
        engine.popular()


def test_http_403_maps_to_rate_limit_not_cookie_error(engine):
    engine.http.get_json.return_value = (403, {"error": "forbidden"})
    with pytest.raises(RateLimitError):
        engine.search("测试")


def test_not_found_is_non_retryable_platform_error(engine):
    engine.http.get_json.return_value = (200, {"code": -404, "message": "啥都木有"})
    with pytest.raises(PlatformError) as exc_info:
        engine.get_video("BV1xx411c7mD")
    assert exc_info.value.retryable is False


def test_non_object_json_is_platform_error(engine):
    engine.http.get_json.return_value = (200, [])
    with pytest.raises(PlatformError, match="non-object"):
        engine.popular()
