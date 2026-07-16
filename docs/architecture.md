# 架构设计

本文档定义 `cn-scraper-mcp` 的长期职责边界。新增功能和代码评审应以本文档为准。

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
