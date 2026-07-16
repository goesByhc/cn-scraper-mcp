<p align="center">
  <img src="https://raw.githubusercontent.com/goesByhc/cn-scraper-mcp/master/assets/cn-scraper-mcp-icon.png" alt="CN Scraper MCP 图标" width="220">
</p>

<h1 align="center">CN Scraper MCP</h1>

<p align="center">
  <strong>让 AI Agent 直接搜索中国互联网——淘宝、京东、小红书、知乎、微博、知识星球……不再被反爬墙挡住。</strong>
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green" alt="MCP compatible"></a>
  <a href="https://github.com/goesByhc/cn-scraper-mcp/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://github.com/goesByhc/cn-scraper-mcp/actions/workflows/ci.yml"><img src="https://github.com/goesByhc/cn-scraper-mcp/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

---

## 这是什么

每个 AI Agent（Codex、Claude Code、Cursor、Trae）都能搜网页，但中文平台通常需要登录态、浏览器环境或平台专用参数：

- **淘宝**：需要浏览器一致的网络指纹和登录 Cookie
- **京东**：依赖已登录的本地浏览器环境
- **小红书**：需要住宅 IP、本地浏览器和搜索结果中的访问参数
- **知乎**：游客搜索已关闭，全部 API 需要登录态
- **拼多多**：平台限制严格，目前不推荐使用
- **微博**：搜索 API 需要登录态（SUB token），热搜游客即可访问
- **抖音**：需要浏览器登录并可能人工处理验证码
- **知识星球**：付费社群，内容藏在 cookie 认证的 REST API 后面

**这个项目就是把踩了好几个月的坑打包成一个 MCP Server**——你的 Agent 一句话就能搜：`taobao_search("儿童学习桌")`。

---

## 平台支持

### 电商

| 平台 | 方式 | 需要浏览器 | 限制 | 稳定性 |
|------|------|-----------|------|--------|
| **淘宝/Tmall** | `curl_cffi` + MTOP 签名 | ❌ | 宽松¹ | ✅ 稳定 |
| **京东/JD** | Chrome CDP headful | ✅ | 中等 | ✅ 稳定² |
| **拼多多/PDD** ❌ | — | — | — | ❌ 不可用³ |

> ¹ 淘宝无硬性限流，但平台可能随时收紧，不建议高频批量抓取。
> ² 京东依赖平台当前页面结构，改版后可能需要适配。通过 `guided_login("jd")` 可自动初始化持久化 Profile。
> ³ 拼多多每个浏览器会话仅放行第一次搜索，之后永久"系统繁忙"。单次搜索结果零实用价值，引擎代码保留但不推荐使用。

### 内容社区

| 平台 | 方式 | 需要浏览器 | 限制 | 稳定性 |
|------|------|-----------|------|--------|
| **小红书/XHS** | 本地 Chrome CDP + cookie | ✅ | 中等⁴ | ✅ 稳定 |
| **知乎/Zhihu** | REST API v4 | 🔑 需登录 | 正常 | ✅ 稳定 |
| **知识星球/ZSXQ** | REST API v2 | ❌ | 正常 | ✅ 稳定 |
| **微博/Weibo** | REST API | ❌ (热搜) / 🔑 (搜索) | 正常 | ✅ 稳定 |
| **抖音/Douyin** ⚠️ | Chrome CDP + 验证码轮询 | ✅ | 实验性⁵ | ⚠️ 实验性 |

> ⁴ 小红书只允许住宅 IP——云浏览器/数据中心 IP 直接封。必须用本地 Chrome。
> ⁵ 抖音搜索需要登录态 + 手动过滑块验证码。支持 120s 等待用户手动验证，通过后自动抓取。`guided_login("douyin")` 可引导登录。

## 快速开始

### 安装

```bash
pip install cn-scraper-mcp
```

也可以从源码安装开发版本：

```bash
git clone https://github.com/goesByhc/cn-scraper-mcp.git
cd cn-scraper-mcp
pip install .
```

### Cookie 配置（一次性）

每个平台需要已登录浏览器的 Cookie。存放在 `~/.cn-scraper-cookies/`：

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

### 内容社区

| 工具 | 说明 |
|------|------|
| `xiaohongshu_search` | 小红书笔记搜索 → 标题、作者、点赞、`noteId`、`xsec_token` |
| `xiaohongshu_note` | 小红书笔记详情 → 标题、正文、作者、标签、互动数、发布时间 |
| `xiaohongshu_comments` | 小红书笔记首屏评论 → 评论内容、用户、点赞、时间（需要 `noteId` + `xsec_token`） |
| `zhihu_search` | 知乎搜索 → 问题、文章（需登录） |
| `zhihu_hot_list` | 知乎热榜（需登录） |
| `weibo_search` | 微博搜索 → 微博帖子内容（需登录） |
| `weibo_hot_list` | 微博热搜榜（无需登录） |
| `weibo_user_timeline` | 微博用户时间线（需登录） |
| `douyin_search` | 抖音搜索 → CDP 浏览器 + 验证码轮询（⚠️ 实验性） |
| `douyin_hot_list` | 抖音热搜榜（需登录 cookie） |
| `zsxq_topics` | 知识星球付费社群帖子 |

### Cookie 管理

| 工具 | 说明 |
|------|------|
| `check_cookies` | 检查所有平台 Cookie 状态 |
| `diagnose` | 环境诊断——依赖版本、浏览器、CDP 端口 |
| `harvest_cookies` | CDP 自动收割 Cookie（包括 HttpOnly） |
| `guided_login` | 引导登录——自动打开浏览器 → 你扫码 → 登录后自动收割 Cookie |

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
item = notes["items"][0]
detail = xhs.get_note(item["noteId"])
comments = xhs.get_comments(item["noteId"], xsec_token=item["xsec_token"])

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
```

---

## MCP 客户端配置

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

## 更多文档

- [架构设计](https://github.com/goesByhc/cn-scraper-mcp/blob/master/docs/architecture.md)：职责边界、平台契约和 Agent 开发守则
- [开发规范](https://github.com/goesByhc/cn-scraper-mcp/blob/master/CONTRIBUTING.md)：环境、编码、测试和 Review 要求

---

## 常见问题

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

## 许可证

MIT — 详见 [LICENSE](https://github.com/goesByhc/cn-scraper-mcp/blob/master/LICENSE)。

## 致谢

- [curl_cffi](https://github.com/lexiforest/curl_cffi) — TLS 指纹伪装
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP Server 框架
- [websockets](https://github.com/python-websockets/websockets) — 异步 WebSocket

---

*Made with ☕ and months of frustration at Chinese platform anti-bot walls.*
