# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Encrypt cookie files at rest.
- Add request metrics, caching, and audit logging.
- Improve experimental Douyin and Pinduoduo adapters.

## [0.4.0] - 2026-07-17

### 新增

- 新增豆瓣条目搜索、条目详情和短评/影评 MCP 工具。
- 新增大众点评商户搜索、商户详情和评价 MCP 工具。
- 豆瓣和大众点评接入本地 Cookie 管理、`guided_login` 和 `harvest_cookies`。
- 登录凭证继续只保存在用户本机，不通过 MCP 返回 Cookie 值。
- 增加两个平台的输入校验、单元测试和 MCP 契约测试。

### 改进

- 豆瓣请求使用浏览器 TLS 指纹，并在 JSON 搜索接口受限时回退到移动搜索页。
- 大众点评商户 ID 支持平台实际使用的字母数字格式，并识别页面风控响应。
- README 和架构文档补充两个平台的能力边界、登录方式和风控说明。

## [0.3.0] - 2026-07-17

### Added

- Safe two-stage and one-command release automation in `scripts/release.py`, including local quality gates, wheel installation verification, GitHub Release creation, Actions monitoring, and PyPI verification.
- Bilibili tools for public video search, popular videos, video details, and top-level comments with cursor/page fallback.

### Changed

- JD search and product details now prefer the platform's signed JSON APIs captured inside the user's Chrome session, with DOM parsing retained as a compatibility fallback.
- Documented Bilibili's public HTTP boundary and JD's browser-signing constraint.

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

[Unreleased]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/goesByhc/cn-scraper-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/goesByhc/cn-scraper-mcp/releases/tag/v0.1.0
