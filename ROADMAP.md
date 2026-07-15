# CN Scraper MCP Roadmap

本文档描述 `cn-scraper-mcp` 从平台工具集合演进为中文互联网 Agent 数据层的实施路线。

Roadmap 不是功能许愿清单。每一阶段都包含明确的范围、依赖和验收标准；未满足当前阶段的发布门槛前，不进入下一阶段的平台扩张。

## 当前基线：v0.1.0

当前版本已经具备：

- 8 个中文互联网平台适配器：淘宝、京东、拼多多、小红书、知乎、知识星球、微博、抖音。
- 19 个 MCP 工具，覆盖搜索、热榜、详情、时间线、比价、诊断和登录。
- HTTP、REST API 与 Chrome CDP 混合采集能力。
- Cookie 收割、引导登录、登录信号校验和原子写入。
- 浏览器端口锁、受管进程清理、超时和统一参数校验。
- Python 3.11、3.12、3.13 CI。
- 单元测试、MCP 协议冒烟测试、Ruff、sdist/Wheel 构建与安装验证。

当前平台支持状态：

| 平台 | 状态 | 正常前置条件或已知限制 |
|------|------|----------------------|
| 淘宝/Tmall | 稳定 | 需要有效 Cookie；依赖 MTOP 接口 |
| 京东/JD | 稳定 | 需要已登录的持久化 Chrome Profile |
| 小红书/XHS | 稳定 | 需要登录；住宅网络环境更稳定 |
| 知乎/Zhihu | 稳定 | 搜索与热榜需要登录 |
| 知识星球/ZSXQ | 稳定 | 账号必须已经拥有对应星球访问权限 |
| 微博/Weibo | 稳定 | 热搜免登录；搜索和时间线需要登录 |
| 抖音/Douyin | 实验性支持 | 登录后可搜索；验证码可能需要手动完成 |
| 拼多多/PDD | 受限支持 | 通常只放行每个浏览器会话的首次搜索 |

## 产品原则

后续开发遵循以下原则：

1. **稳定性优先于平台数量。** 八个可维护的平台优于二十个只有骨架的平台。
2. **登录是正常能力前置条件。** 需要用户自己的登录态不等于不支持，但不得绕过账号权限。
3. **优先使用稳定接口。** 有可靠 HTTP/REST API 时不使用浏览器；必须执行页面脚本时才使用 CDP。
4. **默认保护用户凭证。** Cookie 不进入日志，不因匿名会话被覆盖，并逐步迁移到系统安全存储。
5. **错误必须可执行。** Agent 收到错误后应能判断下一步是重新登录、完成验证码、稍后重试还是等待适配器更新。
6. **真实能力必须可验证。** Mock 单元测试之外，关键平台需要低频 canary 和 MCP 端到端测试。
7. **合规优先。** 只处理用户有权访问的数据，不提供权限绕过和高频批量采集能力。

## 阶段一：v0.1.x 发布与稳定化

目标：完成首个公开版本发布，并建立最基本的发布和回归流程。

### 任务

- [ ] 发布 `v0.1.0` GitHub Release。
- [ ] 发布 `cn-scraper-mcp` 到 PyPI。
- [ ] 在全新环境验证 `pip install cn-scraper-mcp`。
- [ ] 使用 Codex 或 Claude Code 完成一次真实 MCP 客户端验收。
- [ ] 建立 Issue 模板：平台失效、安装问题、功能建议、安全问题。
- [ ] 建立 Pull Request 模板和发布检查清单。
- [ ] 为平台状态增加 `stable`、`experimental`、`limited` 三种等级。
- [ ] 为每次发布维护 `CHANGELOG.md`。

### 验收标准

- GitHub Actions 的 Python 3.11、3.12、3.13 矩阵全部通过。
- 单元测试无失败、无跳过、无 RuntimeWarning。
- Ruff、MCP smoke、Twine 和 Wheel 安装验证全部通过。
- 至少一个真实 MCP 客户端可以发现并调用全部工具。
- README 中的能力状态与真实实现一致。

## 阶段二：v0.2.0 可观测性与故障诊断

目标：平台发生变化时，能够快速判断是登录、网络、风控、DOM 还是接口问题。

### 2.1 统一错误分类 ✅

