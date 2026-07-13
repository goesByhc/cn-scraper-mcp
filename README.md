# 🔥 CN Scraper MCP

**Let AI agents search Chinese web platforms — Taobao, JD, Xiaohongshu, Zhihu, ZSXQ — without getting blocked.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-green)](https://modelcontextprotocol.io)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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

---

## Platform Support

### E-commerce 电商

| Platform | Method | Browser | Rate Limit | Status | Stability |
|----------|--------|---------|------------|--------|-----------|
| **淘宝/Tmall** 🔥 | `curl_cffi` + MTOP | ❌ None | Generous¹ | ✅ Verified | Stable |
| **京东/JD** | Chrome CDP headful | ✅ Required | Moderate | ✅ Verified | May break² |
| **拼多多/PDD** | — | — | — | ❌ Not implemented | — |

> ¹ Taobao rate limits are generous but subject to platform changes — not guaranteed "unlimited."
> ² JD relies on DOM selectors (`div[data-sku]`) which may change without notice.

### Content & Community 内容社区

| Platform | Method | Browser | Rate Limit | Status | Stability |
|----------|--------|---------|------------|--------|-----------|
| **小红书/XHS** | Local Chrome CDP + cookie | ✅ Required | Moderate | ✅ Verified | May break³ |
| **知乎/Zhihu** | REST API v4 | 🔑 Optional | Normal | ✅ Verified | Stable |
| **知识星球/ZSXQ** | REST API v2 | ❌ None | Normal | ✅ Verified | Stable |

> ³ Xiaohongshu blocks datacenter IPs at the network level; only residential IPs work.

### What works vs. what's dead

| API / Selector | Status | Notes |
|---------------|--------|-------|
| `mtop.taobao.wsearch.appsearch` → `itemsArray` | ✅ | Correct field; `data.result` is always `[]` |
| `h5api.m.taobao.com` h5search | ❌ DEAD | Returns 502 |
| `p.3.cn/prices/mgets` | ❌ DEAD | DNS no longer resolves |
| `club.jd.com/comment/productPageComments` | ❌ GATED | Returns "系统繁忙" (12 bytes) |
| `li.gl-item` / `#J_goodsList` | ❌ DEAD | JD changed layout |
| `div[data-sku]` | ✅ | Current JD product selector |
| XHS `section.note-item` | ✅ | Search results DOM |
| XHS `__INITIAL_STATE__.note.noteDetailMap` | ✅ | Note body + comments |
| ZSXQ `api.zsxq.com/v2/groups/{id}/topics` | ✅ | Cookie auth, no browser |
| Zhihu `api/v4/search_v3` | ✅ | Guest works; cookies optional |

---

## Quick Start

### Install

> ⚠️ **Not yet on PyPI** — install from source:

```bash
git clone https://github.com/goesByhc/cn-scraper-mcp.git
cd cn-scraper-mcp
pip install -e .
```

### Cookie Setup (one-time)

Each platform requires cookies from a **logged-in browser session**. Store them in `~/.cn-scraper-cookies/`:

```bash
mkdir -p ~/.cn-scraper-cookies
```

| Platform | Cookie file | How to get cookies |
|----------|------------|--------------------|
| 淘宝 | `taobao.json` | Log into `m.taobao.com`, export all cookies as JSON (DevTools → Application → Cookies). Needs `_m_h5_tk`, `_tb_token_`, `cookie2`, `cna`, `unb`, plus httponly `sgcookie`/`tfstk`/`isg` (use CDP harvest) |
| 京东 | `~/.jd_login_profile/` | Persistent Chrome profile — log in at `jd.com` once, profile remembers |
| 小红书 | `xiaohongshu.json` | DevTools export from `xiaohongshu.com`. Needs `web_session`, `a1`, `webId`, `gid`, `abRequestId` |
| 知乎 | `zhihu.json` | DevTools export from `zhihu.com`. Needs `z_c0`, `d_c0` |
| 知识星球 | `zsxq.json` | DevTools export from `zsxq.com`. Needs `zsxq_access_token` |

> ⚠️ **Taobao httponly cookies**: `sgcookie`, `tfstk`, `isg`, `havana_lgc2_0` are httponly — a manual DevTools copy-paste won't include them. Use CDP `Network.getAllCookies` from a logged-in Chrome session to harvest the full set.

### Run

```bash
cn-scraper-mcp
# or: python -m cn_scraper_mcp.server
```

### Python API

```python
from cn_scraper_mcp.engines import TaobaoEngine, ZhihuEngine, XiaohongshuEngine

# Taobao — pure script, no browser
tb = TaobaoEngine(cookies_path="~/.cn-scraper-cookies/taobao.json")
r = tb.search("华为mate70", limit=5)
print(r["items"][0]["price"])  # "3099.00"

# Zhihu — REST API, guest mode works
zh = ZhihuEngine()
r = zh.search("半导体")
r = zh.hot_list()  # trending topics

# Xiaohongshu — needs local Chrome
xhs = XiaohongshuEngine(cookies_path="~/.cn-scraper-cookies/xiaohongshu.json")
notes = xhs.search("儿童学习桌")
detail = xhs.get_note(notes["items"][0]["noteId"])

# 知识星球 — REST API
from cn_scraper_mcp.engines import ZsxqEngine
zs = ZsxqEngine(cookies_path="~/.cn-scraper-cookies/zsxq.json")
topics = zs.get_topics("28888555451", count=5)
```

---

## MCP Tools

| Tool | Platform | Browser | What it does |
|------|----------|---------|-------------|
| `taobao_search` | 淘宝/Tmall | ❌ | Keyword search → price, sales, shop |
| `jd_search` | 京东 | ✅ | Keyword search → SKU, price, name |
| `xiaohongshu_search` | 小红书 | ✅ | Search notes → title, author, likes |
| `xiaohongshu_note` | 小红书 | ✅ | Get note detail → body, tags, comments |
| `zhihu_search` | 知乎 | 🔑 | Search → questions, articles |
| `zhihu_hot_list` | 知乎 | 🔑 | Trending topics |
| `zsxq_topics` | 知识星球 | ❌ | Fetch group posts → text, comments |
| `check_cookies` | All | ❌ | Diagnose cookie freshness |

---

## MCP Integration

### Codex

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.cn-scraper]
type = "stdio"
command = "cn-scraper-mcp"
args = []
autoApprove = ["taobao_search", "jd_search", "xiaohongshu_search", "zhihu_search", "zhihu_hot_list", "zsxq_topics", "check_cookies"]
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

