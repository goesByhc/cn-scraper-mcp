import pytest

from cn_scraper_mcp.errors import ValidationError
from cn_scraper_mcp.validation import (
    validate_answer_id,
    validate_item_id,
    validate_keyword,
    validate_note_id,
    validate_offset,
    validate_optional_cursor,
    validate_platform,
    validate_port,
    validate_question_id,
    validate_sku,
    validate_video_id,
)


def test_validation_module_does_not_require_server_import():
    assert validate_keyword("  阿根廷  ") == "阿根廷"


def test_note_id_accepts_unicode_alphanumeric_to_match_current_contract():
    assert validate_note_id("帖子123") == "帖子123"


@pytest.mark.parametrize("value", ["", "not-a-platform", 1])
def test_validate_platform_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        validate_platform(value)


@pytest.mark.parametrize("value", [1023, 65536, "9222"])
def test_validate_port_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        validate_port(value)


@pytest.mark.parametrize(
    "validator",
    [validate_answer_id, validate_item_id, validate_question_id, validate_sku, validate_video_id],
)
def test_numeric_id_validators(validator):
    assert validator(" 12345 ") == "12345"
    with pytest.raises(ValidationError):
        validator("12/../x")


@pytest.mark.parametrize("value", [-1, 1.5, "0", True])
def test_validate_offset_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        validate_offset(value)


def test_validate_optional_cursor_accepts_empty_or_numeric():
    assert validate_optional_cursor("") == ""
    assert validate_optional_cursor(" 123 ", "max_id") == "123"
    with pytest.raises(ValidationError):
        validate_optional_cursor("not-a-cursor", "max_id")
