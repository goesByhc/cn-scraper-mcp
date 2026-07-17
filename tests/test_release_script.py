from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
SPEC = spec_from_file_location("release_script", SCRIPT)
assert SPEC and SPEC.loader
release = module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def changelog(unreleased: str) -> str:
    return f"""# Changelog

## [Unreleased]

{unreleased}

## [0.2.0] - 2026-07-16

### Added

- Old feature.

[Unreleased]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/goesByhc/cn-scraper-mcp/releases/tag/v0.2.0
"""


def test_validate_version_accepts_stable_semver():
    assert release.validate_version("1.2.3") == (1, 2, 3)


@pytest.mark.parametrize("version", ["v1.2.3", "1.2", "1.2.3rc1", "latest"])
def test_validate_version_rejects_unsupported_versions(version):
    with pytest.raises(release.ReleaseError):
        release.validate_version(version)


def test_update_changelog_keeps_planned_items_unreleased():
    original = changelog(
        """### Added

- New comments tool.

### Planned

- Future cache."""
    )

    updated = release.update_changelog(original, "0.3.0", date(2026, 7, 17))

    assert "## [Unreleased]\n\n### Planned\n\n- Future cache." in updated
    assert "## [0.3.0] - 2026-07-17\n\n### Added\n\n- New comments tool." in updated
    assert "[Unreleased]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.3.0...HEAD" in updated
    assert "[0.3.0]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.2.0...v0.3.0" in updated


def test_update_changelog_rejects_planned_only_release():
    original = changelog("### Planned\n\n- Future cache.")

    with pytest.raises(release.ReleaseError, match="no complete release notes"):
        release.update_changelog(original, "0.3.0", date(2026, 7, 17))


def test_release_notes_extracts_only_requested_version():
    original = changelog("### Added\n\n- New comments tool.")
    updated = release.update_changelog(original, "0.3.0", date(2026, 7, 17))

    assert release.release_notes(updated, "0.3.0") == "### Added\n\n- New comments tool."
