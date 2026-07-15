<p align="center">
  <img src="assets/cn-scraper-mcp-icon.png" alt="CN Scraper MCP 图标" width="220">
</p>

<h1 align="center">CN Scraper MCP</h1>

<p align="center">
  <strong>让 AI Agent 直接搜索中国互联网——淘宝、京东、小红书、知乎、微博、知识星球……不再被反爬墙挡住。</strong>
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green" alt="MCP compatible"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://github.com/goesByhc/cn-scraper-mcp/actions/workflows/ci.yml"><img src="https://github.com/goesByhc/cn-scraper-mcp/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

---

## 这是什么

每个 AI Agent（Codex、Claude Code、Cursor、Trae）都能搜网页。但中文平台不欢迎机器人：

- **淘宝**：TLS 指纹检测 + MTOP HMAC-MD5 签名，curl 直接弹滑块
- **京东**：无头浏览器返回 0 结果。旧选择器 `li.gl-item` 全死，`p.3.cn` DNS 已消失
- **小红书**：数据中心 IP 直接封（cookie 都来不及检查），搜索结果靠 JS 签名 XHR 加载
- **知乎**：游客搜索已关闭，全部 API 需要登录态
- **拼多多**：`anti_content` token 机制，一个浏览器会话只能搜一次
- **微博**：搜索 API 需要登录态（SUB token），热搜游客即可访问
- **抖音**：需要 CDP 浏览器轮询 + 手动过验证码
- **知识星球**：付费社群，内容藏在 cookie 认证的 REST API 后面

**这个项目就是把踩了好几个月的坑打包成一个 MCP Server**——你的 Agent 一句话就能搜：`taobao_search("儿童学习桌")`。

---

## 平台支持

### 电商

| 平台 | 方式 | 需要浏览器 | 限制 | 稳定性 |
|------|------|-----------|------|--------|
| **淘宝/Tmall** | `curl_cffi` + MTOP 签名 | ❌ | 宽松¹ | ✅ 稳定 |
| **京东/JD** | Chrome CDP headful | ✅ | 中等 | ✅ 稳定² |
| **拼多多/PDD** ⚠️ | Chrome CDP + iPhone UA | ✅ | 每会话首次搜索³ | ⚠️ 已支持（受限） |

> ¹ 淘宝无硬性限流，但平台可能随时收紧，不建议高频批量抓取。
> ² 京东依赖 `div[data-sku]` 选择器，平台改版可能导致适配失效。通过 `guided_login("jd")` 可自动初始化持久化 Profile。
> ³ 拼多多搜索已经接入，但平台通常只放行每个浏览器会话的第一次搜索，适合低频、单次查询，不建议批量使用。

### 内容社区

| 平台 | 方式 | 登录要求 | 其他限制 | 支持状态 |
|------|------|---------|---------|---------|
| **小红书/XHS** | 本地 Chrome CDP | 需要登录 | 住宅网络更稳定⁴ | ✅ 已支持 |
| **知乎/Zhihu** | REST API v4 | 需要登录 | 正常 | ✅ 已支持 |
| **知识星球/ZSXQ** | REST API v2 | 需要登录 | 仅能访问账号已有权限的星球 | ✅ 已支持 |
| **微博/Weibo** | REST API | 热搜免登录；搜索需登录 | 正常 | ✅ 已支持 |
| **抖音/Douyin** | Chrome CDP + 验证码轮询 | 需要登录 | 验证码可能需要手动完成⁵ | ✅ 已支持（实验性） |

> ⁴ 小红书只允许住宅 IP——云浏览器/数据中心 IP 直接封。必须用本地 Chrome。
> 登录是这些平台的正常使用条件，不代表功能不可用。项目使用用户自己的登录态，不绕过账号权限。
> ⁵ 抖音登录后可以正常搜索。遇到滑块时，工具会等待最多 120 秒；手动完成验证后会自动继续抓取。`guided_login("douyin")` 可引导登录。

### API/选择器生死簿

