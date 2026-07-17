# 架构设计

本文档定义 `cn-scraper-mcp` 的长期职责边界。新增功能和代码评审应以本文档为准。

## Agent 开发守则（必须先读）

Agent 修改代码前必须先确认平台真实契约，再讨论实现。测试通过只证明代码符合测试，不能证明平台功能正确。

### 为什么之前会频繁修错

小红书评论功能的反复失败暴露了几类通用问题：

1. **先设计抽象，后理解平台。** 把不同平台的搜索、评论和商品能力强行视为统一接口，导致错误的聚合层、normalizer 和 Engine 基类方案。
2. **只看局部函数，没有追踪完整调用链。** 搜索结果已经包含 `noteId` 和 `xsec_token`，但后续评论工具只传了 `noteId`，关键平台参数在 MCP 边界被丢失。
3. **用降级输出掩盖功能失败。** 结构化评论抓取失败后，曾改成返回整页 `bodyText`，同时让 `comments` 永远为空；这不是 fallback，而是改变了工具契约。
4. **修改测试去认可错误实现。** 测试从断言结构化评论退化为断言页面文本，使测试变绿，却失去了保护真实需求的作用。
5. **没有区分“无数据”和“被拦截”。** 登录失效、验证码、IP 风控、选择器失效和帖子确实没有评论被统一表现为空数组，Agent 无法决定下一步。
6. **Review 只检查语法和单测。** 没有同时检查 MCP schema、参数是否端到端保留、返回值是否仍符合工具描述，以及示例是否还在传播旧调用方式。

这些问题的共同根因是：**实现替代了需求，局部测试替代了端到端契约。**

### 强制工作流

每次修改按以下顺序执行：

1. **读取边界。** 先读本文档；编码规范、命令和安全要求再读 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。
2. **用 CodeGraph 定位。** 先调用 `codegraph_context`；参数链用 `codegraph_trace`，影响面用 `codegraph_impact`。只有 CodeGraph 没覆盖具体细节时才读取或搜索原文件。
3. **写清契约。** 在动代码前明确：平台原生输入、认证要求、返回字段、错误状态、分页/限频行为，以及 Agent 负责的判断。
4. **追踪端到端数据。** 检查 `平台响应 -> Engine -> server.py -> MCP schema -> Agent 下一次调用`，任何必需字段都不能在中间层丢失。
5. **先保住行为测试。** 正常结果、空结果、认证失效、风控和异常都要测试。禁止为了让测试通过而删除字段断言、把结构化结果改成原始文本，或把异常吞成空列表。
6. **做最小实现。** 平台问题在对应 Engine 内解决；只有认证、Cookie、HTTP、CDP、日志等确定性基础设施可以跨平台复用。
7. **自审完整契约。** 检查工具描述、函数签名、示例、README/Architecture、测试和 smoke 工具列表是否一致。
8. **分层验证。** 依次运行定向测试、Ruff、全量测试、MCP smoke 和 `git diff --check`；最后检查 staged/unstaged 状态，避免混入或覆盖用户修改。

### 不可接受的“修复”

- 返回整页文本、HTML 或调试信息，代替工具承诺的结构化业务结果。
- 捕获所有异常后返回空数组，让调用方误判为“没有数据”。
- 为两个名称相似的方法建立跨平台业务抽象，却没有真实的第三个调用方和稳定共同语义。
- 在没有证据时猜 API 字段、DOM selector 或登录信号，并把猜测写成唯一实现。
- 只新增 mock happy path，不覆盖参数传递、页面拦截和失败状态。
- 未经用户要求使用真实账号进行高频或有副作用的在线验证。

### 完成定义

一次修改只有同时满足以下条件才算完成：

- MCP 工具仍提供描述中承诺的能力，而不是“有返回值”。
- 平台必需参数从产生位置完整传到消费位置。
- 成功、空数据、认证失败、风控和程序异常可区分。
- 测试保护需求，没有认可降级实现。
- 定向测试、全量测试、Ruff、MCP smoke 和 diff 检查通过。
- 文档示例与当前 MCP schema 一致。

