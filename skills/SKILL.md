---
name: cn-web-extraction
description: Extract data from Chinese web platforms — Taobao, JD, Pinduoduo, Xiaohongshu, Zhihu, ZSXQ, Weibo, Douyin. Use when the user needs price comparison, content search, or data extraction from Chinese platforms. Backed by the cn-scraper MCP server.
---

# Chinese Web Extraction

## ⚡ MCP-first (preferred)

This skill is backed by **cn-scraper MCP server**. If your agent has MCP support, prefer these tools:

### E-commerce 电商

| Tool | Platform | What it does |
|------|----------|-------------|
| `taobao_search` | 淘宝/Tmall | Keyword search → price, sales, shop. Pure script, no browser, unlimited. |
| `jd_search` | 京东 | Keyword search → SKU, price, name. Needs headful Chrome. |

### Content & Community 内容社区

| Tool | Platform | What it does |
|------|----------|-------------|
| `xiaohongshu_search` | 小红书 | Search notes → title, author, likes. Needs local Chrome. |
| `xiaohongshu_note` | 小红书 | Get note detail → body, tags, comments. |
| `zhihu_search` | 知乎 | Search questions/articles. Guest mode works. |
| `zhihu_hot_list` | 知乎 | Current trending topics. |
| `zsxq_topics` | 知识星球 | Fetch paid-group latest posts → text, comments. REST API. |

### Diagnostics

| `check_cookies` | All platforms | Check cookie freshness for all 6 platforms. |

## Manual Fallback

If MCP tools are unavailable, use the Python API directly:

```python
from cn_scraper_mcp.engines import (
    TaobaoEngine, JDEngine,
    XiaohongshuEngine, ZhihuEngine, ZsxqEngine,
)

# E-commerce
TaobaoEngine("~/.cn-scraper-cookies/taobao.json").search("华为mate70")
JDEngine(profile_dir="~/.jd_login_profile").search("京东京造沐光")

# Content
XiaohongshuEngine().search("儿童学习桌")
ZhihuEngine().search("半导体投资")
ZhihuEngine().hot_list()
ZsxqEngine().get_topics("28888555451", count=5)
```

## Platform-specific notes

### Taobao/Tmall
- `curl_cffi` impersonate=chrome + MTOP HMAC-MD5 signing
- Items in `data.itemsArray` (NOT `data.result`)
- `h5search` API dead (502) → use `appsearch`
- `_m_h5_tk` auto-rotates via Set-Cookie
- httponly cookies (sgcookie/tfstk/isg) must be harvested via CDP

### JD.com
- Headful only (headless = 0 results)
- Current selector: `div[data-sku]`
- Dead: `li.gl-item`, `#J_goodsList`, `p.3.cn/prices`, `club.jd.com/comment`
- Persistent `--user-data-dir` profile, not cookie injection

### Pinduoduo
- `anti_content` token: 1 search per browser session
- Prefer product-link over keyword search
- Mobile UA required

### Xiaohongshu (小红书)
- **Local Chrome only** (datacenter IP → error_code=300012)
- Guest curl → empty shell. Results are JS-signed XHR.
- Cookies: `web_session`, `a1`, `webId`, `gid`, `abRequestId`, `xsecappid`
- Search DOM: `section.note-item` → title, author, likes
- Note detail: `__INITIAL_STATE__.note.noteDetailMap[id].note`
- Comments: `note.comments.list[]`

### Zhihu (知乎)
- REST API v4 `search_v3` — guest works for public content
- Cookies (`z_c0` + `d_c0`) needed for full access
- Hot list: `api/v3/feed/topstory/hot-lists/total`

### ZSXQ / 知识星球
- REST API v2 — cookie auth, no browser
- Cookie: `zsxq_access_token`
- Endpoints: `/v2/groups/{id}/topics` (all or `scope=by_owner`)
- Article-type posts: `talk.article.inline_article_url` for full body

## Pitfalls

- Never fabricate prices or product data
- When gated, ask for screenshot → OCR — don't guess
- XHS/IP risk: only local Chrome, never cloud browser
- JD/headless: always use headful, persistent profile
- Taobao/httponly: full cookie set needed (49+ cookies), CDP harvest
- ZSXQ/cookie expiry: `zsxq_access_token` expires — needs re-login
