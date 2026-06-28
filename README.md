# ZGraph

<p align="center">
  <a href="USAGE.md">使用指南</a> |
  <a href="ARCHITECTURE.md">架构图</a>
</p>

`ZGraph` 是一个以工作流为核心的 Agent Runtime，具备隔离的运行工作区、能力检索、`Guardian`工作流、确定性工作流执行、默认工具集、`CLI`模式以及兼容`OpenAI`的`/v1/chat/completions`接口。

## 使用场景

`ZGraph` 大幅收紧了大模型的能力边界，让大模型仅支持意图和拼接，对高容忍度提供自由拼装接口，对低容忍度提供完整工作流配置。具体场景为：

- 低智大模型在低容忍度业务下疯狂幻觉
- 后端业务接口封装极其复杂
- 后端业务接口有明确且全面的接口文档及错误提示信息

## 运行

```powershell
.\run_me.ps1 --env dev --offline "你好"
.\run_me.ps1 --env dev recommend
.\run_me.ps1 --env dev serve
```

运行时配置文件放在 `env/<name>.env`。你可以添加 `env/test.env` 或 `env/prod.env`，然后用 `--env test` 或 `--env prod` 选择：

```sh
./run_me.sh --env dev --offline "你好"
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `--serve` | 启动兼容 OpenAI 的 HTTP 服务。 |
| `--prompt`, `-p` | 通过参数传入 prompt，而不是位置参数。 |
| `--offline` | 不调用模型服务提供商。未配置 API key 时也会自动进入离线模式。 |
| `--auto-approve` | Guardian 审查后自动批准高风险中断。同时会为该运行启用 `bash`。 |
| `--json` | 打印完整的 `RuntimeResult` JSON，而不是只输出 `content`。 |

### CLI 命令

| 命令 | 别名 | 说明 |
|---------|---------|-------------|
| `serve` | — | 启动 HTTP 服务。也可以用 `--serve`。 |
| `recommend` | `recommendations`, `recommend-questions` | 基于最新记忆返回推荐问题。 |
| `resume` | `approve` | 批准并继续一个被中断的运行。 |
| `refuse` | `reject` | 拒绝一个被中断的运行。 |
| `validate-workflows` | `validate-workflow`, `workflow-validate` | 校验所有配置了 workflow 的 skill 对应的 YAML 是否合法。 |

不传 prompt 调用时会进入交互式 REPL。如果运行被中断，输入 `yes` / `同意` / `继续` 批准，或输入 `no` / `不同意` / `拒绝` 拒绝。

## HTTP 服务

HTTP 服务暴露以下端点：

```text
GET  /
GET  /health
GET  /v1/health
OPTIONS /v1/chat/completions   (CORS 预检)
POST /v1/chat/completions
POST /v1/recommendations
```

- `GET /`、`/health`、`/v1/health` 返回 `{"status":"ok","service":"zgraph"}`。
- `POST /v1/chat/completions` 接受 OpenAI 风格的请求体。如果配置了 `WHITELIST`，请求体必须包含与白名单匹配的 `app_id` 或 `user`。
- 非流式 `/v1/chat/completions` 响应会在顶层额外包含一个 `zgraph` 字段，内含完整 `RuntimeResult`。
- `POST /v1/recommendations` 基于最新记忆返回推荐问题，格式为 `{"data": [{"message": "..."}]}`。

## 配置

所有运行时设置都通过环境变量控制。项目附带的 `zgraph.config.default.yaml` 被启动脚本引用，但**目前运行时并不会解析它**；请使用下面列出的等效环境变量。

### LLM / 服务提供商

#### 多 provider 配置（推荐）

当前 ZGraph 内置支持三个 provider：`deepseek` / `kimi` / `minimax`。所有 provider 都通过 OpenAI 兼容协议接入，因此仅在 `base_url` 与默认 `model` 上做区分。

```bash
# 启用并选择默认 provider
ZGRAPH_PROVIDERS="deepseek,kimi,minimax"
ZGRAPH_DEFAULT_PROVIDER="deepseek"

