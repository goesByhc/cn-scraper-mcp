"""Input validation shared by the MCP transport layer.

These helpers validate transport parameters only. Platform response models and
business semantics deliberately remain inside each platform engine.
"""

from cn_scraper_mcp.auth import AUTH_PROFILES
from cn_scraper_mcp.errors import ValidationError

KEYWORD_MAX_LEN = 200
LIMIT_MIN = 1
LIMIT_MAX = 50
COUNT_MIN = 1
COUNT_MAX = 20
VALID_AUTH_PLATFORMS = frozenset(AUTH_PROFILES)


def validate_keyword(keyword: str) -> str:
    if not isinstance(keyword, str):
        raise ValidationError(
            f"keyword must be a string, got {type(keyword).__name__}",
            hint="Pass a non-empty string for the keyword parameter.",
        )
    cleaned = keyword.strip()
    if not cleaned:
        raise ValidationError(
            "keyword must not be empty",
            hint="Provide a non-empty search keyword (e.g. '华为mate70').",
        )
    if len(cleaned) > KEYWORD_MAX_LEN:
        raise ValidationError(
            f"keyword must be at most {KEYWORD_MAX_LEN} characters, got {len(cleaned)}",
            hint=f"Shorten the keyword to {KEYWORD_MAX_LEN} characters or fewer.",
        )
    return cleaned


def validate_limit(limit: int, default: int = 10) -> int:
    if not isinstance(limit, int):
        limit = default
    return max(LIMIT_MIN, min(LIMIT_MAX, limit))


def validate_count(count: int, default: int = 5) -> int:
    if not isinstance(count, int):
        count = default
    return max(COUNT_MIN, min(COUNT_MAX, count))


def _validate_numeric_id(value: str, name: str, hint: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string, got {type(value).__name__}", hint=hint)
    cleaned = value.strip()
    if not cleaned or not cleaned.isdigit():
        raise ValidationError(f"{name} must be numeric, got '{cleaned}'", hint=hint)
    return cleaned


def validate_group_id(group_id: str) -> str:
    return _validate_numeric_id(
        group_id,
        "group_id",
        "Pass the numeric ZSXQ group/planet ID as a string (e.g. '28888555451').",
    )


def validate_note_id(note_id: str) -> str:
    hint = "Pass the note ID string from xiaohongshu_search results."
    if not isinstance(note_id, str):
        raise ValidationError(f"note_id must be a string, got {type(note_id).__name__}", hint=hint)
    cleaned = note_id.strip()
    if not cleaned:
        raise ValidationError("note_id must not be empty", hint=hint)
    if not cleaned.isalnum():
        raise ValidationError(f"note_id must be alphanumeric, got '{cleaned}'", hint=hint)
    return cleaned


def validate_mid(mid: str) -> str:
    return _validate_numeric_id(
        mid,
        "mid",
        "Pass the post ID from weibo_search or weibo_user_timeline results.",
    )


def validate_answer_id(answer_id: str) -> str:
    return _validate_numeric_id(
        answer_id,
        "answer_id",
        "Pass the ID from a type='answer' item returned by zhihu_search.",
    )


def validate_xsec_token(xsec_token: str) -> str:
    hint = "Pass xsec_token from the matching xiaohongshu_search item."
    if not isinstance(xsec_token, str):
        raise ValidationError(
            f"xsec_token must be a string, got {type(xsec_token).__name__}", hint=hint
        )
    cleaned = xsec_token.strip()
    if not cleaned:
        raise ValidationError("xsec_token must not be empty", hint=hint)
    if len(cleaned) > 2048:
        raise ValidationError(
            f"xsec_token must be at most 2048 characters, got {len(cleaned)}",
            hint="Pass the unmodified xsec_token from xiaohongshu_search.",
        )
    return cleaned


def validate_platform(platform: str) -> str:
    hint = f"Pass one of: {', '.join(sorted(VALID_AUTH_PLATFORMS))}"
    if not isinstance(platform, str):
        raise ValidationError(
            f"platform must be a string, got {type(platform).__name__}", hint=hint
        )
    cleaned = platform.strip().lower()
    if not cleaned:
        raise ValidationError("platform must not be empty", hint=hint)
    if cleaned not in VALID_AUTH_PLATFORMS:
        raise ValidationError(
            f"Unsupported platform '{cleaned}'",
            hint=f"Supported platforms: {', '.join(sorted(VALID_AUTH_PLATFORMS))}",
        )
    return cleaned


def validate_port(port: int | None) -> int | None:
    if port is not None and (not isinstance(port, int) or port < 1024 or port > 65535):
        raise ValidationError(
            f"port must be between 1024 and 65535, got {port}",
            hint="Provide a valid CDP debug port number.",
        )
    return port
