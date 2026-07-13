---
name: cn-web-extraction
description: Extract product/article/page data from Chinese web platforms that are login-gated or anti-bot (Taobao/Tmall, JD, Pinduoduo, Xiaohongshu, Zhihu, Weibo, Douyin). Use when the user shares a share-link and asks "能拉到这个页面的信息吗" or needs price comparison across Chinese e-commerce platforms.
---

# Chinese Web Extraction

## ⚡ MCP-first (preferred)

This skill is backed by the **ecom-scraper MCP server**. If your agent has MCP support (Codex, Claude Code, Cursor, Trae, Reasonix), prefer calling these tools:

| Tool | What it does | Requirements |
|------|-------------|--------------|
| `taobao_search` | 淘宝/天猫关键词搜索 | Cookie file at `~/.ecom-cookies/taobao.json` |
| `jd_search` | 京东关键词搜索 | Chrome + logged-in profile |
| `check_cookies` | 检查各平台 cookie 状态 | — |

## Manual Fallback

If MCP tools are unavailable, use the Python API directly:

```python
from cn_scraper_mcp.engines import TaobaoEngine, JDEngine

# Taobao (no browser needed)
tb = TaobaoEngine(cookies_path="path/to/taobao_cookies.json")
result = tb.search("华为mate70", limit=10)

# JD (needs Chrome)
jd = JDEngine(profile_dir="~/.jd_login_profile")
result = jd.search("京东京造沐光")
```

## Platform-specific notes

### Taobao/Tmall
- Uses `curl_cffi` to impersonate Chrome TLS + MTOP HMAC-MD5 signing
- Items are in `data.itemsArray`, NOT `data.result` (always empty)
- `h5search` API is dead (502); use `appsearch`
- Cookie refresh: `_m_h5_tk` auto-rotates via Set-Cookie

### JD.com (京东)
- **Must be headful** (headless returns 0)
- Current selector: `div[data-sku]`
- Dead: `li.gl-item`, `#J_goodsList`, `p.3.cn/prices`, `club.jd.com/comment`
- Use persistent `--user-data-dir` profile, NOT cookie injection

### Pinduoduo (拼多多)
- `anti_content` token per session: only 1 search allowed per browser session
- Prefer product-link lookup over keyword search
- Mobile UA required (`iPhone; CPU iPhone OS 15_0`)

### Xiaohongshu (小红书)
- Guest search returns empty `__INITIAL_STATE__`
- Results arrive via signed XHR (`x-s`/`x-t` headers)
- Use local Chrome CDP (NOT cloud browser — datacenter IP is blocked)
- Cookie inject on `.xiaohongshu.com`, then navigate search URL

### Weibo / Zhihu / Douyin
- Heavily login-gated
- Cookie injection + CDP is the reliable path
- Fallback: ask user for a screenshot → OCR

## Cookie Harvest

Preferred method: Chrome CDP `Network.getAllCookies` from a logged-in session:

```python
from cn_scraper_mcp.engines.cdp import CDPClient
# Connect to running Chrome on debug port
# Run Network.getAllCookies for the target domain
```

## Pitfalls

- Never report boilerplate `og:` meta as product data
- Never fabricate prices or product names
- When all gates are closed, ask for a screenshot — don't guess