# 各 provider 的 API key
DEEPSEEK_API_KEY="sk-..."
KIMI_API_KEY="sk-..."
MINIMAX_API_KEY="sk-..."

# 可选：覆盖默认 base_url 或 model
DEEPSEEK_MODEL="deepseek-chat"
DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
KIMI_MODEL="moonshot-v1-128k"
MINIMAX_BASE_URL="https://api.minimax.chat/v1"
```

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `ZGRAPH_PROVIDERS` | — | 启用的 provider 列表，逗号分隔、小写，例如 `"deepseek,kimi,minimax"`。未设置时回退到旧的单 provider 模式。 |
| `ZGRAPH_DEFAULT_PROVIDER` | `ZGRAPH_PROVIDERS` 字典序首个 | 当前默认 provider 名称。 |
| `<PROVIDER>_API_KEY` | — | 单个 provider 的 API key，例如 `DEEPSEEK_API_KEY`。 |
| `<PROVIDER>_MODEL` | 见下表 | 单个 provider 的默认 model，可被环境变量覆盖。 |
| `<PROVIDER>_BASE_URL` | 见下表 | 单个 provider 的 base URL，可被环境变量覆盖。 |

默认 `base_url` 与默认 `model`：

| Provider | `base_url` | `model` |
|---|---|---|
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-chat` |
| `kimi` | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| `minimax` | `https://api.minimax.chat/v1` | `MiniMax-Text-01` |

> 默认 base_url 是从公开信息整理的，请按内部实际地址通过 `<PROVIDER>_BASE_URL` 覆盖。

#### 单 provider 配置（向后兼容）

若未设置 `ZGRAPH_PROVIDERS`，ZGraph 自动使用下列旧字段合成名为 `default` 的 provider：

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `BASE_URL` | — | LLM 服务提供商的基础 URL。 |
| `APIKEY` / `API_KEY` | — | LLM 服务提供商的 API key。 |
| `LLM_MODEL_NAME` / `MODEL_NAME` | `gpt-4o-mini` | 模型名称。 |
| `LLM_PROVIDER` | `openai` | 提供商适配器（多 provider 模式下不再使用）。 |
| `LLM_TIMEOUT` | `120` | 请求超时时间（秒）。 |
| `LLM_TEMPERATURE` | — | 采样温度。 |
| `LLM_TOP_P` | — | Nucleus sampling 参数。 |
| `LLM_MAX_TOKENS` | — | 最大 token 数。 |
| `LLM_REASONING_EFFORT` | — | 推理强度提示。 |
| `STRUCTURED_OUTPUT` | `false` | 对兼容模型使用结构化输出。 |

### 运行时

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `ZGRAPH_OFFLINE` | `false` | 完全跳过 LLM 服务提供商。 |
| `ZGRAPH_STREAM` | `true` | 支持时以流式返回响应。 |
| `MAX_ROUNDS` | `50` | Agent 最大轮数。 |
| `SYSTEM_PROMPT` | 内置 | 默认系统提示词。 |
| `ZGRAPH_RUN_TTL_SECONDS` | `86400` | 运行工作区保留多久后清理。 |
| `ZGRAPH_ALLOW_BASH` | `false` | 允许 `bash` 工具执行 shell 命令。 |
| `ZGRAPH_AUTO_APPROVE_INTERRUPTS` | `false` | 自动批准高风险中断。 |

### 服务 / 工作区

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | HTTP 服务主机。 |
| `PORT` | `8001` | HTTP 服务端口。 |
| `ZGRAPH_HOME` | `./.zgraph` | 根工作区目录。 |
| `ZGRAPH_DATA_DIR` | `$ZGRAPH_HOME/data` | 持久化数据目录。 |
| `ZGRAPH_LAYER_CONFIG` | `./zgraph.config.default.yaml` | 层配置路径（当前未加载）。 |
| `WHITELIST` | — | serve 模式下允许的 `app_id`/`user` 逗号分隔列表。 |

