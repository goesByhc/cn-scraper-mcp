<p align="center">
  <img src="assets/cn-scraper-mcp-icon.png" alt="CN Scraper MCP icon" width="220">
</p>

<h1 align="center">CN Scraper MCP</h1>

<p align="center">
  <strong>Let AI agents search Chinese web platforms — Taobao, JD, Xiaohongshu, Zhihu, Weibo, Douyin, and ZSXQ — through one MCP server.</strong>
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green" alt="MCP compatible"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://github.com/goesByhc/cn-scraper-mcp/actions/workflows/ci.yml"><img src="https://github.com/goesByhc/cn-scraper-mcp/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

---

## Why?

Every AI agent can search the web. But Chinese platforms don't welcome bots:

- **Taobao**: TLS fingerprinting + MTOP HMAC-MD5 signing
- **JD.com**: Headless returns 0. `li.gl-item` selectors dead. `p.3.cn` DNS dead.
- **Xiaohongshu**: Datacenter IP → blocked before cookies are checked. Results are JS-signed XHR.
- **Zhihu**: Guest access limited; full content needs cookies.
- **ZSXQ (知识星球)** : Paid-group content behind cookie auth REST API.

**This project distills months of trial and error** — the exact recipes that work in 2026, packaged as an MCP server your AI agent calls with one line:
`taobao_search("儿童学习桌")`

### Security and privacy

`cn-scraper-mcp` runs locally. Cookies, passwords, and browser profiles are not uploaded to a hosted relay:

- Cookies are stored under `~/.cn-scraper-cookies/`; JD uses a local persistent Chrome profile.
- You sign in on the platform's official page. The server does not read or store your password.
- Cookie values are neither logged nor returned to the Agent through MCP results.
- Credentials are sent only to the matching platform when a tool or remote login check needs them.
- The credential-handling code is open source and auditable.

Treat these files like a signed-in browser session: never share or commit them, and protect their local file permissions.

---

## Platform Support

### E-commerce 电商

| Platform | Method | Browser | Rate Limit | Status | Stability |
|----------|--------|---------|------------|--------|-----------|
| **淘宝/Tmall** 🔥 | `curl_cffi` + MTOP | ❌ None | Generous¹ | ✅ Verified | Stable |
| **京东/JD** | Chrome CDP headful | ✅ Required | Moderate | ✅ Verified | May break² |
| **拼多多/PDD** ⚠️ | Chrome CDP + iPhone UA | ✅ Required | 🔴 1 search only¹ | ✅ Verified | Fragile³ |

> ¹ Taobao rate limits are generous but subject to platform changes — not guaranteed "unlimited."
> ² JD relies on DOM selectors (`div[data-sku]`) which may change without notice.
> ³ PDD allows exactly ONE search per browser session. Server enforces engine-level single-use. `anti_content` token requires real browser; cookies expire in ~1 hour.

### Content & Community 内容社区

| Platform | Method | Browser | Rate Limit | Status | Stability |
|----------|--------|---------|------------|--------|-----------|
| **小红书/XHS** | Local Chrome CDP + cookie | ✅ Required | Moderate | ✅ Verified | May break³ |
| **知乎/Zhihu** | REST API v4 | ❌ None | Normal | ✅ Verified | Stable |
| **微博/Weibo** 🔥 | REST API | ❌ None | Normal | ✅ Verified | Stable⁴ |
| **抖音/Douyin** ⚠️ | Chrome CDP | ✅ Required | Strict | ⚠️ Experimental | Fragile⁵ |
| **知识星球/ZSXQ** | REST API v2 | ❌ None | Normal | ✅ Verified | Stable |

> ³ Xiaohongshu blocks datacenter IPs at the network level; only residential IPs work.
> ⁴ Weibo hot list (热搜榜) works **without login** via `weibo.com/ajax/side/hotSearch`.
>    Search requires login cookies (SUB token) via `m.weibo.cn` mobile API.
> ⁵ Douyin needs a signed-in browser and may require the user to complete a slider CAPTCHA. The tool waits for manual verification before continuing.

## Quick Start

### Install

```bash
pip install cn-scraper-mcp
```

Or install the development version from source:

```bash
git clone https://github.com/goesByhc/cn-scraper-mcp.git
cd cn-scraper-mcp
pip install .
```

### Docker

Run cn-scraper-mcp in an isolated container with Chromium pre-installed — no local browser setup needed.

```bash
# Build and run (MCP stdio mode)
docker build -t cn-scraper-mcp .
docker run -i --rm \
  -v ~/.cn-scraper-cookies:/root/.cn-scraper-cookies \
  -v ~/.jd_login_profile:/root/.jd_login_profile \
  cn-scraper-mcp

# Or with docker compose
docker compose build
docker compose run --rm cn-scraper
```

**AI agent integration** (Codex, Claude Code, Cursor):

```toml
# ~/.codex/config.toml
[mcp_servers.cn-scraper]
command = "docker"
args = ["run", "-i", "--rm",
  "-v", "/absolute/path/.cn-scraper-cookies:/root/.cn-scraper-cookies",
  "-v", "/absolute/path/.jd_login_profile:/root/.jd_login_profile",
  "cn-scraper-mcp"]
```