- [x] 定义稳定的错误代码（`src/cn_scraper_mcp/errors.py`）：
  - `session_expired` · `captcha_required` · `risk_controlled`
  - `network_timeout` · `browser_unavailable` · `cdp_unavailable`
  - `selector_mismatch` · `api_changed` · `permission_denied`
  - 向后兼容：`AuthRequiredError`(AUTH_REQUIRED) `RateLimitError`(RATE_LIMITED)
    `ValidationError`(INVALID_INPUT) `PlatformError`(PLATFORM_ERROR)
  - 旧类名兼容：`CookieExpiredError`(COOKIE_EXPIRED) `CookieMissingError`(COOKIE_MISSING)
    `BrowserError`(BROWSER_ERROR) `ParseError`(PARSE_ERROR)
- [x] 所有 MCP 工具统一返回 `error.code`、`error.message`、`hint` 和 `retryable`。
- [x] 为每类错误增加单元测试（`tests/test_errors.py` + `tests/test_server_error_mapping.py`）。
- [x] 引擎 dict 返回路径映射（xiaohongshu/jd 状态 → 统一错误码）。
- [x] 京东异常映射：`JDLoginWallError`→`session_expired` `JDCaptchaError`→`captcha_required`。

### 2.2 平台健康探针 ✅

- [x] `scripts/platform_health.py` 升级为统一健康检查接口
- [x] 检查 API/Cookie/DOM/CDP 端口
- [x] 输出机器可读 JSON（`{platform, status, reason, last_success, latency_ms, adapter_version}`）
- [x] 保留为独立脚本（用户决策：不新增 MCP 工具）
- [x] 63 个单元测试覆盖（`tests/test_platform_health.py`）

### 2.3 Canary smoke 框架 ✅（真实 canary 待账号）

- [x] `.github/workflows/canary.yml` — 手动触发的 8 平台 mock smoke（真实查询前不启用定时任务）
- [x] `scripts/canary_runner.py` — 每个平台固定查询（mock 占位）
- [x] 失败脱敏诊断，不保存 Cookie/完整响应
- [x] 连续失败计数持久化，达到阈值后输出 Issue 创建建议模板
- [x] 41 个单元测试（`tests/test_canary.py`）
- [ ] 专用低权限测试账号（待申请）

### 验收标准

- 任一平台故障都能归类到明确错误代码。
- 健康报告不包含 Cookie、Authorization 或敏感查询参数。
- canary 失败能在一次执行内定位到平台和故障阶段。
- canary 不因单个平台故障阻塞其他平台检查。

## 阶段三：v0.3.0 会话管理与凭证安全

目标：统一 Cookie、Chrome Profile 和 CDP 登录状态，并减少明文凭证风险。

### 3.1 SessionManager ✅

- [x] 统一会话接口（`login`/`validate`/`refresh`/`status`/`delete`）
- [x] `CookieSession`（封装 CookieFileManager 路径解析和字段校验）
- [x] `ChromeProfileSession`（管理 `~/.jd_login_profile` 等持久化 profile）
- [x] `CDPSession`（封装 BrowserLock + 端口管理 + 进程管理）
- [x] 迁移 `cookie_harvest.py` 和 `engines/jd.py` 到 SessionManager
- [x] 保持现有环境变量和文件路径兼容
- [x] 79 个单元测试（`tests/test_session.py`）

### 3.2 安全存储 ✅

- [x] `SecureStorage` 类：多后端自动选择（Windows Credential Manager / macOS Keychain / Linux Secret Service / Fernet 兜底）
- [x] `export_session` / `import_session` / `delete_session`
- [x] 明文 Cookie 文件模式继续可用，启动时安全提示
- [x] 65 个单元测试（`tests/test_secure_storage.py`）

### 3.3 自动登录诊断 ✅

- [x] `diagnose_auth_failure(platform, error_dict)` — 16 种错误码 → 中文诊断 + 可执行建议
- [x] `enrich_error_with_diagnostics` — 增强 error dict 的 hint 字段
- [x] `guided_login` 增强：显示平台/端口/剩余时间/状态变化检测
- [x] 45 个单元测试（`tests/test_auth_diagnostics.py`）

### 验收标准

- 所有平台通过同一接口查询登录状态。
- 默认日志和诊断输出中不存在 Cookie 值。
- 匿名 Cookie、部分 Cookie 和失败采集不会覆盖有效登录态。
- 加密存储在 Windows、macOS、Linux 至少各通过一次集成测试。