### 能力检索

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `ZGRAPH_TOKENIZER_STRATEGY` | `word` | `rerank` 或 `word`。 |
| `RERANK_MODEL_NAME` | — | Rerank 模型名称。 |
| `RERANK_BASE_URL` | — | Rerank 端点基础 URL。 |
| `RERANK_API_KEY` | — | Rerank API key。 |
| `RERANK_TIMEOUT` | `30` | Rerank 请求超时。 |
| `RERANK_DOCUMENT_CHAR_LIMIT` | `1024` | Rerank 单文档最大字符数。 |
| `RERANK_BATCH_SIZE` | `4` | Rerank 批大小。 |
| `SKILL_SEARCH` | `true` | 启用 skill 搜索，而不是注入所有 skill。 |
| `SKILL_TOP_K` | `4` | 最多选择多少个匹配 skill。 |
| `SKILL_MIN_SCORE` | `0.18` | skill 匹配最低分。 |
| `SKILL_CONTEXT_CHAR_LIMIT` | `1200` | 注入系统提示词的 skill 文本最大字符数。 |
| `TOOL_TOP_K` | `4` | 最多选择多少个匹配工具。 |
| `TOOL_MIN_SCORE` | `0.18` | 工具匹配最低分。 |

### 记忆 / 日志

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `MEMORY_SUMMARY_MAX_TOKENS` | `256` | 记忆摘要最大 token 数。 |
| `MEMORY_SUMMARY_TEMPERATURE` | `0.2` | 记忆摘要温度。 |
| `ZGRAPH_LOG_LEVEL` | `INFO` | 日志级别。 |
| `ZGRAPH_LOG_ENABLED` | `true` | 是否启用日志。 |

### 媒体存储（Phase 3）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ZGRAPH_TMP_STORE_PATH` | `$ZGRAPH_HOME/storage` | 媒体文件根目录（图片/音频/视频）。 |
| `ZGRAPH_STORAGE_PROVIDERS` | `localfs` | 媒体后端列表，逗号分隔。当前只实现了 `localfs`；`minio` / `s3` 占位但跳过。 |
| `ZGRAPH_MEDIA_TTL_SECONDS` | `3600` | 媒体文件 TTL（秒），过期自动清理。 |

### Hook（Phase 2）

ZGraph 默认注册 4 个内置 hook，按顺序串联：

| Hook | 作用 |
|---|---|
| `AuditHook` | Final 事件触发时把 run 元数据写到 `runs/{run_id}/logs/audit.json` |
| `MetricsHook` | 累计 content / reasoning / tool call 计数到 `ctx.metadata` |
| `PIIFilterHook` | 拦截 `ContentDelta`，mask 邮箱 / 大陆手机号 / 18 位身份证 |
| `GuardianHook` | 监听 `ToolCallStart` + `Final`，跑 validate / risk / approve；高风险转 Final.status=interrupted |

可通过 `ZGraphRuntime(settings, hooks=[...])` 传入自定义列表替换默认。

## 工作区布局

运行级资源分两部分（Phase 3 整合后）：

```text
.zgraph/
  runs/{run_id}/
    logs/
      audit.json
      conversation.json
  storage/{run_id}/                  # 媒体 + 工作流产物（替代老的 runs/{run_id}/outputs/）
    {block_id}.{ext}
```

| 目录 | 用途 |
|---|---|
| `runs/{run_id}/logs/audit.json` | 审计元数据（NDJSON 追加） |
| `runs/{run_id}/logs/conversation.json` | 对话日志（含 reasoning_content） |
| `storage/{run_id}/{name}` | 媒体文件（图片 / 音频 / 视频 / 工作流产物），URL: `http://HOST:PORT/files/{run_id}/{name}` |

持久化数据存放在运行工作区之外：

- `.zgraph/data/memory.jsonl` — 保存的对话记忆。
- `.zgraph/audit/{run_id}/` — 已结束对话的归档，提供审计日志。

## Runtime 事件流（Phase 1）

