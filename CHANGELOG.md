# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Safe two-stage and one-command release automation in `scripts/release.py`, including local quality gates, wheel installation verification, GitHub Release creation, Actions monitoring, and PyPI verification.
- Bilibili tools for public video search, popular videos, video details, and top-level comments with cursor/page fallback.

### Changed

- JD search and product details now prefer the platform's signed JSON APIs captured inside the user's Chrome session, with DOM parsing retained as a compatibility fallback.
- Documented Bilibili's public HTTP boundary and JD's browser-signing constraint.

### Planned

- Encrypt cookie files at rest.
- Add request metrics, caching, and audit logging.
- Improve experimental Douyin and Pinduoduo adapters.

## [0.2.0] - 2026-07-16

### Added

- Zhihu and Weibo comment tools, validated against real MCP sessions.
- Online login verification for Zhihu and Weibo.
- README guidance for CDP-based login and local-only credential storage.

### Changed

- Isolated platform engine imports so one broken adapter does not prevent the MCP server from starting.
- Moved input validation and diagnostics out of `server.py` into focused modules.
- Standardized technical errors at shared boundaries while preserving each platform's native business results.
- Corrected the MCP package version, tool descriptions, Agent configuration examples, and contract tests.
- Documented the platform-focused architecture and development guidelines for Agents.

### Removed

- `compare_prices` MCP tool and the `compare.py` cross-platform aggregation module. Agents now call platform-specific tools and make their own comparisons.
- Obsolete local skill and understand-anything configuration files.

## [0.1.0] - 2026-07-14

### Added

- MCP tools for Taobao, JD, Pinduoduo, Xiaohongshu, Zhihu, ZSXQ, Weibo, and Douyin.
- Cookie diagnostics, CDP cookie harvesting, and guided browser login.
- Browser-port locking and managed Chrome process cleanup.
- Unit tests across Python 3.11, 3.12, and 3.13.
- Deterministic MCP protocol smoke test and wheel build verification.

### Security

- Cookie values are excluded from logs.
- Cookie harvesting preserves existing credentials unless login-signal cookies are present.

### Known limitations

- Douyin support is experimental and may require manual captcha completion.
- Pinduoduo search is heavily session- and risk-control-dependent.
- Platform API and DOM changes can require adapter updates.

[Unreleased]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/goesByhc/cn-scraper-mcp/releases/tag/v0.1.0