## 阶段四：v0.4.0 统一数据层

目标：让上层 Agent 不必理解每个平台不同的字段和返回结构。

### 4.1 公共数据模型 ✅

- [x] `SearchItem` / `ProductItem` / `ContentItem` / `TrendItem` 数据类（`src/cn_scraper_mcp/models.py`）
- [x] 明确可空字段（None），不用空字符串
- [x] 保留 `raw` 字段，to_dict() 默认不返回
- [x] 11 个 normalize 函数（所有平台）
- [x] `schema_version="1.0"`
- [x] 71 个单元测试（`tests/test_models.py`）

### 4.2 聚合搜索 ✅

- [x] `search_all` — 8 平台并发搜索
- [x] `search_products` — 电商（taobao/jd/pdd）+ 价格对比
- [x] `search_content` — 内容平台
- [x] `get_trending` — 热榜聚合
- [x] 平台白名单、每平台限制、整体超时、部分成功
- [x] 38 个单元测试（`tests/test_aggregate.py`）

### 4.3 去重与排序 ✅

- [x] 规则去重（标题相似度 > 0.8 + 同平台 URL）
- [x] 商品聚类（同款 vs 不同规格）
- [x] 排序函数（相关性/价格/热度/时间）
- [x] 集成到 aggregate.py
- [x] 48 个单元测试（`tests/test_dedup.py`）

### 验收标准

- 聚合工具返回统一 schema。
- 任一平台适配器不应把平台私有字段泄漏为必需公共字段。
- 部分平台失败时，聚合结果仍可用且能解释缺失原因。
- 商品比价不会把明显不同规格误判为同一商品。

## 阶段五：v0.5.0 ~~缓存、限流与审计~~（已移除，按用户决策）

目标：~~减少重复请求，降低平台风控风险，并为性能优化提供数据。~~

> 用户决策：SQLite 缓存、平台级限流、隐私安全审计三项功能暂不加入项目。

## 阶段六：v0.6.0 适配器与插件架构

目标：降低新增和维护平台的成本，让第三方平台扩展不必进入主仓库。

### 6.1 PlatformAdapter ✅

- [x] 统一适配器协议 ABC（`search`/`validate_session`/`health_check`/`normalize`/`capabilities`）
- [x] `TaobaoAdapter` 作为第一个迁移示例
- [x] `api_version="0.6"`, `schema_version="1.0"` 声明
- [x] 43 个单元测试（`tests/test_adapter.py`）

### 6.2 能力注册表 ✅

- [x] `CapabilityRegistry` — register/get/list_all/platforms/capability_matrix
- [x] `generate_readme_matrix()` / `generate_health_check_params()` / `generate_test_params()`
- [x] 已集成在 `src/cn_scraper_mcp/adapter.py`

### 6.3 第三方插件 ✅

- [x] `discover_plugins()` — entry_points 扫描 + `~/.cn-scraper-plugins/` 文件系统回退
- [x] `validate_plugin()` — 契约测试 + API/schema 版本检查
- [x] 插件安全隔离（损坏跳过、不阻塞核心 Server）
- [x] `plugin_template/` 最小模板
- [x] 38 个单元测试（`tests/test_plugin.py`）

### 验收标准

- 新平台无需修改聚合层、诊断层和核心 Server 即可注册。
- 第三方插件可通过契约测试验证兼容性。
- 缺失或损坏的插件不会影响核心 8 个平台。
- README 和工具能力说明能够由注册表自动生成。

## 阶段七：v0.7.0 CLI 与安装体验

目标：让用户无需理解 Cookie 目录、CDP 端口和 MCP JSON 即可完成安装。

### CLI 规划 ✅

- [x] `cn-scraper-mcp init` / `doctor` / `login <platform>` / `session list` / `session delete`
- [x] `cn-scraper-mcp config --client codex|claude` / `serve`
- [x] 所有命令支持 `--json`
- [x] `pyproject.toml` entry point 指向 `cn_scraper_mcp.cli:main`
- [x] 36 个单元测试（`tests/test_cli.py`）

### 安装与部署