`runtime.astream(user_input)` 返回 `AsyncIterator[RuntimeEvent]`。事件类型：

| 事件 | 触发时机 | 字段 |
|---|---|---|
| `ContentDelta` | 模型输出文本增量 | `text` |
| `ReasoningDelta` | thinking 模型推理增量 | `text`（来自 `additional_kwargs["reasoning_content"]`） |
| `MediaReady` | 媒体产出 | `block_id, modality, mime, url, size_bytes, metadata, expires_at` |
| `ToolCallStart` / `Args` / `End` | 工具调用生命周期 | `tool_call_id, tool_name, args_delta` |
| `Interrupt` | Guardian 中断 | `run_id, tool_call_id, reason, interrupt_token` |
| `Final` | 流结束 | `run_id, status, finish_reason, runtime_result` |

SSE 映射见 `zgraph/layer/output.py:CompletionsAsyncStreamOutputLayer.astream`，每个 chunk 带 `id:` 和 `event:` 字段支持断点续传。

## 运行时流程

1. **意图分析** — 将请求分类为 `hint`、`intent`、`todo`。离线时回退到本地词令牌启发式。
2. **能力编译** — 选择匹配的 `SKILL`、工具和工作流，计算 `risk_level` 和 `spawn_required`。
3. **Guardian 审查**（仅 medium/high risk）：
   - `validate` — 检查必要的状态/能力字段。
   - `risk` — 根据选中的工具重新计算风险。
   - `approve` — `medium risk` 自动批准；`high risk` 会产生中断，除非设置了 `--auto-approve`。
4. **执行** — 按以下优先级选择一条路径：
   - **配置工作流** — 如果选中的 `SKILL` 声明了 `workflow:` 或 `workflow_mode: strict`（或带有 `workflow` / `strict-workflow` 校验标签），则加载对应的 `workflow.yaml`，解析输入槽位，并由 `WorkflowExecutor` 确定性执行。
   - **临时工作流** — 如果能力编译器判定需要临时工作流，则先由 LLM 规划 `workflow.yaml`，再由另一个 LLM 审查并可选地修正，最后由 `WorkflowExecutor` 执行通过审查的计划。
   - **Agent 回退** — 否则使用 `langchain.agents.create_agent` 配合选中的工具和注入的 skill 文本执行。
   - **离线产物** — 离线或未配置 API key 时，写入静态运行时结果，而不是调用服务提供商。
5. **记忆与审计** — 将本次交互保存到 `memory.jsonl`，并为运行写入 `audit.json`。
6. **推荐问题** — `recommend` 和 `/v1/recommendations` 基于最新记忆消息生成后续问题。

## 工作流

`ZGraph`引入`app`的概念。主要包含两个方面的考量：

> 一方面，当后端业务有着极复杂的逻辑：
> 
> - 上传获得封面
> - 提交基础信息创建数据
> - 提交详细信息修正详情
> - 查询相关负责人、核准人等所属，且所属层级非常复杂
> - ……
>
> 另一方面，当前端逻辑有着不可跳过的操作：
>
> - 生成的内容必须上传`OSS`，因为前端仅提供`OSS`地址解析与预览
> - 表格信息必须带有后端逻辑，比如表格最右侧列需要由模型提供详情、修改等操作按钮
> - `OSS`必须自行删除认证信息，否则过期后前端无法呈现内容
> - ……
> 
> 在以上两个需求的加持下，`Agent`被迫成为整个公司产品线的最后一层兜底。
> 
> 也正因如此，`app`的概念则是在进化为`claw`后不得不从`dify`捡回来的兜底。

`ZGraph` 支持确定性的 YAML 工作流，Schema 如下：

```yaml
name: example
version: "1"
mode: sequential
description: "这个工作流的作用"
inputs:
  bank_name:
    description: 题库名称
    required: true
    aliases: [题库名]
    default: ""
steps:
  - id: create_bank
    name: 创建题库
    type: tool
    tool: bash
    needs: []
    args:
      command: 'curl -X POST "$ZBZN_BASE_URL/..." -d ''{"bankName":"{{inputs.bank_name}}"}'''
    outputs:
      BANK_ID: json.data.id
    assert:
      - BANK_ID exists
    retries: 1
```