### 可直接给 Agent 的任务前缀

```text
开始前先阅读 docs/architecture.md，并用 CodeGraph 追踪完整调用链。
先写明平台原生输入、返回契约和错误状态，再修改代码。
不要新增跨平台聚合或业务统一层；不要用 bodyText/HTML 代替结构化结果；
不要修改测试去认可降级行为。完成后执行定向测试、全量测试、Ruff、
MCP smoke、git diff --check，并自审 MCP schema、示例和参数端到端传递。
```

## 核心原则：Agent 负责决策，MCP Server 提供原子能力

`cn-scraper-mcp` 是面向 Agent 的平台能力服务器，不是跨平台搜索或推荐系统。

MCP Server 应把每个平台的真实能力暴露为清晰、稳定、自描述的原子工具；由 Agent 根据用户意图选择平台、并行调用工具、比较结果并形成结论。

### Agent 的职责

- 根据用户目标判断需要调用哪些平台。
- 决定串行或并行调用，以及失败后的重试策略。
- 综合多个平台的结果。
- 执行跨平台比较、排序、去重、聚类和推荐。
- 向用户解释选择依据和结果差异。

### MCP Server 的职责

- 提供平台原生、边界明确的 MCP 工具。
- 校验单次工具调用的输入。
- 处理平台认证、HTTP、CDP、限速和响应解析。
- 返回 Agent 能理解的结构化结果、警告和错误。
- 保留平台特有信息，不替 Agent 做跨平台判断。

## 唯一统一的业务领域：登录状态

跨平台允许统一的业务抽象仅限于登录状态，因为所有需要认证的平台都面临同一类基础问题：凭证从哪里获取、当前是否已登录、缓存的凭证是否仍然有效。

可以统一：

- 获取或收割 Cookie 等登录凭证。
- 定位和读取本地凭证缓存。
- 验证必需的登录字段是否存在。
- 验证当前登录状态是否有效、过期或需要人工重新登录。
- 检查凭证缓存的存在时间、新鲜度和可用性。
- 返回不包含凭证值的登录状态与修复提示。

建议共享的登录状态只描述认证事实，例如：

```json
{
  "platform": "weibo",
  "state": "valid",
  "cached": true,
  "stale": false,
  "missing_fields": [],
  "hint": ""
}
```

登录状态层不得解释搜索结果，也不得知道热搜、评论、商品、价格等平台业务结构。各 Engine 仍自行决定如何把凭证注入自己的请求。

HTTP Client、CDP transport、日志和错误映射可以作为技术基础设施复用；这种代码复用不代表平台业务接口需要统一。

### 登录验证的两层语义

- `check_cookies` 只检查本地文件、必需字段和缓存新鲜度；`ready` 不等于平台仍接受该凭证。
- `verify_login` 发起只读在线请求，只有平台明确接受后才返回 `verified: true`。
- 当前知乎使用 `/api/v4/me` 身份端点；微博使用桌面 AJAX 的空查询作为无副作用认证探针。
- 没有稳定只读探针的平台必须返回 `remote_state: unsupported`，禁止用文件存在、Cookie 字段齐全或业务空结果冒充在线验证成功。
- 远端不可达、限流和服务端错误使用统一技术错误；登录被拒绝返回明确的 `rejected` 状态。

### 明确禁止的方向

除非存在无法由 Agent 完成的确定性基础设施需求，否则不新增：

- `search_all`、`search_products`、`search_content` 一类跨平台调度工具。
- `compare_prices` 一类替 Agent 执行跨平台比较或最佳结果判断的工具。
- 服务端自动选择平台或把无效平台回退为默认平台。
- 跨平台排序、推荐、去重、聚类或“最佳结果”判断。
- 为统一调度而强迫所有 Engine 实现相同能力。
- 跨平台的 Search/HotList/Comment/Product Item 模型、normalizer 或业务 Protocol。
- 与 Agent 重复的规划、重试和决策逻辑。

## MCP 工具设计

工具应以平台和原生操作命名，例如：

- `taobao_search`
- `jd_search`
- `weibo_search`
- `weibo_hot_list`
- `zhihu_hot_list`
- `zsxq_topics`
- `zsxq_article`

