# 🔥 CN Scraper MCP

**Let AI agents search Chinese e-commerce — Taobao, JD, Pinduoduo — without getting blocked.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-green)](https://modelcontextprotocol.io)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Why?

Every AI agent (Codex, Claude Code, Cursor, Trae) can search the web. But Taobao, JD, and Pinduoduo don't welcome bots:

- **Taobao**: TLS fingerprinting + MTOP HMAC-MD5 signing required
- **JD.com**: Headless returns 0 results. Old `li.gl-item` selectors are dead. `p.3.cn` DNS is dead.
- **Pinduoduo**: `anti_content` token per session. Only 1 search allowed before "系统繁忙".

**This project is the distillation of months of trial and error** — the exact recipes that work in 2026, packaged as an MCP server your AI agent can call with a single line:
`taobao_search("儿童学习桌")`

---

## Quick Start

### Install

```bash
pip install cn-scraper-mcp
```

Or from source:

```bash
git clone https://github.com/YOUR_USER/cn-scraper-mcp.git
cd cn-scraper-mcp
pip install -e .
```

### Cookie Setup (one-time)

Each platform requires cookies from a **logged-in browser session**. Store them in `~/.ecom-cookies/`:

```bash
mkdir -p ~/.ecom-cookies
```

| Platform | Cookie file | How to get cookies |
|----------|------------|--------------------|
| 淘宝 | `~/.ecom-cookies/taobao.json` | Log into `m.taobao.com`, export cookies as JSON (DevTools → Application → Cookies → export all as JSON) |
| 京东 | `~/.jd_login_profile/` | Launch Chrome with `--remote-debugging-port=9247 --user-data-dir=~/.jd_login_profile`, log into `jd.com` once. Profile persists. |
| 拼多多 | `~/.ecom-cookies/pdd.json` | Same as Taobao; mobile `yangkeduo.com` cookies required |

> ⚠️ **Required Taobao cookies**: `_m_h5_tk`, `_m_h5_tk_enc`, `_tb_token_`, `cookie2`, `cna`, `unb`, `_nk_`, `cookie17`, plus fingerprint cookies (`isg`, `tfstk`). Missing httponly cookies → use CDP harvest, not copy-paste.

### Run

```bash
# Direct CLI usage
python -m cn_scraper_mcp.server

# Or if installed:
cn-scraper-mcp
```

### Python API

```python
from cn_scraper_mcp.engines import TaobaoEngine, JDEngine

# Taobao — pure script, no browser
tb = TaobaoEngine(cookies_path="~/.ecom-cookies/taobao.json")
results = tb.search("华为mate70", limit=5)
print(results["items"][0]["price"])  # "3099.00"

# JD — requires headful Chrome with login
jd = JDEngine(profile_dir="~/.jd_login_profile")
results = jd.search("京东京造沐光", limit=5)
```

---

## Platform Support

| Platform | Method | Browser required | Rate limit | Status |
|----------|--------|-----------------|------------|--------|
| **淘宝** 🔥 | `curl_cffi` + MTOP signing | ❌ None | **Unlimited** | ✅ Tested |
| **京东** | Chrome CDP (headful) | ✅ Yes | Moderate | ✅ Tested (fresh login) |
| **拼多多** | Chrome CDP + anti_content | ✅ Yes | **1 per session** | ⚠️ Experimental |

### What works vs. what's dead

| API / Selector | Status | Notes |
|---------------|--------|-------|
| `mtop.taobao.wsearch.appsearch` → `itemsArray` | ✅ | Correct field; `data.result` is always `[]` |
| `h5api.m.taobao.com` h5search | ❌ DEAD | Returns 502 |
| `p.3.cn/prices/mgets` | ❌ DEAD | DNS no longer resolves |
| `club.jd.com/comment/productPageComments` | ❌ GATED | Returns "系统繁忙" (12 bytes) |
| `li.gl-item` / `#J_goodsList` | ❌ DEAD | JD changed layout |
| `div[data-sku]` | ✅ | Current JD product selector |
| `curl_cffi` impersonate=chrome | ✅ | Bypasses TLS fingerprint slide-verification |

---

## MCP Integration

### Codex (OpenAI)

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.ecom-scraper]
type = "stdio"
command = "python"
args = ["-m", "cn_scraper_mcp.server"]
autoApprove = ["taobao_search", "jd_search", "check_cookies"]
```

### Claude Code / Cursor / Trae

Add to settings:

```json
{
  "mcp": {
    "servers": {
      "ecom-scraper": {
        "command": "python",
        "args": ["-m", "cn_scraper_mcp.server"]
      }
    }
  }
}
```

### Reasonix

Add to `config.toml`:

```toml
[[plugins]]
name = "ecom-scraper"
command = "python"
args = ["-m", "cn_scraper_mcp.server"]
```

Then restart the agent — `taobao_search`, `jd_search`, and `check_cookies` tools will appear.

---

## Architecture

```
AI Agent                          This project
(Codex / Claude / Cursor)         ┌─────────────────────┐
    │ MCP tool call               │  server.py (FastMCP) │
    │ taobao_search("华为")  ───→  │   ├─ taobao.py       │ → curl_cffi + MTOP → Taobao API
    ▼                             │   ├─ jd.py           │ → Chrome CDP       → JD search
ecom-scraper MCP server           │   ├─ cdp.py          │ → raw websockets   → Chrome
    │ stdio transport              │   └─ cookie_manager  │ → check/refresh
    ▼                             └─────────────────────┘
~/.ecom-cookies/                    ~/.agent-browser/
(JSON cookie files)                 (Chromium for CDP)
```

---

## FAQ

**Q: Why not Playwright / Selenium?**
They're heavier, slower, and many agents can't run them. `curl_cffi` + raw CDP websockets = minimal dependencies.

**Q: My Taobao search returns `Session过期`.**
Your `_m_h5_tk` cookie expired. Re-export cookies from a fresh browser session.

**Q: JD search returns 0 results.**
3 possibilities: (1) Chrome is headless → switch to headful. (2) Profile not logged in → open JD in the launched Chrome, log in once. (3) Cookie injection without `pt_key`/`pt_pin` → use persistent profile, not cookie injection.

**Q: Pinduoduo "系统繁忙" after first search.**
This is per-session `anti_content` rate limit. For bulk search, use product links instead of keyword search.

**Q: Is this legal?**
This project is for **educational and research purposes only**. Scraping e-commerce sites may violate their Terms of Service. Use at your own risk. Never use for spam, DDoS, or commercial scraping at scale.

---

## Roadmap

- [ ] Pinduoduo MCP tool (anti_content session management)
- [ ] Xiaohongshu (小红书) search via CDP
- [ ] Cookie harvest automation (CDP Network.getAllCookies)
- [ ] Cross-platform price comparison tool
- [ ] Docker support (containerized Chrome)
- [ ] PyPI package

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built on the shoulders of:
- [curl_cffi](https://github.com/lexiforest/curl_cffi) — TLS fingerprint impersonation
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [websockets](https://github.com/python-websockets/websockets) — async WebSocket client

---

*Made with ☕ and frustration at Chinese e-commerce anti-bot walls.*
