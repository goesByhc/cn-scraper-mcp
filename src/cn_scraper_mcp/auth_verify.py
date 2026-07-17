"""Remote verification of cached platform login state.

This module is intentionally part of the shared authentication boundary. It
does not normalize search, comment, hot-list, or product results.
"""

from collections.abc import Callable

from cn_scraper_mcp.auth import AUTH_PROFILES, CookieFileManager, check_all_cookies
from cn_scraper_mcp.errors import ParseError, PlatformError, technical_error_from_http
from cn_scraper_mcp.http import HttpClient

Verifier = Callable[[dict, HttpClient], bool]


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def _verify_zhihu(cookies: dict, client: HttpClient) -> bool:
    status, data = client.get_json(
        "https://www.zhihu.com/api/v4/me",
        headers={"Cookie": _cookie_header(cookies)},
    )
    if status != 200:
        if status in (401, 403):
            return False
        raise technical_error_from_http("zhihu", status)
    if data.get("id"):
        return True
    raise ParseError("知乎登录验证响应缺少账号标识")


def _verify_weibo(cookies: dict, client: HttpClient) -> bool:
    # The desktop cookie is not accepted by m.weibo.cn/api/config. An empty
    # desktop search is a read-only authenticated probe and creates no content.
    status, data = client.get_json(
        "https://weibo.com/ajax/statuses/search",
        params={"q": "", "page": "1"},
        headers={
            "Cookie": _cookie_header(cookies),
            "Referer": "https://weibo.com/",
            "X-Requested-With": "XMLHttpRequest",
            "Client-Version": "v2.47.94",
        },
    )
    if status != 200:
        if status in (401, 403):
            return False
        raise technical_error_from_http("weibo", status)
    if data.get("ok") == 1:
        return True
    if data.get("ok") == -100:
        return False
    raise PlatformError("微博登录验证返回了无法识别的响应", retryable=False)


def _verify_zsxq(cookies: dict, client: HttpClient) -> bool:
    """Verify ZSXQ login via the current-user endpoint."""
    status, data = client.get_json(
        "https://api.zsxq.com/v2/users/self",
        headers={"Cookie": _cookie_header(cookies)},
    )
    if status != 200:
        if status in (401, 403):
            return False
        raise technical_error_from_http("zsxq", status)
    if data.get("user") or data.get("resp_data", {}).get("user"):
        return True
    raise ParseError("知识星球登录验证响应缺少账号标识")


def _verify_douyin(cookies: dict, client: HttpClient) -> bool:
    """Verify Douyin login by checking for authenticated user info."""
    status, data = client.get_json(
        "https://www.douyin.com/aweme/v1/web/user/profile/self/",
        headers={
            "Cookie": _cookie_header(cookies),
            "Referer": "https://www.douyin.com/",
        },
    )
    if status != 200:
        if status in (401, 403):
            return False
        raise technical_error_from_http("douyin", status)
    platform_status = data.get("status_code")
    if platform_status == 8:
        return False
    if platform_status not in (None, 0):
        raise PlatformError(
            f"抖音登录验证返回 status_code={platform_status}",
            retryable=False,
        )
    user = data.get("user") or data.get("user_info") or {}
    if user.get("uid") or user.get("short_id") or user.get("nickname"):
        return True
    raise ParseError("抖音登录验证响应缺少账号标识")


_REMOTE_VERIFIERS: dict[str, Verifier] = {
    "zhihu": _verify_zhihu,
    "weibo": _verify_weibo,
    "zsxq": _verify_zsxq,
    "douyin": _verify_douyin,
}


def verify_login(platform: str, *, client: HttpClient | None = None) -> dict:
    """Verify a cached login against a platform-side read-only endpoint.

    Unsupported platforms are reported explicitly; a fresh local file is never
    mislabeled as remotely verified.
    """
    if platform not in AUTH_PROFILES:
        raise ValueError(f"Unknown platform '{platform}'")

    profile = AUTH_PROFILES[platform]
    if profile.is_profile:
        cache = check_all_cookies()[platform]
        return {
            "platform": platform,
            "cache_state": cache["cache_state"],
            "verified": False,
            "remote_state": "unsupported",
            "reason": "This platform uses a browser profile; no stable read-only remote probe is available.",
        }

    manager = CookieFileManager(platform)
    cache = manager.check()
    base = {
        "platform": platform,
        "cache_state": cache["cache_state"],
        "verified": False,
    }
    if not cache["valid"]:
        return {**base, "remote_state": "local_invalid", "missing_fields": cache["missing_fields"]}

    verifier = _REMOTE_VERIFIERS.get(platform)
    if verifier is None:
        return {
            **base,
            "remote_state": "unsupported",
            "reason": "No stable read-only platform login probe is implemented yet.",
        }

    accepted = verifier(manager.load(), client or HttpClient(timeout=10, max_retries=1))
    return {
        **base,
        "verified": accepted,
        "remote_state": "verified" if accepted else "rejected",
    }


def remotely_verifiable_platforms() -> tuple[str, ...]:
    return tuple(sorted(_REMOTE_VERIFIERS))