不支持某项能力的平台不应提供占位方法。例如，知识星球只有基于 `group_id` 的 topics/article 能力，不应为了统一接口添加会抛出 `NotImplementedError` 的关键词 `search()`。

平台原生的关联参数也不应为了制造统一接口而隐藏。例如，小红书搜索结果会返回同一笔记的 `noteId` 和 `xsec_token`；Agent 调用 `xiaohongshu_comments` 时负责把这两个字段一起传回。MCP 工具负责清晰声明参数约束并获取该平台的结构化评论，不负责跨平台聚合或评论语义判断。

### 工具描述必须告诉 Agent

- 工具解决什么问题。
- 是否需要 Cookie、浏览器或人工登录。
- 输入参数的含义和限制。
- 调用是否昂贵、限频或具有平台风控风险。
- 返回字段的语义。
- 失败是否值得重试，以及用户可以采取什么行动。

### 返回结果

每个工具应返回符合其平台原生语义、且字段名称清晰的结果。例如淘宝商品、微博热搜、知乎回答和知识星球主题不需要被转换成同一种 Item。

工具可以使用一致的技术性错误结构：

```json
{
  "ok": false,
  "platform": "weibo",
  "operation": "search",
  "error": {
    "code": "AUTH_REQUIRED",
    "message": "微博登录状态不可用",
    "retryable": false,
    "hint": "请重新收割微博 Cookie"
  }
}
```

成功结果不建立跨平台公共字段集，也不通过 `platform_data` 包装成伪统一模型。Agent 直接依据每个 MCP 工具的独立 schema 理解结果。

结构化错误和登录状态属于基础协议；搜索、热搜、评论、商品和价格结果属于平台业务。两者不可混淆。

### 技术错误的渐进统一

统一的是失败类别和恢复动作，而不是各平台成功结果：

- 认证缺失/失效、HTTP 传输失败、平台限流、解析失败和浏览器故障使用 `ScraperError` 子类。
- MCP 边界用 `error_response()` 输出稳定的 `{ok, error: {code, message, retryable, hint}}`。
- Engine 的成功字典保持平台原生结构，不增加跨平台 Item、normalizer 或业务包装层。
- 旧 Engine 可逐个平台迁移；新代码不得继续发明新的裸 `{"error": "..."}` 技术错误格式。

### 模块边界与故障隔离

- `server.py` 只负责 MCP 工具声明、调用编排和异常到 MCP 结果的转换。
- `validation.py` 负责 MCP 输入参数校验，不导入 FastMCP，也不理解平台响应。
- `diagnostics.py` 负责本地环境诊断，不做真实抓取。
- `auth.py` 负责本地凭证缓存；`auth_verify.py` 负责远端登录验证。
- MCP 工具必须直接导入对应平台模块，例如 `engines.weibo`；不得通过 eager `engines` 聚合导入让一个平台的依赖错误影响其他平台。
- `engines.__init__` 只保留惰性兼容导出，不能在包导入时加载全部平台。

当前 `HttpClient` 的限速状态属于单次 Client 实例，不保证跨 MCP 调用生效。这是已确认的性能/风险取舍；在没有明确新需求前，不为此引入线程、全局锁或后台调度。

## 平台契约记录

平台接口、DOM 和风控会变化。实现前应以当前代码、真实响应或可信的一手资料确认契约；本节记录已知边界，不代表可以跳过验证。

