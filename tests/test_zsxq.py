"""Unit tests for ZsxqEngine.get_topics() response parsing.

ALL mocks — no real network, filesystem, or Chrome.

We mock the _get() method at the instance level.
"""

import json
from unittest.mock import Mock

import pytest

from cn_scraper_mcp.engines.zsxq import ZsxqEngine
from cn_scraper_mcp.http import HttpClient


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_engine() -> ZsxqEngine:
    """Build a ZsxqEngine bypassing __init__ (no real cookie file)."""
    eng = ZsxqEngine.__new__(ZsxqEngine)
    eng.cookies_path = "/fake/path/zsxq.json"
    eng.cookies = {"zsxq_access_token": "fake_token_abc123"}
    # Inject an HttpClient with max_retries=0 (no network retry in tests)
    eng.http = HttpClient(max_retries=0)
    return eng


def _normal_topics_response():
    """Realistic ZSXQ topics API response with mixed content types."""
    return {
        "succeeded": True,
        "resp_data": {
            "topics": [
                {
                    "topic_id": 100001,
                    "title": "今日复盘：半导体板块分析",
                    "create_time": "2026-07-12T20:30:00+08:00",
                    "likes_count": 42,
                    "comments_count": 15,
                    "readers_count": 320,
                    "talk": {
                        "text": "今天半导体板块整体走强，重点关注设备龙头...",
                        "owner": {
                            "user_id": 88801,
                            "name": "投资笔记",
                        },
                        "images": [{"url": "https://img.zsxq.com/abc.jpg"}],
                    },
                    "show_comments": [
                        {
                            "owner": {"name": "小王"},
                            "text": "感谢分享，学到了",
                            "likes_count": 5,
                            "create_time": "2026-07-12T21:00:00+08:00",
                        },
                        {
                            "owner": {"name": "小李"},
                            "text": "老师辛苦了",
                            "likes_count": 3,
                            "create_time": "2026-07-12T21:05:00+08:00",
                        },
                    ],
                },
                {
                    "topic_id": 100002,
                    "title": "",
                    "create_time": "2026-07-12T18:00:00+08:00",
                    "likes_count": 23,
                    "comments_count": 8,
                    "readers_count": 150,
                    "talk": {
                        "text": "简短思考",
                        "owner": {
                            "user_id": 88801,
                            "name": "投资笔记",
                        },
                    },
                    "show_comments": [],
                },
                {
                    "topic_id": 100003,
                    "title": "行业深度研报",
                    "create_time": "2026-07-11T09:00:00+08:00",
                    "likes_count": 88,
                    "comments_count": 32,
                    "readers_count": 1200,
                    "talk": {
                        "text": "预览内容在这里...",
                        "article": {
                            "id": "art_500",
                            "inline_article_url": "https://articles.zsxq.com/id_art500.html",
                            "title": "行业深度研报——半导体产业链全解析",
                        },
                        "owner": {
                            "user_id": 88801,
                            "name": "投资笔记",
                        },
                    },
                    "show_comments": [
                        {
                            "owner": {"name": "张三"},
                            "text": "这篇文章太赞了",
                            "likes_count": 12,
                            "create_time": "2026-07-11T10:00:00+08:00",
                        }
                    ],
                },
            ]
        },
    }


def _empty_topics_response():
    """Response with empty topics array."""
    return {
        "succeeded": True,
        "resp_data": {"topics": []},
    }


def _failed_response():
    """Response where API returned succeeded=false."""
    return {
        "succeeded": False,
        "error": "请先登录",
    }


def _failed_response_no_error_key():
    """Response where succeeded=false but no explicit error key."""
    return {
        "succeeded": False,
    }


# ── Tests: get_topics() ───────────────────────────────────────────────────