| API / 选择器 | 状态 | 说明 |
|-------------|------|------|
| `mtop.taobao.wsearch.appsearch` → `itemsArray` | ✅ 活着 | 正确字段；`data.result` 永远为 `[]` |
| `h5api.m.taobao.com` h5search | ❌ 已死 | 返回 502 |
| `p.3.cn/prices/mgets` | ❌ 已死 | DNS 不再解析 |
| `club.jd.com/comment/productPageComments` | ❌ 被封 | 返回 "系统繁忙"（12 字节） |
| `li.gl-item` / `#J_goodsList` | ❌ 已死 | 京东改了布局 |
| `div[data-sku]` | ✅ 当前 | 京东现行商品选择器 |
| XHS `section.note-item` | ✅ | 搜索结果 DOM |
| XHS `__INITIAL_STATE__.note.noteDetailMap` | ✅ | 笔记正文 + 评论 |
| PDD `mobile.yangkeduo.com/search_result.html` | ⚠️ | 仅首次搜索有效 |
| ZSXQ `api.zsxq.com/v2/groups/{id}/topics` | ✅ | Cookie 认证，免浏览器 |
| 知乎 `api/v4/search_v3` | 🔑 | 需 z_c0 + d_c0 |
| 微博 `ajax/side/hotSearch` | ✅ | 游客可访问（需 Referer + X-Requested-With 头） |
| 微博 `ajax/statuses/search` | 🔑 | 需 SUB cookie |
| 微博 `ajax/statuses/mymblog` | 🔑 | 用户时间线，需 SUB cookie |
| 抖音 `douyin.com/search/{kw}` | ✅ | 登录后通过 CDP 搜索；验证码出现时允许手动完成 |

---

## 快速开始

### 安装

> ⚠️ **尚未发布到 PyPI**，请从源码安装：

```bash
git clone https://github.com/goesByhc/cn-scraper-mcp.git
cd cn-scraper-mcp
pip install -e ".[dev]"
```

### Cookie 配置（一次性）

需要登录的平台使用用户自己的 Cookie 或持久化浏览器 Profile，默认存放在 `~/.cn-scraper-cookies/`：

```bash
mkdir -p ~/.cn-scraper-cookies
```

| 平台 | Cookie 文件 | 获取方式 |
|------|------------|---------|
| 淘宝 | `taobao.json` | 登录 `m.taobao.com` → DevTools → Application → Cookies → 导出 JSON。需要 `_m_h5_tk`、`_tb_token_`、`cookie2`、`cna`、`unb`，以及 HttpOnly 的 `sgcookie`/`tfstk`/`isg`（用 CDP 收割） |
| 京东 | `~/.jd_login_profile/` | 持久 Chrome Profile——在 `jd.com` 登录一次，Profile 自动记住 |
| 小红书 | `xiaohongshu.json` | 从 `xiaohongshu.com` DevTools 导出。需要 `web_session`、`a1`、`webId`、`gid` |
| 知乎 | `zhihu.json` | 从 `zhihu.com` DevTools 导出。需要 `z_c0`、`d_c0` |
| 知识星球 | `zsxq.json` | 从 `zsxq.com` DevTools 导出。需要 `zsxq_access_token` |
| 拼多多 | `pdd.json` | 从 `yangkeduo.com` DevTools 导出。需要 `PDDAccessToken`、`pdd_user_id`。⚠️ Token 约 1 小时过期 |

> ⚠️ **淘宝 HttpOnly Cookie**：`sgcookie`、`tfstk`、`isg`、`havana_lgc2_0` 是 HttpOnly 的——DevTools 手动复制拿不到。用 CDP `Network.getAllCookies` 从已登录 Chrome 收割完整集合，或用内置的 `harvest_cookies` MCP 工具。

### 启动

```bash
cn-scraper-mcp
# 或: python -m cn_scraper_mcp.server
```

### Docker

容器内预装 Chromium，无需本地浏览器：

```bash
docker build -t cn-scraper-mcp .
docker run -i --rm \
  -v ~/.cn-scraper-cookies:/root/.cn-scraper-cookies \
  -v ~/.jd_login_profile:/root/.jd_login_profile \
  cn-scraper-mcp
```

Agent 集成配置：

```toml
# Codex ~/.codex/config.toml
[mcp_servers.cn-scraper]
type = "stdio"
command = "docker"
args = ["run", "-i", "--rm",
  "-v", "~/.cn-scraper-cookies:/root/.cn-scraper-cookies",
  "-v", "~/.jd_login_profile:/root/.jd_login_profile",
  "cn-scraper-mcp"]
```

> Docker 镜像内置 Chromium + `--no-sandbox`。京东 headful 模式如需 Xvfb，设置环境变量 `XVFB_WRAPPER=1`。小红书仍需住宅 IP——数据中心 IP 会被封。

---

## MCP 工具一览

### 电商搜索

| 工具 | 说明 |
|------|------|
| `taobao_search` | 淘宝/天猫关键词搜索 → 价格、销量、店铺 |
| `jd_search` | 京东关键词搜索 → SKU、价格、商品名 |
| `pdd_search` | 拼多多搜索 → 仅首次有效 |
| `pdd_product_detail` | 拼多多商品详情 → 不限次数 |
| `compare_prices` | 跨平台比价 → 淘宝 vs 京东 vs 拼多多，最低价/中位数/价格区间 |