| 平台 | 当前能力路径 | 关键约束 |
|---|---|---|
| 淘宝 | MTOP 搜索接口 | 依赖完整 Cookie 和签名；不要回退到已失效的 h5search 响应结构 |
| 京东 | 本地 Chrome 生成登录态/设备签名，注入式观察器读取平台 JSON API；DOM 仅降级 | `h5st` 和设备令牌不能安全地脱离浏览器重放；观察器不得记录 Cookie、请求头、请求体或签名 URL |
| 拼多多 | 浏览器搜索页 | 风控严格，目前不推荐依赖连续搜索 |
| 小红书 | 本地浏览器 CDP + 页面状态/DOM | 需要住宅 IP、登录 Cookie；后续读取必须保留搜索结果的访问参数 |
| 知乎 | v4 API | 搜索需要 `z_c0`、`d_c0` 登录态 |
| 微博 | AJAX API | 搜索和时间线需要 `SUB`；热搜可游客访问 |
| 知识星球 | v2 REST API | 使用 group/topic 原生能力，不提供伪关键词搜索 |
| 抖音 | 浏览器 CDP | 可能需要用户手动完成验证码，不能把等待状态当成空结果 |
| B 站 | 公开 Web API：search/type、popular、view、reply/main | 当前四项能力均可游客纯 HTTP 调用；遇到 `-412` 必须低频退避，不能自动升级成浏览器抓取 |
| 豆瓣 | 移动搜索页 + 平台 JSON 端点 | 搜索在 JSON 端点受限时只回退到移动搜索页；条目详情/评论可能要求登录或触发风控，不能把风控页面当成空数据 |
| 大众点评 | 商户搜索页、商户详情页、评价页 | 页面结构和风控变化较快；解析失败或被拦截必须返回明确错误，不得静默返回空商户/空评价 |

### 知乎调用链

```text
zhihu_search(keyword)
  -> item.id + item.type
      -> zhihu_comments(item.id)   # 仅对 type="answer" 的项
```

### 微博调用链

```text
weibo_search(keyword) / weibo_user_timeline(uid)
  -> item.id (mid)
      -> weibo_comments(item.id)   # 首屏结构化评论
```

### B 站调用链

```text
bilibili_search(keyword) / bilibili_popular()
  -> item.bvid
      -> bilibili_video(item.bvid)
      -> bilibili_comments(item.bvid, cursor=next_cursor)
```

B 站评论接口内部先用 `bvid` 查询 `aid`，再调用视频类型 `type=1` 的一级评论接口。新版 `reply/main` 遇到游客风控码 `-352/-412` 时降级到公开的 `/x/v2/reply` 页码接口，并通过 `pagination` 告知 Agent 当前游标语义。`bvid` 是 Agent 可见的稳定关联参数；Engine 不要求 Agent 理解或额外传递内部 `aid`。

约束：

- 知乎评论仅支持 `type="answer"` 的结果项，调用 `v4/answers/{id}/comments`。
- 微博评论调用 `weibo.com/ajax/statuses/buildComments`，需传入搜索结果的 `id`（mid）。
- 两者均为纯 REST API，并发安全；需登录 cookie。
- 两个平台各自返回 `count` + `comments`，字段以各自 MCP 工具描述为准；相似字段不构成跨平台公共评论模型。

### 知乎、微博评论在线验证基线

2026-07-16 使用本地已有登录 Cookie 做过低频真实请求验证：

- `tests/test_zhihu.py` 与 `tests/test_weibo.py` 共 54 项测试通过。
- 知乎按 `zhihu_search -> answer item.id -> zhihu_comments` 调用，真实回答成功返回 3 条评论。
- 微博按 `weibo_user_timeline -> item.id -> weibo_comments` 调用，从一条已知有 457 条评论的微博成功返回 3 条结构化评论。
- 两份 Cookie 缓存虽然超过新鲜度阈值，但在线请求仍成功；`stale` 只表示应重新验证，不等同于登录已经失效。

已知限制：

- 知乎真实响应的作者名位于 `author.member.name`，正文包含 HTML；Engine 必须按该结构提取作者并清理正文标签，测试 fixture 也应保持这一真实层级。
- 单次返回空 `comments` 只能表示该请求没有取得评论，不能单独证明接口失效。在线验证应选择已知存在评论的内容，并同时检查是否返回认证或风控错误。
- 真实账号验证必须保持低频、最小条数，禁止把在线接口加入默认高频测试。上述样本 ID 仅记录当时验证事实，不作为永久测试 fixture。

### 小红书调用链

```text
xiaohongshu_search(keyword)
  -> item.noteId + item.xsec_token
      -> xiaohongshu_note(note_id)                 # 帖子正文与互动信息
      -> xiaohongshu_comments(note_id, xsec_token) # 首屏结构化评论
```