Replace `/absolute/path/` with the real host path; MCP clients launch the process directly and do not expand `~` in arguments.

For Claude Code, Cursor, or Reasonix, use the same Docker command in their `mcpServers` file and replace the host paths below with absolute paths:

```json
{
  "mcpServers": {
    "cn-scraper": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/absolute/path/.cn-scraper-cookies:/root/.cn-scraper-cookies",
        "-v", "/absolute/path/.jd_login_profile:/root/.jd_login_profile",
        "cn-scraper-mcp"
      ]
    }
  }
}
```

> **Note on Chromium**: The Docker image includes Chromium with `--no-sandbox --headless=new` flags. For JD headful mode (if headless detection blocks you), set `XVFB_WRAPPER=1` in the container environment to wrap the server with `xvfb-run`. Datacenter IPs are still blocked by Xiaohongshu — use a residential IP or local Chrome.

### Recommended: guided CDP login

After connecting the MCP server, ask your Agent to call:

```text
guided_login(platform="weibo")
```

The tool opens local Chrome on the platform's official login page. After you scan the QR code or enter your password yourself, it uses CDP to collect the complete cookie set—including HttpOnly cookies—and stores it locally. JD uses a local persistent Chrome profile.

This is the recommended flow: there is no cookie copy-and-paste, required fields are less likely to be missed, and re-login is straightforward when credentials expire. If Chrome is already running with remote debugging and is signed in, use `harvest_cookies(platform="weibo")` instead.

### Run

```bash
cn-scraper-mcp
# or: python -m cn_scraper_mcp.server
```

## MCP Tools

| Tool | Platform | Browser | What it does |
|------|----------|---------|-------------|
| `taobao_search` | 淘宝/Tmall | ❌ | Keyword search → price, sales, shop |
| `jd_search` | 京东 | ✅ | Keyword search → SKU, price, name |
| `pdd_search` | 拼多多 ⚠️ | ✅ | Keyword search → goodsId, price, name (单次!) |
| `pdd_product_detail` | 拼多多 | ✅ | Product detail → price, specs, sold-out status |
| `xiaohongshu_search` | 小红书 | ✅ | Search notes → title, author, likes |
| `xiaohongshu_note` | 小红书 | ✅ | Get note detail → body, tags, comments |
| `zhihu_search` | 知乎 | ❌ | Search → questions, articles |
| `zhihu_hot_list` | 知乎 | ❌ | Trending topics |
| `zhihu_comments` | 知乎 | ❌ | Fetch comments for an answer |
| `weibo_search` | 微博 | ❌ | Search posts |
| `weibo_hot_list` | 微博 | ❌ | Trending topics |
| `weibo_comments` | 微博 | ❌ | Fetch first-page post comments |
| `zsxq_topics` | 知识星球 | ❌ | Fetch group posts → text, comments |
| `check_cookies` | All | ❌ | Diagnose cookie freshness |
| `verify_login` | 知乎/微博 | ❌ | Verify cached login against a read-only remote probe |

---

## MCP Integration

### Codex

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.cn-scraper]
command = "cn-scraper-mcp"
args = []
```

Run `codex mcp list` after saving to check the connection.

### Claude Code / Cursor / Reasonix

These clients support the standard `mcpServers` JSON schema:

- Claude Code: `.mcp.json` in the project root
- Cursor: global `~/.cursor/mcp.json`, or project-local `.cursor/mcp.json`
- Reasonix: `.mcp.json` in the project root

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

Trae's configuration-file location varies by version. Use its MCP settings UI to add a local stdio server named `cn-scraper`, with command `cn-scraper-mcp` and no arguments.

> If the client cannot find the command, run `where cn-scraper-mcp` on Windows or `which cn-scraper-mcp` on macOS/Linux, then use the returned absolute path as `command`.

---

## FAQ

**Q: Is it safe to use?**
The server runs locally and does not use a project-hosted relay. Cookies and browser profiles stay on your machine. Cookie values are not logged or returned to the Agent through MCP, and credentials are sent only to the matching platform domain when needed.

**Q: Does it read or store my password?**
No. You sign in on the platform's official page. After login, CDP stores only the cookies created by the browser.

**Q: Where are credentials stored?**
Cookies are stored under `~/.cn-scraper-cookies/`; JD uses a local persistent Chrome profile. Treat them like a signed-in browser session: never share or commit them.

**Q: What is the easiest way to sign in?**
Call `guided_login("platform")`. It opens Chrome on the official login page, waits for you to sign in, and then saves the cookies locally.

**Q: Is scraping legal?**
Use the project only for learning and research. Large-scale collection may violate platform terms. Do not use it for spam, denial-of-service activity, or commercial bulk scraping.

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [curl_cffi](https://github.com/lexiforest/curl_cffi) — TLS fingerprint impersonation
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [websockets](https://github.com/python-websockets/websockets) — async WebSocket client

---

*Made with ☕ and frustration at Chinese platform anti-bot walls.*