### 内容社区

| 工具 | 说明 |
|------|------|
| `xiaohongshu_search` | 小红书笔记搜索 → 标题、作者、点赞 |
| `xiaohongshu_note` | 小红书笔记详情 → 正文、标签、评论 |
| `zhihu_search` | 知乎搜索 → 问题、文章（需登录） |
| `zhihu_hot_list` | 知乎热榜（需登录） |
| `weibo_search` | 微博搜索 → 微博帖子内容（需登录） |
| `weibo_hot_list` | 微博热搜榜（无需登录） |
| `weibo_user_timeline` | 微博用户时间线（需登录） |
| `douyin_search` | 抖音搜索 → 登录后通过 CDP 搜索，验证码出现时等待手动完成（实验性） |
| `douyin_hot_list` | 抖音热搜榜（需登录 cookie） |
| `zsxq_topics` | 知识星球付费社群帖子 |

### Cookie 管理

| 工具 | 说明 |
|------|------|
| `check_cookies` | 检查所有平台 Cookie 状态 |
| `diagnose` | 环境诊断——依赖版本、浏览器、CDP 端口 |
| `harvest_cookies` | CDP 自动收割 Cookie（包括 HttpOnly） |
| `guided_login` | 引导登录——自动打开浏览器 → 你扫码 → 登录后自动收割 Cookie |

---

## Python API

```python
from cn_scraper_mcp.engines import (
    TaobaoEngine, JDEngine, PDDEngine,
    XiaohongshuEngine, ZhihuEngine, ZsxqEngine, WeiboEngine, DouyinEngine,
)

# 淘宝 —— 纯脚本，免浏览器
tb = TaobaoEngine()
r = tb.search("华为mate70", limit=5)
print(r["items"][0]["price"])  # "3099.00"

# 京东 —— 需要 Chrome headful
jd = JDEngine()
r = jd.search("京东京造沐光")

# 拼多多 —— ⚠️ 仅一次搜索
pdd = PDDEngine()
r = pdd.search("儿童学习桌", limit=5)
detail = pdd.product_detail("123456789")  # 不限次数

# 小红书 —— Obscura 优先，Chrome 兜底
xhs = XiaohongshuEngine()
notes = xhs.search("测评")
detail = xhs.get_note(notes["items"][0]["noteId"])

# 知乎 —— 需要登录 Cookie
zh = ZhihuEngine()
r = zh.search("半导体")
hot = zh.hot_list()

# 知识星球 —— REST API
zs = ZsxqEngine()
topics = zs.get_topics("28888555451", count=5)

# 微博 —— 热搜免登，搜索需 Cookie
wb = WeiboEngine()
hot = wb.hot_list()
r = wb.search("热搜话题")
timeline = wb.user_timeline("2803301701")  # 人民日报 UID

# 抖音 —— CDP 浏览器，需登录
dy = DouyinEngine()
dy.ensure_chrome()
r = dy.search("华为")
hot = dy.hot_list()

# 跨平台比价
from cn_scraper_mcp.compare import compare_prices
result = compare_prices("华为mate70", platforms=["taobao", "jd"])
print(result["best_deal"])  # 最低价商品
```

---

## Agent 集成

### Codex

`~/.codex/config.toml`：

```toml
[mcp_servers.cn-scraper]
type = "stdio"
command = "cn-scraper-mcp"
args = []
autoApprove = ["*"]
```

### Claude Code / Cursor / Trae

```json
{
  "mcp": {
    "servers": {
      "cn-scraper": {
        "command": "cn-scraper-mcp",
        "args": []
      }
    }
  }
}
```

### Reasonix

```toml
[[plugins]]
name = "cn-scraper"
command = "cn-scraper-mcp"
args = []
```

---

## 架构