约束：

- `xiaohongshu_comments` 的 `xsec_token` 必须来自与 `note_id` 相同的搜索项，不能跨帖子复用。
- 评论读取优先使用请求笔记对应的页面状态，再回退到已渲染评论 DOM；不得回退为整页 `bodyText`。
- 返回 `state`、`count`、`source` 和 `comments`。单条评论包含 `id`、`content`、`userName`、`userId`、`likes`、`time`。
- 当前只获取首屏已加载评论，不承诺自动翻页。
- 登录失效、验证码、IP 风控、程序异常和正常空评论必须保持可区分。

## 分层与依赖方向

```text
Agent
  -> MCP tools (server.py)
      -> platform Engine
          -> shared infrastructure
             - http.py
             - engines/cdp.py
             - auth.py
             - logging.py
             - errors.py
```

依赖规则：

1. `server.py` 负责 MCP 注册、输入校验和错误映射，不承担跨平台编排。
2. 每个平台 Engine 独立实现真实平台能力，Engine 之间互不依赖。
3. 跨平台业务统一只发生在 Cookie 获取、登录验证和凭证缓存有效性检查。
4. Engine 可以复用 HTTP、CDP、Cookie 和日志基础设施，但不共享搜索等业务结果模型。
5. 基础设施层不依赖 `server.py`，也不包含 Agent 决策逻辑。
6. 只有出现真实的多个调用方时才提取共享抽象，不为假想的聚合或插件系统提前设计基类。

## Engine 接口策略

不建立统一 `BaseEngine`，也不建立跨平台的 `SearchEngine`、`HotListEngine`、`CommentEngine` 或 `ProductEngine` Protocol。即使两个平台的方法名称相同，其参数约束、成本、风控、分页和返回语义也可能不同。

每个平台直接实现自己的原生操作，并通过独立 MCP schema 暴露给 Agent。只有登录状态可以定义共享接口，例如凭证获取、登录验证和缓存有效性检查。

HTTP、CDP、Cookie、日志等技术复用优先采用组合，而不是建立深层 `HttpEngine` / `CdpEngine` 继承树。

## 删除跨平台比价

`compare.py` 和 `compare_prices` 把平台选择、结果标准化和"最佳价格"判断固化在 Server 内，与本设计原则冲突，已在当前 Unreleased 变更中删除。

已完成的删除范围：

- `src/cn_scraper_mcp/compare.py` — 已删除。
- `server.py` 中的 `compare_prices` MCP 工具 — 已移除。
- `engines/__init__.py` 中的相关导出 — 已清理。
- `tests/test_compare.py` — 已删除。
- README、工具清单和示例中的 `compare_prices` 文档 — 已更新。

Agent 需要比价时，应分别调用淘宝、京东、拼多多等平台工具，并结合用户要求自行比较。

## 新增功能的判断标准

新增 MCP 工具前依次回答：

1. 这是平台原生能力，还是 Agent 可以通过多个现有工具完成的决策？
2. 如果是决策逻辑，为什么必须放在 Server，而不能交给 Agent？
3. 工具是否暴露了平台真实能力，而不是为了统一接口制造假能力？
4. 除登录状态外，是否引入了跨平台公共模型、normalizer、Protocol 或聚合逻辑？
5. 输入、认证要求、成本、风控风险、返回语义和错误恢复方式是否足够清晰？
6. 是否可以独立测试而不访问真实网络、浏览器或用户目录？

如果第 1 项属于 Agent 决策且第 2 项没有明确的基础设施理由，则不应新增该工具。

## 非目标

当前架构不以以下能力为目标：

- 在 MCP Server 内复刻 Agent 的规划能力。
- 建立通用搜索网关或推荐引擎。
- 统一处理不同平台的搜索、热搜、评论、商品或价格结果。
- 强制所有平台返回相同的数据模型或实现相同的业务 Protocol。
- 为尚未存在的第三方插件提前设计复杂生命周期。
- 用服务端聚合隐藏平台之间真实的能力和限制差异。