class TestZsxqGetTopics:
    """Test ZsxqEngine.get_topics() response parsing."""

    def test_normal_topics_with_talk_text_owner_comments(self):
        """Normal topics array → parsed correctly with all fields."""
        engine = _make_engine()
        engine._get = Mock(return_value=_normal_topics_response())

        result = engine.get_topics("28888555451", count=5)

        assert result["group_id"] == "28888555451"
        assert result["count"] == 3
        assert len(result["topics"]) == 3

        # First topic — regular post with comments
        t0 = result["topics"][0]
        assert t0["topic_id"] == "100001"
        assert t0["title"] == "今日复盘：半导体板块分析"
        assert "半导体板块整体走强" in t0["text"]
        assert len(t0["text"]) <= 500
        assert t0["author"] == "投资笔记"
        assert t0["author_id"] == "88801"
        assert t0["created_at"] == "2026-07-12T20:30:00+08:00"
        assert t0["likes"] == 42
        assert t0["comments_count"] == 15
        assert t0["readers"] == 320
        assert t0["is_article"] is False
        assert t0["article_url"] is None
        assert t0["has_images"] is True
        assert len(t0["comments"]) == 2

        # Comments
        c0 = t0["comments"][0]
        assert c0["user"] == "小王"
        assert c0["text"] == "感谢分享，学到了"
        assert c0["likes"] == 5

    def test_article_type_post_has_article_url(self):
        """Article-type posts populate is_article=True and article_url."""
        engine = _make_engine()
        engine._get = Mock(return_value=_normal_topics_response())

        result = engine.get_topics("28888555451", count=5)

        # Third topic — article post
        t2 = result["topics"][2]
        assert t2["is_article"] is True
        assert t2["article_url"] == "https://articles.zsxq.com/id_art500.html"
        assert t2["has_images"] is False

    def test_empty_topics_array(self):
        """Empty topics array → count=0, topics=[]."""
        engine = _make_engine()
        engine._get = Mock(return_value=_empty_topics_response())

        result = engine.get_topics("28888555451", count=5)

        assert result["count"] == 0
        assert result["topics"] == []

    def test_api_succeeded_false_with_error(self):
        """API returns succeeded=false with error → error dict returned."""
        engine = _make_engine()
        engine._get = Mock(return_value=_failed_response())

        result = engine.get_topics("28888555451", count=5)

        assert "error" in result
        assert result["error"] == "请先登录"
        assert result["group_id"] == "28888555451"

    def test_api_succeeded_false_no_error_key(self):
        """API returns succeeded=false with no error key → default error message."""
        engine = _make_engine()
        engine._get = Mock(return_value=_failed_response_no_error_key())

        result = engine.get_topics("28888555451", count=5)

        assert "error" in result
        assert result["error"] == "API 返回失败"

    def test_topic_without_comments(self):
        """Topic with empty show_comments → empty comments list."""
        engine = _make_engine()
        engine._get = Mock(return_value=_normal_topics_response())

        result = engine.get_topics("28888555451", count=5)

        # Second topic has no comments
        t1 = result["topics"][1]
        assert t1["comments"] == []

    def test_topic_text_truncated_to_500(self):
        """talk.text longer than 500 chars should be truncated."""
        engine = _make_engine()
        long_text = "A" * 800
        resp = {
            "succeeded": True,
            "resp_data": {
                "topics": [
                    {
                        "topic_id": 200001,
                        "title": "长文本测试",
                        "create_time": "2026-07-12T12:00:00+08:00",
                        "likes_count": 0,
                        "comments_count": 0,
                        "readers_count": 0,
                        "talk": {
                            "text": long_text,
                            "owner": {"user_id": 99999, "name": "测试用户"},
                        },
                        "show_comments": [],
                    }
                ]
            },
        }
        engine._get = Mock(return_value=resp)

        result = engine.get_topics("12345", count=5)
        t0 = result["topics"][0]
        assert len(t0["text"]) == 500

    def test_owner_only_scope(self):
        """When owner_only=True, the scope parameter should be used."""
        engine = _make_engine()
        engine._get = Mock(return_value=_normal_topics_response())

        result = engine.get_topics("28888555451", count=5, owner_only=True)

        # Verify _get was called with the correct URL containing scope=by_owner
        call_url = engine._get.call_args[0][0]
        assert "scope=by_owner" in call_url


class TestZsxqGetArticle:
    """Test ZsxqEngine.get_article() response parsing."""

    # get_article uses urllib directly, not _get()
    # Since the task focuses on get_topics() parsing, we keep this minimal.

    def test_get_article_extracts_from_ql_editor(self):
        """Article extraction from ql-editor div."""
        engine = _make_engine()

        html = "<html><body>\n<div class=\"content ql-editor\" style=\"padding:10px\">\n<p>这是文章正文内容</p>\n<p>第二段落</p>\n</div>\n</body></html>"
        engine.http.get_text = Mock(return_value=(200, html))

        result = engine.get_article("https://articles.zsxq.com/id_test.html")

        assert result["url"] == "https://articles.zsxq.com/id_test.html"
        assert "这是文章正文内容" in result["text"]
        assert "第二段落" in result["text"]