```
AI Agent (Codex / Claude / Cursor / Trae / Reasonix)
    │ MCP stdio
    ▼
┌─────────────────────────────────────────────────────┐
│                    server.py (FastMCP)               │
│  23 个工具 · 参数校验 · 统一错误模型 · stderr 日志   │
├─────────────────────────────────────────────────────┤
│  引擎层                                              │
│  taobao.py ──→ curl_cffi + MTOP ──→ h5api.m.taobao   │
│  jd.py     ──→ Chrome CDP        ──→ search.jd.com   │
│  pdd.py    ──→ Chrome CDP + iUA  ──→ mobile.yangkeduo │
│  xiaohongshu.py ─→ Chrome CDP    ──→ xiaohongshu.com  │
│  zhihu.py  ──→ REST API v4       ──→ zhihu.com       │
│  weibo.py  ──→ REST API          ──→ weibo.com       │
│  zsxq.py   ──→ REST API v2       ──→ api.zsxq.com    │
│  douyin.py ──→ Chrome CDP + 验证码轮询 ─→ douyin.com │
├─────────────────────────────────────────────────────┤
│  基础设施                                            │
│  auth.py     — Cookie 管理与字段校验                 │
│  http.py     — 超时/重试/退避/限速                   │
│  cdp.py      — Chrome CDP 驱动 + BrowserLock + 进程管理 │
│  logging.py  — stderr 脱敏日志 + 错误记录            │
│  errors.py   — 异常类型 + 统一 error_response()      │
│  compare.py  — 跨平台比价聚合层                      │
│  cookie_harvest.py — CDP Cookie 收割 + 引导登录      │
└─────────────────────────────────────────────────────┘
```

---

## 测试

全量单元测试均使用 Mock（不需要真实网络、Chrome 或 Cookie），并覆盖全部 23 个 MCP 工具：

```bash
pytest tests/ -v         # 全量单元测试
ruff check src/ tests/   # 零警告
pytest tests/ --cov      # 覆盖率报告
python scripts/mcp_smoke_test.py  # 本地 MCP 协议冒烟测试
```

GitHub Actions CI：Ubuntu 上的 Python 3.11 / 3.12 / 3.13 矩阵，自动执行单元测试、Ruff、MCP 协议冒烟测试、Wheel 构建与安装验证。

---

## 常见问题

**Q: 为什么不用 Playwright / Selenium？**  
更重更慢，且很多 AI Agent 跑不了。`curl_cffi` + 原生 CDP WebSocket = 最少依赖。

**Q: 淘宝返回 `Session过期`。**  
`_m_h5_tk` cookie 过期了。从浏览器重新收割一次。

**Q: 京东返回 0 结果。**  
三种可能：(1) Chrome 开了 headless → 换成 headful。(2) Profile 没登录。(3) Cookie 注入缺少真实登录态 → 用持久 Profile。

**Q: 小红书搜出 "IP存在风险"。**  
你用了云浏览器/数据中心 IP。小红书在 IP 层面就封了——换成**本地 Chrome** 或住宅 IP。

**Q: 拼多多第一次能搜、第二次就 "系统繁忙"。**  
这是平台限制，不是 bug。每个浏览器会话只放行第一次搜索。重启 MCP Server 可获得新会话。

**Q: 抖音搜索卡在验证码。**
抖音需要手动过滑块验证码。`douyin_search` 检测到验证码后会持续等待（最多 120s），你过完验证码它会自动继续抓取。

**Q: 怎么初始化 Cookie 最方便？**
用 `guided_login("平台名")` 工具。它会自动打开 Chrome → 导航到登录页 → 等你扫码/输密码 → 登录后自动收割 Cookie 并保存。

**Q: 合法吗？**  
仅用于**学习和研究目的**。批量抓取可能违反平台服务条款。风险自负。切勿用于垃圾信息、DDoS 或商业级大规模抓取。

---

## 路线图

完整的分阶段计划、任务依赖和验收标准见 [ROADMAP.md](ROADMAP.md)。

当前进度：v0.1.0 发布准备已完成，下一阶段重点是统一错误分类、平台健康探针和低频真实 canary。

- [x] 淘宝/Tmall（curl_cffi + MTOP）
- [x] 京东（Chrome CDP headful）
- [x] 拼多多（CDP + iPhone UA + 单次限制文档化）
- [x] 小红书（本地 Chrome CDP）
- [x] 知乎（REST API，已适配登录要求）
- [x] 知识星球（REST API v2）
- [x] 微博（热搜 + 搜索 + 用户时间线）
- [x] 抖音（CDP 浏览器 + 验证码轮询，实验性）
- [x] 跨平台比价工具
- [x] CDP Cookie 自动收割（含 HttpOnly）
- [x] 引导登录（guided_login）
- [x] Docker 支持
- [x] GitHub Actions CI
- [x] 统一错误模型 + 参数校验
- [x] 并发隔离（BrowserLock）
- [x] 平台健康检查脚本
- [x] 全部 23 个 MCP 工具测试覆盖
- [ ] 发布到 PyPI
- [ ] Cookie 加密存储
- [ ] 请求指标、缓存、审计

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)。

## 致谢

- [curl_cffi](https://github.com/lexiforest/curl_cffi) — TLS 指纹伪装
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP Server 框架
- [websockets](https://github.com/python-websockets/websockets) — 异步 WebSocket

---

*Made with ☕ and months of frustration at Chinese platform anti-bot walls.*