- [ ] PyPI 安装成为默认推荐方式。
- [ ] 提供 Windows、macOS、Linux 的最短安装路径。
- [ ] 明确本地模式和 Docker 模式的能力差异。
- [ ] 浏览器登录平台默认推荐本地模式。
- [ ] REST API 平台允许无浏览器 Docker 部署。
- [ ] 增加升级和数据迁移文档。

### 验收标准

- 新用户能够在 10 分钟内完成安装、诊断、登录和首次搜索。
- CLI 生成的 MCP 配置可直接使用。
- 所有 CLI 命令支持机器可读 JSON 输出。
- 升级不会静默删除 Cookie、Profile 或缓存。

## 阶段八：v1.0.0 稳定版门槛

只有同时满足以下条件，才发布 `v1.0.0`：

- [ ] 公共 MCP 工具和返回 schema 有明确兼容策略。
- [ ] 核心平台使用统一适配器和 SessionManager。
- [ ] 错误代码、健康检查稳定。
- [ ] Python 3.11、3.12、3.13 全绿，0 warning。
- [ ] 核心平台具备低频真实 canary。
- [ ] Windows、macOS、Linux 至少各完成一次端到端验收。
- [ ] PyPI、GitHub Release、CHANGELOG 和迁移指南同步发布。
- [ ] 安全存储、日志脱敏和凭证删除路径经过审计。
- [ ] 至少两个真实 MCP 客户端完成兼容性验证。
- [ ] 连续两个次版本未发生破坏性数据模型变更。

## v1.0 之后的平台扩展候选

平台扩展必须建立在适配器和 canary 基础设施之上。

优先候选：

1. **B 站**：搜索、热门视频、视频信息和 UP 主公开内容。
2. **豆瓣**：电影、图书和公开条目；小组能力需要单独评估。
3. **什么值得买**：商品、优惠和公开评测内容。
4. **雪球/东方财富**：公开行情和讨论，必须明确非投资建议。
5. **公众号公开文章**：只处理用户提供的公开链接。

谨慎候选：

- 大众点评等强风控、位置敏感平台。
- 需要复杂移动端签名或设备指纹的平台。
- 主要内容来自私域、付费或非公开权限的平台。

新增平台必须满足：

- 明确合法、公开或用户已有权限的数据范围。
- 有可维护的实现路径，不以一次性破解为基础。
- 有契约测试、错误分类和健康检查。
- 有明确维护者或长期维护计划。

## 横向质量指标

每个阶段持续追踪以下指标：

| 指标 | 目标 |
|------|------|
| 单元测试 | 0 failure，0 unexpected skip，0 RuntimeWarning |
| Ruff | 0 error |
| MCP smoke | 100% 通过 |
| 包构建 | sdist/Wheel/Twine 全部通过 |
| 凭证泄露 | 0 Cookie 值、0 Authorization 泄露 |
| 核心平台 canary | 最近 7 天成功率可见 |
| 错误可诊断率 | 所有用户可见错误都有稳定 code 和 hint |
| 向后兼容 | 破坏性变更必须有迁移说明和主版本升级 |

## 明确不做

以下内容不属于项目目标：

- 绕过付费、私密群组或账号权限。
- 自动破解或绕过验证码；允许用户手动完成自己账号的验证。
- 大规模、高频、分布式商业采集。
- 伪造设备身份、批量注册账号或规避平台封禁。
- 对所有平台承诺永久稳定或强 SLA。
- 在错误日志、Issue 或测试产物中保存真实 Cookie。

## 推荐实施顺序

最短关键路径为：

```text
v0.1 发布
  → 错误分类与健康检查
  → SessionManager 与安全存储
  → 统一数据模型与 search_all
  → ~~缓存、限流和审计~~（已移除）
  → PlatformAdapter 与插件系统
  → CLI 与安装体验
  → v1.0 稳定版
  → 新平台扩展
```

## 贡献方式

提交 Roadmap 相关功能时，请在 Issue 或 PR 中注明：

- 对应阶段和任务。
- 对现有 MCP 工具和返回结构的影响。
- 是否涉及真实账号、Cookie、浏览器或网络测试。
- 新增的测试与验收证据。
- 是否需要更新 README、CHANGELOG 或迁移文档。

Roadmap 会随着平台变化和用户反馈调整，但"稳定、安全、可诊断优先于平台数量"的原则保持不变。
