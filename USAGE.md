# ZGraph 使用指南

本文件列出 ZGraph 每个功能在 **CLI** 和 **OpenAI-compatible HTTP completions** 两种入口下的触发方式，并附上可以直接复制到 `cmd.exe` 或 PowerShell 运行的命令。

> 假设当前工作目录就是项目根目录，且已存在 `env/dev.env`（或你自定义的 env 文件）。

---

## 目录

1. [前置准备](#前置准备)
2. [普通对话 / 执行 prompt](#普通对话--执行-prompt)
3. [推荐问题](#推荐问题)
4. [HTTP serve 模式](#http-serve-模式)
5. [高风险任务与中断恢复](#高风险任务与中断恢复)
6. [工作流校验](#工作流校验)
7. [离线模式](#离线模式)
8. [常用 flag 速查](#常用-flag-速查)

---

## 前置准备

所有命令依赖 `run_me.ps1`（PowerShell）或 `run_me.sh`（Git Bash / WSL）。它们会读取 `env/<name>.env` 加载环境变量。

默认使用 `dev` 环境：

```powershell
# PowerShell
.\run_me.ps1 --env dev "hello"
```

```cmd
# cmd.exe 需要先调用 PowerShell
powershell -ExecutionPolicy Bypass -File .\run_me.ps1 --env dev "hello"
```

---

## 普通对话 / 执行 prompt

### CLI

直接传 prompt 作为位置参数：

```powershell
.\run_me.ps1 --env dev "你好，介绍一下自己"
```

使用 `--prompt` / `-p`：

```powershell
.\run_me.ps1 --env dev --prompt "你好"
```

JSON 输出完整 RuntimeResult：

```powershell
.\run_me.ps1 --env dev --json "你好"
```

### Completions (HTTP)

启动 serve：

```powershell
# PowerShell 新窗口
Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File .\run_me.ps1 --env dev serve"
```

然后调用：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/v1/chat/completions" -Method POST -ContentType "application/json" -Body '{"model":"zgraph","messages":[{"role":"user","content":"你好"}]}'
```

流式调用（PowerShell）：

```powershell
$body = '{"model":"zgraph","stream":true,"messages":[{"role":"user","content":"你好"}]}'
Invoke-RestMethod -Uri "http://127.0.0.1:8001/v1/chat/completions" -Method POST -ContentType "application/json" -Body $body -OutFile $null
```

> 非流式响应会在顶层额外返回一个 `zgraph` 字段，包含完整 `RuntimeResult`。

---

## 推荐问题

推荐问题基于最近一次保存的记忆生成，返回 `{"data": [{"message": "..."}]}`。

### CLI

```powershell
.\run_me.ps1 --env dev recommend
```

等价别名：

```powershell
.\run_me.ps1 --env dev recommendations
.\run_me.ps1 --env dev recommend-questions
```

### Completions (HTTP)

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/v1/recommendations" -Method POST
```

---

## HTTP serve 模式

启动 OpenAI-compatible 服务器：

```powershell
# PowerShell
.\run_me.ps1 --env dev serve

# 或者使用 flag
.\run_me.ps1 --env dev --serve
```

可用端点：

```text
GET  /
GET  /health
GET  /v1/health
OPTIONS /v1/chat/completions
POST /v1/chat/completions
POST /v1/recommendations
```

健康检查：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/v1/health" -Method GET
```

带 `WHITELIST` 的 completions 调用：

```powershell
$body = '{"model":"zgraph","app_id":"my-app","messages":[{"role":"user","content":"你好"}]}'
Invoke-RestMethod -Uri "http://127.0.0.1:8001/v1/chat/completions" -Method POST -ContentType "application/json" -Body $body
```

---

## 高风险任务与中断恢复

涉及 `bash`、`delete` 等工具的任务会被判定为 high risk。默认需要人工批准。

### 直接执行（会中断）

```powershell
.\run_me.ps1 --env dev "用 bash 列出当前目录"
```

如果中断，CLI 会输出 `run_id` 和继续命令：

```text
Pending approval for run <run_id>. Type yes to continue or no to refuse.
```

### 自动批准

```powershell
.\run_me.ps1 --env dev --auto-approve "用 bash 列出当前目录"
```

> `--auto-approve` 同时会把该运行的 `bash` 权限打开。

### 恢复中断的运行

```powershell
.\run_me.ps1 --env dev resume <run_id>

# 别名
.\run_me.ps1 --env dev approve <run_id>
```

### 拒绝中断的运行

```powershell
.\run_me.ps1 --env dev refuse <run_id>

# 别名
.\run_me.ps1 --env dev reject <run_id>
```

### 交互式 REPL

不传 prompt 进入交互模式：

```powershell
.\run_me.ps1 --env dev
```

中断时直接输入 `yes` / `同意` / `继续` 或 `no` / `不同意` / `拒绝`。

---

## 工作流校验

校验所有配置了 workflow 的 skill 对应的 YAML 是否合法、工具是否存在。

```powershell
.\run_me.ps1 --env dev validate-workflows

# 别名
.\run_me.ps1 --env dev validate-workflow
.\run_me.ps1 --env dev workflow-validate
```

普通输出会给出每个 workflow 的来源文件、必填输入、错误列表和可操作提示。校验失败时先看这段摘要，再按提示打开对应 `workflow.yaml` 或 `SKILL.md`。

JSON 输出：

```powershell
.\run_me.ps1 --env dev --json validate-workflows
```

---

## 离线模式

离线模式跳过所有 LLM 调用，返回本地运行结果。

```powershell
.\run_me.ps1 --env dev --offline "hello"
```

> 当没有配置 API key 时，离线模式会自动生效。

---

## 常用 flag 速查

| Flag | CLI 示例 | 说明 |
|------|----------|------|
| `--env dev` | `.\run_me.ps1 --env dev "hi"` | 选择 env 配置文件 |
| `--serve` | `.\run_me.ps1 --env dev --serve` | 启动 HTTP 服务 |
| `--offline` | `.\run_me.ps1 --env dev --offline "hi"` | 离线模式 |
| `--auto-approve` | `.\run_me.ps1 --env dev --auto-approve "..."` | 自动批准高风险中断 |
| `--json` | `.\run_me.ps1 --env dev --json "hi"` | 输出完整 RuntimeResult |
| `--prompt` / `-p` | `.\run_me.ps1 --env dev -p "hi"` | 用 flag 传 prompt |

---

## cmd.exe 一次性调用示例

如果你必须在 `cmd.exe` 中运行，可以用 `powershell -ExecutionPolicy Bypass -File`：

```cmd
powershell -ExecutionPolicy Bypass -File .\run_me.ps1 --env dev "你好"
```

```cmd
powershell -ExecutionPolicy Bypass -File .\run_me.ps1 --env dev recommend
```

```cmd
powershell -ExecutionPolicy Bypass -File .\run_me.ps1 --env dev --auto-approve "用 bash 查看当前时间"
```

```cmd
powershell -ExecutionPolicy Bypass -File .\run_me.ps1 --env dev serve
```
