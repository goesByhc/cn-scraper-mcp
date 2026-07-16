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

### 安全与隐私

`cn-scraper-mcp` 在你的电脑上本地运行，不需要把 Cookie、账号密码或浏览器 Profile 上传到任何中转服务器：

- Cookie 默认保存在 `~/.cn-scraper-cookies/`，京东登录态保存在本地 Chrome Profile。
- 登录过程直接发生在平台官方页面，软件不会读取或保存你的账号密码。
- Cookie 值不会写入日志，也不会通过 MCP 工具结果返回给 Agent；工具只返回状态、字段名和本地路径等非敏感信息。
- 发起抓取或在线登录验证时，凭证只会发送给对应平台域名。
- 代码完全开源，所有凭证处理流程都可以审查。

建议仍像保护浏览器登录态一样保护本机账号：不要分享 Cookie 文件，不要把凭证提交到 Git，并限制本地文件的访问权限。

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
| **知乎/Zhihu** | REST API v4 | ❌ | 正常 | ✅ 稳定 |
| **知识星球/ZSXQ** | REST API v2 | ❌ | 正常 | ✅ 稳定 |
| **微博/Weibo** | REST API | ❌ | 正常 | ✅ 稳定 |
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

### 推荐：CDP 自动登录并保存 Cookie

安装并连接 MCP 后，直接让 Agent 调用：

```text
guided_login(platform="weibo")
```

它会打开本地 Chrome 并进入平台官方登录页。你自己扫码或输入密码后，工具通过 CDP 自动读取完整 Cookie（包括 JavaScript 无法读取的 HttpOnly Cookie），再保存到本机 `~/.cn-scraper-cookies/`。京东则保存到本地持久化 Chrome Profile。

这是推荐方式，因为它不会要求你复制 Cookie，不容易漏掉关键字段，也更适合 Cookie 过期后的重新登录。可用平台名包括 `taobao`、`jd`、`xiaohongshu`、`zhihu`、`weibo`、`zsxq`、`douyin` 和 `pdd`。

已有通过远程调试端口启动且登录完成的 Chrome 时，也可以调用：

```text
harvest_cookies(platform="weibo")
```

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
command = "docker"
args = ["run", "-i", "--rm",
  "-v", "/本机绝对路径/.cn-scraper-cookies:/root/.cn-scraper-cookies",
  "-v", "/本机绝对路径/.jd_login_profile:/root/.jd_login_profile",
  "cn-scraper-mcp"]
```

请把 `/本机绝对路径/` 替换为真实路径；MCP 客户端直接启动进程时不会替你展开 `~`。

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
| `zhihu_search` | 知乎搜索 → 问题、文章 |
| `zhihu_hot_list` | 知乎热榜 |
| `zhihu_comments` | 知乎回答评论 |
| `weibo_search` | 微博搜索 → 微博帖子内容 |
| `weibo_hot_list` | 微博热搜榜 |
| `weibo_user_timeline` | 微博用户时间线 |
| `weibo_comments` | 微博帖子首屏评论 |
| `douyin_search` | 抖音搜索 → CDP 浏览器 + 验证码轮询（⚠️ 实验性） |
| `douyin_hot_list` | 抖音热搜榜 |
| `zsxq_topics` | 知识星球付费社群帖子 |

### Cookie 管理

| 工具 | 说明 |
|------|------|
| `check_cookies` | 检查所有平台 Cookie 状态 |
| `verify_login` | 在线验证知乎/微博登录态；其他平台明确返回 unsupported |
| `diagnose` | 环境诊断——依赖版本、浏览器、CDP 端口 |
| `harvest_cookies` | CDP 自动收割 Cookie（包括 HttpOnly） |
| `guided_login` | 引导登录——自动打开浏览器 → 你扫码 → 登录后自动收割 Cookie |

## MCP 客户端配置

### Codex

`~/.codex/config.toml`：

```toml
[mcp_servers.cn-scraper]
command = "cn-scraper-mcp"
args = []
```

保存后可用 `codex mcp list` 检查连接状态。

### Claude Code / Cursor / Reasonix

这三个客户端都支持标准的 `mcpServers` JSON：

- Claude Code：项目根目录 `.mcp.json`
- Cursor：全局 `~/.cursor/mcp.json`，或项目目录 `.cursor/mcp.json`
- Reasonix：项目根目录 `.mcp.json`

```json
{
  "mcpServers": {
    "cn-scraper": {
      "command": "cn-scraper-mcp",
      "args": []
    }
  }
}
```

### Trae

Trae 不同版本的配置文件位置可能不同。建议在设置中的 MCP 管理界面添加本地 stdio Server：名称填写 `cn-scraper`，命令填写 `cn-scraper-mcp`，参数留空。

> 如果客户端提示找不到命令，先用 `where cn-scraper-mcp`（Windows）或 `which cn-scraper-mcp`（macOS/Linux）找到完整路径，再把 `command` 替换为该路径。

---

## 更多文档

- [架构设计](https://github.com/goesByhc/cn-scraper-mcp/blob/master/docs/architecture.md)：职责边界、平台契约和 Agent 开发守则
- [开发规范](https://github.com/goesByhc/cn-scraper-mcp/blob/master/CONTRIBUTING.md)：环境、编码、测试和 Review 要求

---

## 常见问题

**Q: 使用这个软件安全吗？**
软件在你的电脑上本地运行，不经过项目方的中转服务器。Cookie 和浏览器 Profile 保存在本机，Cookie 值不会写入日志或通过 MCP 返回给 Agent；需要访问平台时，凭证只发送给对应的平台域名。

**Q: 软件会读取或保存账号密码吗？**
不会。登录发生在平台官方页面，由你自己扫码或输入密码；工具只在登录完成后通过 CDP 保存浏览器产生的 Cookie。

**Q: Cookie 保存在什么地方？**
Cookie 默认保存在 `~/.cn-scraper-cookies/`，京东使用本地持久化 Chrome Profile。请像保护已登录浏览器一样保护这些文件，不要分享或提交到 Git。

**Q: 怎么初始化 Cookie 最方便？**
用 `guided_login("平台名")` 工具。它会自动打开 Chrome → 导航到登录页 → 等你扫码/输密码 → 登录后自动收割 Cookie 并保存。

**Q: 合法吗？**
仅用于**学习和研究目的**。批量抓取可能违反平台服务条款。风险自负。切勿用于垃圾信息、DDoS 或商业级大规模抓取。

---

## 许可证

MIT — 详见 [LICENSE](https://github.com/goesByhc/cn-scraper-mcp/blob/master/LICENSE)。

## 支持项目

如果这个项目帮你节省了时间，可以请作者喝杯咖啡：

<p align="center">
  <img src="assets/wechat-pay.jpg" alt="微信赞赏码" width="240">
</p>

## 致谢

- [curl_cffi](https://github.com/lexiforest/curl_cffi) — TLS 指纹伪装
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP Server 框架
- [websockets](https://github.com/python-websockets/websockets) — 异步 WebSocket

---

*Made with ☕ and months of frustration at Chinese platform anti-bot walls.*
