# 开发规范

## 目录

- [1. Codegraph：代码搜索与 Review 的入口](#1-codegraph代码搜索与-review-的入口)
- [2. 项目架构](#2-项目架构)
- [3. 新增平台引擎](#3-新增平台引擎)
- [4. 编码规范](#4-编码规范)
- [5. 异常处理](#5-异常处理)
- [6. 安全规范](#6-安全规范)
- [7. 测试规范](#7-测试规范)
- [8. PR Review 清单](#8-pr-review-清单)

---

## 1. Codegraph：代码搜索与 Review 的入口

**所有代码搜索、架构理解、变更影响分析必须以 `codegraph` 为第一入口**，禁止直接从 IDE 搜索/Glob/Grep 开始。

### 1.1 工具速查

| 场景 | 工具 | 用法 |
|---|---|---|
| **理解功能/架构/bug** | `codegraph_context` | 描述任务，一步获取入口点+关联符号+关键代码 |
| **查符号定义** | `codegraph_node` | 查单个符号的位置、签名、调用链 |
| **查谁调用了某函数** | `codegraph_callers` | 分析上游依赖 |
| **查某函数调了谁** | `codegraph_callees` | 分析下游依赖 |
| **调用路径追踪** | `codegraph_trace` | "from X to Y" 完整链路 |
| **重构影响分析** | `codegraph_impact` | 修改某符号前，列出所有受影响符号 |
| **符号名搜索** | `codegraph_search` | 按名称查符号位置（无代码体） |
| **批量查看源码** | `codegraph_explore` | 一次获取多个相关符号的完整源码 |
| **文件树** | `codegraph_files` | 项目结构概览（比 Glob 快） |
| **索引健康** | `codegraph_status` | 调试索引用，平时不用 |

### 1.2 工作流：接到新任务时

```
1. codegraph_context(task="用户的 bug 描述或功能需求")
   → 一步获取：入口函数 + 关联符号 + 关键代码片段
   → 大多数问题在这一步已经可以定位

2. 如果上下文不够：
   → codegraph_trace 追踪调用链路
   → codegraph_explore 批量查看相关文件源码

3. 修改前：
   → codegraph_impact(symbol="要改的符号")
   → 确认不会意外破坏下游调用方

4. 修改后做 Review：
   → 再次 codegraph_impact 对比影响范围
   → codegraph_context 确认修改相关代码未遗漏
```

### 1.3 禁止事项

- **禁止** 用 `grep` / `rg` / `find` 做代码搜索 — 用 `codegraph_search` 或 `codegraph_context`
- **禁止** 跳过 `codegraph_impact` 直接改核心模块 — 必须先了解影响范围
- **禁止** 凭记忆判断"这个函数没人调用" — 用 `codegraph_callers` 验证

---

## 2. 项目架构

完整的职责边界和 MCP 工具设计原则见 [`docs/architecture.md`](docs/architecture.md)。其中最重要的原则是：**Agent 负责平台选择、跨平台比较和结果综合；MCP Server 只提供平台原子能力。跨平台只统一 Cookie 获取、登录验证和凭证缓存有效性，不统一搜索、热搜、评论、商品或价格。**

```
src/cn_scraper_mcp/
├── server.py              # FastMCP 入口 — @mcp.tool() 注册 + 参数校验 + 异常映射
├── http.py                # 共享 HTTP Client (重试/退避/限速)
├── auth.py                # Cookie 文件管理 + check_all_cookies
├── cookie_harvest.py      # CDP Cookie 收割 + guided_login
├── errors.py              # 统一异常模型 (ScraperError 体系)
├── logging.py             # 脱敏日志 + 错误记录
└── engines/
    ├── __init__.py         # 公开导出
    ├── cdp.py              # CDP 底层驱动 + BrowserLock + 进程管理
    ├── taobao.py           # 淘宝 (curl_cffi + MTOP)
    ├── jd.py               # 京东 (Chrome CDP headful)
    ├── pdd.py              # 拼多多 (CDP + iPhone UA)
    ├── xiaohongshu.py      # 小红书 (Obscura/CDP)
    ├── zhihu.py            # 知乎 (REST API)
    ├── weibo.py            # 微博 (REST API)
    ├── zsxq.py             # 知识星球 (REST API)
    └── douyin.py           # 抖音 (CDP + REST)
```

### 分层职责

| 层 | 文件 | 职责 | 直接依赖 |
|---|---|---|---|
| **MCP 协议层** | `server.py` | 工具注册、参数校验、异常→MCP error 映射 | engines, errors, auth |
| **引擎层 - HTTP** | `taobao.py`, `weibo.py`, `zhihu.py`, `zsxq.py` | 平台 API 调用、响应解析、结果结构化 | http.py, auth.py |
| **引擎层 - CDP** | `jd.py`, `pdd.py`, `xiaohongshu.py`, `douyin.py` | 浏览器生命周期、CDP JS 注入、DOM 提取 | cdp.py, auth.py |
| **基础设施层** | `cdp.py`, `http.py`, `auth.py`, `cookie_harvest.py`, `errors.py`, `logging.py` | 跨引擎通用能力 | 无项目内依赖 |

### 设计原则

1. **Agent 决策，Server 执行** — 平台选择、跨平台比较、排序和综合属于 Agent；MCP Server 暴露平台原子能力
2. **只统一登录状态** — 跨平台共享仅限 Cookie 获取、登录验证和凭证缓存有效性检查
3. **平台业务不统一** — 搜索、热搜、评论、商品和价格保留各平台原生参数与返回 schema
4. **引擎之间互不依赖** — 每个引擎是自包含的，可以独立导入使用
5. **基础设施层无项目内循环依赖** — `cdp.py`、`http.py`、`errors.py`、`logging.py` 互不引用
6. **Cookie 不落日志** — `logging.py` 的 `SanitizingFormatter` 统一脱敏，引擎层不需要额外处理
7. **CDP 互斥** — 同一端口同一时间只有一个操作，通过 `BrowserLock` 保证

---

## 3. 新增平台引擎

新增平台时只实现该平台真实存在的原生能力。以下 HTTP/CDP 模板中的 `search()` 仅适用于确实提供搜索能力的平台，不是所有 Engine 必须实现的统一接口。热搜、评论、主题、文章等操作应按平台语义独立命名、独立定义参数和返回 schema。

### 3.1 判断引擎类型

| 特征 | 选择 |
|---|---|
| 纯 REST/JSON API，可直接用 curl 调用 | **HTTP 引擎** |
| 需要浏览器执行 JS、解析 DOM、过验证码 | **CDP 引擎** |

### 3.2 HTTP 引擎模板

```python
"""<平台名> 搜索引擎 — 简要说明工作原理."""

from cn_scraper_mcp.http import HttpClient
from cn_scraper_mcp.logging import get_logger

logger = get_logger("cn_scraper_mcp.engines.<module>")

class <Platform>Engine:
    """搜索 <平台名>。

    Usage:
        engine = <Platform>Engine(cookies_path="~/.cn-scraper-cookies/<file>.json")
        result = engine.search("关键词", limit=10)
    """

    def __init__(self, cookies_path: str | None = None):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "<ENV_VAR>"
            ) or str(Path.home() / ".cn-scraper-cookies" / "<file>.json")
        self.cookies_path = cookies_path
        self.cookies = {}
        if os.path.exists(cookies_path):
            try:
                self.cookies = json.load(open(cookies_path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.cookies = {}

        self.http = HttpClient(
            timeout=15, max_retries=3, backoff_base=1.0, rate_limit_interval=0.5,
        )

    def search(self, keyword: str, limit: int = 10) -> dict:
        """搜索。

        Returns:
            {"keyword": str, "items": [{...}]}
        """
        ...
```

### 3.3 CDP 引擎模板

```python
"""<平台名> 搜索引擎 — Chrome CDP + JS 注入提取."""

from .cdp import CDPClient, get_browser_lock, is_chrome_running, launch_chrome

class <Platform>Engine:
    """CDP 引擎 — 需要本地 Chrome。

    Usage:
        engine = <Platform>Engine()
        engine.ensure_browser()  # 启动/复用 Chrome
        result = engine.search("关键词", limit=10)
    """

    DEFAULT_PORT = 9xxx  # 分配一个独有端口

    def __init__(self, cookies_path=None, port=None):
        self.port = port or self.DEFAULT_PORT
        # ... cookie 加载 ...

    def ensure_browser(self) -> bool:
        """确保浏览器运行中。返回 True 表示就绪。"""
        if is_chrome_running(self.port):
            return True
        launch_chrome(self.port, profile, url="...", headless=False)
        return is_chrome_running(self.port)

    def search(self, keyword: str, limit: int = 10) -> dict:
        if not self.ensure_browser():
            return {"error": "浏览器不可用"}

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect()
                await cdp.enable()
                await cdp.navigate(search_url, wait=5)
                raw = await cdp.evaluate(EXTRACTOR_JS, return_by_value=True)
                return json.loads(raw)
            finally:
                await cdp.close()

        with get_browser_lock(self.port):
            result = asyncio.run(_do())

        return self._parse_result(keyword, result, limit)

    def cleanup(self):
        """释放浏览器资源。"""
        close_browser(self.port)
```

### 3.4 注册引擎

1. 在 `engines/__init__.py` 中添加 `from .<module> import <Platform>Engine` 并导出
2. 在 `server.py` 中为平台原生操作添加独立的 `@mcp.tool()` 包装函数，不增加跨平台聚合工具
3. 平台配置同步到 `auth.py` 的 `PLATFORM_CONFIG`
4. Cookie harvest 支持：在 `cookie_harvest.py` 的 `PLATFORM_DOMAINS`、`_LOGIN_SIGNAL_COOKIES`、`_LOGIN_URLS` 中添加条目

---

## 4. 编码规范

### 4.1 Python 版本

**Python 3.11+**（使用 `from __future__ import annotations`）。

### 4.2 格式化

```toml
# pyproject.toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

提交前运行：
```bash
ruff check src/ tests/   # 必须零警告
ruff format src/ tests/  # 自动格式化
```

### 4.3 类型注解

- **必须** 写函数签名的参数和返回值类型
- 用 `from __future__ import annotations` 延迟求值
- 复杂类型用 `dict[str, Any]` 而非 `Dict[str, Any]`
- 可选参数用 `str | None` 而非 `Optional[str]`

```python
# ✅ 正确
def search(self, keyword: str, limit: int = 10) -> dict:
    ...

# ❌ 错误 — 缺少类型
def search(self, keyword, limit=10):
    ...
```

### 4.4 Docstring

- **必须** 写模块级 docstring（第一行概述，之后详细说明）
- **必须** 写公开方法的 docstring（至少包含功能说明和返回值格式）
- 用统一的返回值格式注释：

```python
def search(self, keyword: str, limit: int = 10) -> dict:
    """搜索 <平台> 内容。

    Args:
        keyword: 搜索关键词
        limit: 最大返回条数

    Returns:
        {"keyword": str, "count": int, "items": [{...}]}
    """
```

### 4.5 命名

| 类型 | 规范 | 示例 |
|---|---|---|
| 模块 | `lowercase_with_underscores` | `cookie_harvest.py` |
| 类 | `PascalCase` | `XiaohongshuEngine` |
| 函数/方法 | `lowercase_with_underscores` | `search()`, `check_all_cookies()` |
| 私有函数 | `_lowercase_with_underscores` | `_validate_keyword()` |
| 常量 | `UPPER_SNAKE_CASE` | `_KEYWORD_MAX_LEN`, `STALE_HOURS` |
| 平台 Engine | `<Platform>Engine` | `TaobaoEngine`, `WeiboEngine` |

### 4.6 导入顺序

```python
# 1. 标准库
import json
import os
from pathlib import Path

# 2. 第三方库
from fastmcp import FastMCP

# 3. 项目内模块
from cn_scraper_mcp.http import HttpClient
from cn_scraper_mcp.errors import ScraperError

# 4. engines 内部相对导入
from .cdp import CDPClient, launch_chrome
```

---

## 5. 异常处理

### 5.1 异常层次

```
ScraperError (base)
├── CookieExpiredError     — Cookie 过期，需重新登录
├── CookieMissingError     — Cookie 文件未找到
├── AuthRequiredError      — 平台要求登录但未提供凭证
├── RateLimitError         — 平台限流
├── ParseError             — 响应解析失败（页面结构变更）
├── BrowserError           — 浏览器/CDP 通信错误
├── ValidationError        — 输入参数非法
└── PlatformError          — 通用平台错误（兜底）
```

### 5.2 Engine 自定义异常

Engine 可以定义自己的异常子类（如 `TaobaoAuthError`、`JDCaptchaError`），但**不需要继承 `ScraperError`**。在 `server.py` 的工具函数中，通过 try/except 将 engine 异常映射为标准 `ScraperError`：

```python
# server.py — 每个工具函数中的标准模式
try:
    engine = TaobaoEngine()
    return engine.search(keyword, limit=limit)
except ValidationError as e:
    return error_response(e)
except TaobaoAuthError:
    return error_response(CookieExpiredError(
        message="淘宝登录已过期",
        hint="重新登录淘宝后更新 cookie 文件。",
    ))
except Exception as e:
    record_error(e)
    return error_response(e)
```

### 5.3 返回值约定

- 成功：使用该平台原生操作的独立 schema；搜索、热搜、评论、商品和价格结果不建立跨平台公共模型
- 失败：使用结构化技术错误，通过 `error_response()` 统一构造；登录问题统一返回可执行的重新登录或 Cookie 修复提示
- 登录状态：可以统一 `{platform, state, cached, stale, missing_fields, hint}` 等不包含凭证值的字段

---

## 6. 安全规范

### 6.1 Cookie 安全（最高优先级）

1. **日志禁止输出 Cookie 值** — `logging.py` 的 `SanitizingFormatter` 自动脱敏，但各模块仍需注意不直接 print cookie
2. **日志中只记录 Cookie 名称/数量** — 不对 key=value 做字符串拼接
3. **`error_response()` 不泄漏原始异常消息** — 对未知异常包装为通用 `PlatformError`
4. **文件路径硬编码** — `cookie_harvest.py` 中的 `COOKIE_DIR` 不可由用户覆盖
5. **原子写入** — Cookie 文件先用临时文件写入，再 `rename` 替换，防止写入中断导致文件损坏
6. **登录信号校验** — `_save_cookies` 只保留包含有效登录凭证的 cookie，防止空/匿名 cookie 覆盖已有有效凭证

### 6.2 URL/请求日志脱敏

- `sanitize_url()` 去掉 query string
- `HttpClient._short_url()` 去掉 query + credentials
- 禁止日志中打印完整 URL（含 token 参数）

---

## 7. 测试规范

### 7.1 测试框架

```bash
pytest tests/ -v              # 运行全部测试
pytest tests/ --cov           # 含覆盖率报告
ruff check src/ tests/        # Lint（零警告）
python scripts/mcp_smoke_test.py  # MCP 协议冒烟测试
```

### 7.2 测试原则

1. **全部 Mock** — 单元测试不依赖真实网络、Chrome、Cookie 文件
2. **覆盖所有 MCP 工具** — 每个 `@mcp.tool()` 至少有一个测试用例
3. **覆盖所有自定义异常** — 每个异常类型有测试
4. **覆盖错误路径** — cookie 缺失、API 返回错误、解析失败等

### 7.3 测试文件命名

```
tests/
├── test_taobao.py       # 每个 engine 对应一个
├── test_jd.py
├── test_pdd.py
├── test_xiaohongshu.py
├── test_zhihu.py
├── test_weibo.py
├── test_zsxq.py
├── test_douyin.py
├── test_http.py         # 基础设施测试
├── test_cdp.py
├── test_auth.py
├── test_cookie_harvest.py
├── test_errors.py
├── test_logging.py
├── test_guided_login.py
├── test_concurrency.py  # BrowserLock 并发安全
└── conftest.py           # 共享 fixtures
```

---

## 8. PR Review 清单

### 8.1 代码搜索：必须使用 codegraph

| 检查项 | 操作 |
|---|---|
| 理解变更范围 | `codegraph_context(task="<PR 描述>")` |
| 影响分析 | `codegraph_impact(symbol="<改动的核心函数>")` |
| 调用链验证 | `codegraph_callers` / `codegraph_callees` 确认依赖未被破坏 |
| 遗漏检查 | `codegraph_explore` 对比改动前后相关符号 |

### 8.2 功能性检查

- [ ] 新增 Engine 是否在 `engines/__init__.py` 导出
- [ ] 新增 MCP Tool 是否有对应测试
- [ ] 平台配置是否同步到 `auth.py` → `PLATFORM_CONFIG`
- [ ] Cookie harvest 支持是否补全（`PLATFORM_DOMAINS` + `_LOGIN_SIGNAL_COOKIES` + `_LOGIN_URLS`）
- [ ] CDP 引擎是否使用了 `get_browser_lock(port)` 串行化
- [ ] CDP 引擎是否分配了不冲突的默认端口
- [ ] HTTP 引擎是否使用 `HttpClient`（不直接 `urllib.request`）

### 8.3 安全检查

- [ ] 日志中是否有 Cookie 值泄漏风险
- [ ] 是否有 `print(cookie)` 类的调试代码残留
- [ ] 异常处理是否通过 `error_response()` 包装，不会泄漏原始栈帧
- [ ] Cookie 文件写入是否是原子操作（先写 tmp 再 rename）

### 8.4 质量检查

```bash
# 必须全部通过才能合并
ruff check src/ tests/         # 零警告
pytest tests/ -v --cov        # 全部通过
python scripts/mcp_smoke_test.py  # 冒烟测试通过
```

---

## 附录：环境配置

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 设置日志级别
export CN_SCRAPER_LOG_LEVEL=DEBUG  # 可选 DEBUG/INFO/WARNING/ERROR

# 运行单个平台的冒烟测试（需要真实 Cookie）
python scripts/platform_health.py --platform taobao
```

---

## 发布流程

PyPI 发布由 GitHub Release 触发，并通过 Trusted Publishing 完成；不要在本地直接运行
`twine upload`。发布脚本需要已安装并登录的 [GitHub CLI](https://cli.github.com/)。

开发期间先把已完成的用户可见变更写入 `CHANGELOG.md` 的 `Unreleased` 小节。
`Planned` 小节只表示未来计划，脚本不会把它归入当前版本。

推荐使用可审阅的两阶段流程：

```bash
# 1. 更新版本号，并把 Unreleased 内容归档为 0.3.0
python scripts/release.py prepare 0.3.0

# 2. 审阅 CHANGELOG.md 和版本 diff，然后执行完整发布
python scripts/release.py publish 0.3.0
```

确认发行说明已经准备好时，也可以一次完成：

```bash
python scripts/release.py release 0.3.0
```

脚本会验证工作区和远端分支、运行与发布工作流一致的检查、构建并安装验证 wheel、
提交和推送版本变更、创建 GitHub Release、等待 Actions 完成，最后通过 PyPI JSON API
确认新版本可见。只有自动化环境才应使用 `--yes` 跳过最终确认；`--skip-checks` 仅用于
明确知道本地环境无法运行检查的情况，GitHub Actions 仍会执行全部发布检查。
