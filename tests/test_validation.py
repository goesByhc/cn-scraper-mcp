import pytest

from cn_scraper_mcp.errors import ValidationError
from cn_scraper_mcp.validation import (
    validate_keyword,
    validate_note_id,
    validate_platform,
    validate_port,
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