> 完整案例在`./.zgraph/apps`下。

工作流特性：

- `needs` — 步骤依赖；步骤只在所有依赖步骤完成后执行。
- `outputs` — 使用 `json.path`、`data.path`、`content`、`ok` 从工具结果中提取值。
- `assert` — 轻量级断言，如 `VAR exists` 或 `VAR == value`。
- `retries` — 步骤失败时最多重试 3 次。
- `{{inputs.name}}`、`{{steps.step_id.outputs.name}}`、`{{state.key}}` — 参数中的模板替换。

Skill 通过 front-matter 绑定工作流：

```yaml
---
name: exam-plan
workflow: workflow
workflow_mode: strict
required_tools: bash, datetime
---
```

工作流文件按以下顺序发现：

1. `workflow:` 中指定的路径（相对 skill 目录或 `.zgraph/workflows/` 解析）。
2. skill `SKILL.md` 同目录下的 `workflow.yaml` / `workflow.yml`。
3. `.zgraph/workflows/{skill.name}.yaml` / `.yml`。

运行前可用 `validate-workflows` 检查所有配置工作流是否合法。普通输出会列出工作流名称、来源文件、必填输入和错误提示；需要机器读取时使用 `--json`。

## 工具

运行时注册以下工具：

| 工具 | 风险 | 说明 |
|------|------|-------------|
| `read` | low | 读取工作区文件。 |
| `glob` | low | 列出匹配模式的文件。 |
| `datetime` | low | 获取当前日期/时间；支持格式和时区。 |
| `write` | medium | 写入工作区文件。 |
| `update` | medium | 替换工作区文件中的文本。 |
| `settodolist` | medium | 存储当前运行的待办列表。 |
| `spawn` | medium | 创建子 agent 草稿产物。 |
| `approve-interrupt` | medium | 批准一个待处理的中断。 |
| `refuse-interrupt` | medium | 拒绝一个待处理的中断。 |
| `delete` | high | 删除工作区文件或目录。 |
| `bash` | high | 执行 shell 命令；需要 `ZGRAPH_ALLOW_BASH=true` 或自动批准。 |
| `adapter.call` | high | 调用 app 本地配置的 adapter 动作。 |

## Skills

运行时 skill 从 `ZGRAPH_HOME/apps` 和 `ZGRAPH_HOME/skills` 加载，默认即 `.zgraph/apps` 与 `.zgraph/skills`。加载器读取 `**/SKILL.md`，也支持顶层 `*.md` 作为简单本地 skill。

Skill front-matter 支持：

- `name` — skill 标识。
- `description` — 用于 skill 检索（搜索只按 description 排名）。
- `required_tools` — skill 期望的工具。
- `validations` — 例如 `strict-workflow`、`merged-output-created`。
- `tags` — 例如 `workflow`。
- `workflow` — 绑定的工作流文件名。
- `workflow_mode: strict` — 强制确定性工作流执行。
- `preconditions` — 所需前置条件。

重复 skill 名称会去重；第一个来源优先。

## 高风险任务与中断

交互式 CLI 模式下，高风险任务会暂停等待批准。输入 `yes` 继续同一运行，或输入 `no` 拒绝。非交互式使用时可显式恢复：

```powershell
.\run_me.ps1 --env dev resume <run_id>
.\run_me.ps1 --env dev refuse <run_id>
```

要在保留 Guardian 审查的同时自动批准所有高风险中断：

```powershell
.\run_me.ps1 --env dev --auto-approve "你的任务"
.\run_me.ps1 --env dev --auto-approve
```

同样的行为也可以通过 `ZGRAPH_AUTO_APPROVE_INTERRUPTS=true` 启用。
注意 `--auto-approve` 也会为被批准的那个运行启用 `bash`。