## Architecture

```
AI Agent (Codex / Claude / Cursor / Trae / Reasonix)
    │
    ├─ taobao_search("华为")  ──→  TaobaoEngine  ──→  curl_cffi + MTOP  ──→  h5api.m.taobao.com
    ├─ jd_search("京造")      ──→  JDEngine      ──→  Chrome CDP         ──→  search.jd.com
    ├─ xiaohongshu_search()   ──→  XHSEngine      ──→  Local Chrome CDP   ──→  xiaohongshu.com
    ├─ zhihu_search()         ──→  ZhihuEngine    ──→  REST API v4        ──→  zhihu.com
    └─ zsxq_topics()          ──→  ZsxqEngine     ──→  REST API v2        ──→  api.zsxq.com
```

---

## FAQ

**Q: Why not Playwright / Selenium?**
Heavier, slower, and many AI agents can't run them. `curl_cffi` + raw CDP websockets = minimal dependencies.

**Q: Taobao returns `Session过期`.**
Your `_m_h5_tk` cookie expired. Re-harvest from a fresh browser session.

**Q: JD returns 0 results.**
3 possibilities: (1) Chrome is headless → switch to headful. (2) Profile not logged in. (3) Cookie injection without real login session → use persistent profile.

**Q: XHS search "IP存在风险".**
You're using a cloud/datacenter browser. XHS blocks these at IP level. Use **local Chrome** on your residential IP.

**Q: Is this legal?**
For **educational and research purposes only**. Scraping may violate platform ToS. Use at your own risk. Never spam, DDoS, or scrape at commercial scale.

---

## Roadmap

- [x] Taobao/Tmall (curl_cffi + MTOP)
- [x] JD.com (Chrome CDP headful)
- [x] Xiaohongshu (local CDP + cookie)
- [x] Zhihu (REST API)
- [x] ZSXQ / 知识星球 (REST API)
- [ ] Weibo / Douyin
- [ ] Pinduoduo MCP tool (anti_content session mgmt)
- [ ] Publish to PyPI
- [ ] Cookie harvest automation (CDP Network.getAllCookies)

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [curl_cffi](https://github.com/lexiforest/curl_cffi) — TLS fingerprint impersonation
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [websockets](https://github.com/python-websockets/websockets) — async WebSocket client

---

*Made with ☕ and frustration at Chinese platform anti-bot walls.*
