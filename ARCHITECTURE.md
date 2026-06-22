# ZGraph 架构图

本文档用 Mermaid 流程图描述 ZGraph 的整体架构与运行时数据流。

> 生成时间：2026-06-13
> 基于 `zgraph/` 下全部源码绘制。

## 1. 总体运行时流程

```mermaid
flowchart TB
    subgraph Entry["入口"]
        CLI["main.run_cli<br/>命令行交互 / 一次性 prompt"]
        HTTP["ZGraphHttpHandler<br/>POST /v1/chat/completions"]
        REC["GET/POST<br/>/v1/recommendations"]
    end

    subgraph Config["配置"]
        ENV["env/*.env + 环境变量"]
        SET["Settings.from_env()<br/>zgraph/config.py"]
    end

    subgraph Layer["输入/输出层<br/>zgraph/layer"]
        IN["CompletionsInputLayer<br/>CliInputLayer"]
        OUT["CompletionsGenerateOutputLayer<br/>CompletionsStreamOutputLayer"]
    end

    subgraph Runtime["运行时核心<br/>zgraph/runtime.py"]
        RUN["ZGraphRuntime.run(user_input)"]
        MW["MiddlewareChain<br/>Exception → RateLimit → Logger"]
        PIPE["_run_unprotected"]
    end

    subgraph Pipeline["主流水线"]
        IW["IntentWorkflow<br/>生成 hint / intent / todo"]
        CC["CapabilityCompiler<br/>zgraph/capability.py"]
        GW["Guardian Workflows<br/>validate → risk → approve"]
        EX["_execute<br/>配置 workflow /<br/>临时 workflow /<br/>AgentManager /<br/>offline"]
    end

    subgraph Data["持久化"]
        MEM["MemorySaver<br/>.zgraph/data/memory.jsonl"]
        AUD["Audit<br/>.zgraph/runs/{run_id}/logs/audit.json"]
        ART["Artifacts/Outputs<br/>.zgraph/runs/{run_id}"]
    end

    CLI --> SET
    HTTP --> IN --> RUN
    REC --> RUN
    ENV --> SET
    SET --> RUN
    RUN --> MW --> PIPE
    PIPE --> IW --> CC
    CC --> GW
    GW -->|高风险 + 未自动批准| INT["返回 interrupted"]
    GW -->|低风险 / 自动批准| EX
    EX --> ART
    EX --> MEM
    EX --> AUD
    EX --> OUT
    CLI --> OUT
```

## 2. 组件依赖全景

```mermaid
flowchart TB
    subgraph Entry["入口"]
        main["main.py"]
    end

    subgraph ConfigModule["配置"]
        cfg["zgraph/config.py<br/>Settings"]
    end

    subgraph LayerModule["层适配器<br/>zgraph/layer"]
        layer_in["input.py<br/>Cli/Completions"]
        layer_out["output.py<br/>Generate/Stream"]
        layer_evt["event.py"]
    end

    subgraph RuntimeModule["运行时"]
        runtime["zgraph/runtime.py<br/>ZGraphRuntime"]
        result["RuntimeResult"]
    end

    subgraph MiddlewareModule["中间件<br/>zgraph/middleware"]
        mw_chain["MiddlewareChain"]
        mw_exc["ExceptionMiddleware"]
        mw_rate["RateLimitMiddleware"]
        mw_log["LoggerMiddleware"]
    end

    subgraph WorkspaceModule["工作区<br/>zgraph/workspace.py"]
        wm["WorkspaceManager"]
        rw["RunWorkspace<br/>tmp/artifacts/outputs/logs"]
    end

    subgraph CapabilityModule["能力编译<br/>zgraph/capability.py"]
        cc["CapabilityCompiler"]
    end

    subgraph TokenizerModule["分词/检索<br/>zgraph/core/tokenizer"]
        tok_svc["build_tokenizer"]
        tok_word["WordTokenizer"]
        tok_rerank["RerankTokenizer"]
    end

    subgraph SkillsModule["技能<br/>zgraph/core/skills"]
        sk_load["SkillLoader"]
        sk_res["SkillResearcher"]
        sk_files[".zgraph/skills/**/*.SKILL.md"]
    end

    subgraph ToolsModule["工具<br/>zgraph/core/tool"]
        tb["build_default_tool_registry"]
        reg["Registry[RuntimeTool]"]
        t_ret["ToolRetriever"]
        t_impl["Read/Write/Update/Delete<br/>Glob/Bash/DateTime<br/>Todo/Interrupt/Spawn"]
    end

    subgraph WorkflowsModule["工作流<br/>zgraph/workflow"]
        wf_base["base.py<br/>Workflow Protocol"]
        wf_intent["service/intent.py"]
        wf_rec["service/recommend.py"]
        wf_val["guardian/validate.py"]
        wf_risk["guardian/risk.py"]
        wf_app["guardian/approve.py"]
        wf_spec["spec.py<br/>WorkflowSpec / validate"]
        wf_plan["planner.py<br/>TemporaryWorkflowPlanner<br/>TemporaryWorkflowReviewer"]
        wf_exec["executor.py<br/>WorkflowExecutor"]
    end

    subgraph ProviderModule["模型提供方"]
        provider["zgraph/core/provider.py<br/>build_chat_model"]
    end

    subgraph AgentModule["Agent<br/>zgraph/core/agent"]
        am["AgentManager"]
        af["AgentFactory"]
        ar["AgentRunner"]
        ah["AgentHandle"]
        ct["CancellationToken"]
    end

    subgraph MemoryModule["记忆<br/>zgraph/core/memory"]
        ml["MemoryLoader"]
        mc["MemoryCompressor"]
        jms["JsonlMemorySaver"]
        mem_file["memory.jsonl"]
    end

    main --> cfg
    main --> runtime
    main --> layer_in
    main --> layer_out
    runtime --> cfg
    runtime --> wm --> rw
    runtime --> mw_chain
    mw_chain --> mw_exc
    mw_chain --> mw_rate
    mw_chain --> mw_log
    runtime --> sk_load
    sk_load --> sk_files
    runtime --> tb
    tb --> reg
    reg --> t_impl
    t_impl --> rw
    runtime --> wf_intent
    wf_intent --> provider
    runtime --> cc
    cc --> tok_svc
    tok_svc --> tok_word
    tok_svc --> tok_rerank
    cc --> sk_res
    cc --> t_ret
    sk_res --> tok_svc
    t_ret --> tok_svc
    runtime --> wf_val
    runtime --> wf_risk
    runtime --> wf_app
    wf_risk --> t_impl
    runtime --> am
    am --> af
    am --> ar
    am --> ah
    ar --> ct
    af --> provider
    runtime --> ml
    runtime --> mc
    runtime --> jms
    ml --> mem_file
    jms --> mem_file
    runtime --> result
    result --> layer_out
    wf_rec --> ml
    wf_rec --> provider
```

## 3. 主运行时序列图

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户/客户端
    participant M as main.py
    participant R as ZGraphRuntime
    participant MW as MiddlewareChain
    participant W as RunWorkspace
    participant IW as IntentWorkflow
    participant CC as CapabilityCompiler
    participant GW as Guardian
    participant AM as AgentManager
    participant LLM as 大模型服务
    participant MEM as MemorySaver
    participant AUD as Audit

    U->>M: prompt / chat/completions
    M->>R: runtime.run(user_input)
    R->>W: WorkspaceManager.create_run()
    R->>MW: _run_unprotected
    MW->>R: execute

    R->>R: build ToolContext + ToolRegistry
    R->>R: SkillLoader.load()

    R->>IW: run(state)
    alt online
        IW->>LLM: structured/json intent
        LLM-->>IW: hint/intent/todo
    else offline / failure
        IW->>IW: _fallback(word tokenizer)
    end
    IW-->>R: WorkflowResult

    R->>CC: compile(state)
    CC->>CC: SkillResearcher.search
    CC->>CC: ToolRetriever.search
    CC-->>R: capabilities

    alt recommend_questions
        R->>wf_rec: RecommendQuestionsWorkflow
        wf_rec-->>R: result
    else normal
        alt risk in {medium, high}
            R->>GW: ValidateWorkflow
            GW-->>R: ok / fail
            R->>GW: RiskWorkflow
            R->>GW: ApproveWorkflow
            alt high + not auto_approve
                GW-->>R: interrupt
                R-->>M: status=interrupted
            end
        end

        R->>R: _execute
        alt temporary workflow required
            R->>R: _execute_temporary_workflow
            alt configured workflow found
                R->>R: _execute_configured_workflow
            else plan + review + execute
                R->>LLM: TemporaryWorkflowPlanner
                LLM-->>R: workflow_yaml
                R->>R: validate_workflow_spec
                R->>LLM: TemporaryWorkflowReviewer
                LLM-->>R: approved / corrected_yaml
                R->>R: WorkflowExecutor.run
                R-->>R: WorkflowExecutionResult
            end
        else offline / no api_key
            R->>R: _offline_execute
        else agent fallback
            R->>AM: AgentManager.run
            AM->>LLM: create_agent + invoke
            LLM-->>AM: final answer
            AM-->>R: content
        end

        R->>MEM: save memory
        R->>AUD: write audit
    end

    R-->>M: RuntimeResult
    M-->>U: response
```

## 4. 能力编译（Capability Compiler）细节

```mermaid
flowchart LR
    subgraph Input["输入"]
        UI["user_input"]
        H["state.hint"]
        I["state.intent"]
    end

    CC["CapabilityCompiler.compile"]

    subgraph Skills["技能选择"]
        SK_QUERY["query = user_input + summary + keywords + intent"]
        SK_SR["SkillResearcher.search"]
        SK_SEL["selected_skills"]
    end

    subgraph Tools["工具选择"]
        TQ["query"]
        TR["ToolRetriever.search"]
        CAND["candidate_tools<br/>来自 hint"]
        REQ["required_tools<br/>来自 skill"]
        DEDUP["去重 + 兜底 read"]
    end

    subgraph Output["输出 capabilities"]
        CAP["selected_skills<br/>selected_tools<br/>required_tools<br/>selected_workflows<br/>preconditions<br/>validations<br/>risk_level<br/>spawn_required<br/>retrieval_strategy"]
    end

    UI & H & I --> CC
    CC --> SK_QUERY --> SK_SR --> SK_SEL
    CC --> TQ --> TR
    CAND & REQ --> DEDUP
    TR --> DEDUP
    SK_SEL & DEDUP --> CAP
```

## 5. Guardian 风险审批链

```mermaid
flowchart TB
    START["capabilities.risk_level"] -->|low| SKIP["跳过 Guardian"]
    START -->|medium / high| VAL["ValidateWorkflow<br/>检查必要字段"]
    VAL -->|失败| FAIL["返回 failed"]
    VAL -->|通过| RISK["RiskWorkflow<br/>根据选中工具重新定级"]
    RISK --> APPR["ApproveWorkflow"]
    APPR -->|low/medium| AUTO["自动批准"]
    APPR -->|high + auto_approve=true| AUTO2["自动批准"]
    APPR -->|high + auto_approve=false| INT["status=interrupted<br/>等待 resume_interrupted"]
    AUTO --> EXEC["继续执行"]
    AUTO2 --> EXEC
    SKIP --> EXEC
```

## 6. 工具执行与 Agent 调用

```mermaid
flowchart TB
    subgraph Registry["工具注册"]
        B["build_default_tool_registry"]
        T["RuntimeTool 实例"]
        L["to_langchain_tool<br/>StructuredTool"]
    end

    subgraph Agent["Agent 执行"]
        AM["AgentManager.run"]
        AF["AgentFactory.create<br/>langchain.agents.create_agent"]
        AR["AgentRunner.run<br/>写入 conversation.json"]
    end

    subgraph Offline["离线模式"]
        OFF["_offline_execute<br/>写入 runtime-result.json"]
    end

    B --> T --> L --> AF
    AF --> AR
    AM --> AF
    AM --> AR

    runtime["runtime._execute"] -->|online| AM
    runtime -->|offline / 无 api_key| OFF
```

## 7. 工作区目录结构

```mermaid
flowchart LR
    ROOT[".zgraph/runs/{run_id}"] --> tmp["tmp/"]
    ROOT --> artifacts["artifacts/"]
    ROOT --> outputs["outputs/"]
    ROOT --> logs["logs/"]
    ROOT --> drafts["drafts/"]
    ROOT --> scripts["scripts/"]
    logs --> audit["audit.json"]
    logs --> conv["conversation.json"]
```

## 8. Workflow 引擎（已接入主 runtime）

项目里实现并接入了一套完整的 Workflow 规划/审查/执行能力，作为 `AgentManager` 之外的确定性执行路径。

```mermaid
flowchart LR
    subgraph Spec["规格层"]
        SP["WorkflowSpec<br/>WorkflowStepSpec"]
        VAL["validate_workflow_spec"]
        PAR["parse_workflow_text"]
    end

    subgraph Plan["规划层"]
        PL["TemporaryWorkflowPlanner<br/>LLM 生成 workflow.yaml"]
        RV["TemporaryWorkflowReviewer<br/>LLM 审查顺序与依赖"]
    end

    subgraph Exec["执行层"]
        EX["WorkflowExecutor<br/>顺序执行 step"]
        REC["WorkflowExecutionResult"]
    end

    PAR --> SP --> VAL
    PL --> SP
    RV --> SP
    SP --> EX --> REC
    EX -->|调用| ToolRegistry["ToolRegistry"]
```

> 说明：
> - `WorkflowExecutor` 支持变量传递（`outputs`）、`needs` 依赖、`assert` 断言、`retries` 重试。
> - `TemporaryWorkflowPlanner/Reviewer` 利用 LLM 生成并审查 workflow。
> - `WorkflowRegistry` 根据 skill 的 `workflow:` / `workflow_mode: strict` / `strict-workflow` validation / `workflow` tag 查找对应的 `workflow.yaml`。
> - `WorkflowSlotResolver` 从用户输入、`state.hint.slots` 和 LLM 提取 workflow 输入槽位。
> - 在 `_execute` 中，如果 `capabilities.selected_workflows` 包含 `temporary_workflow`，则优先走 workflow 路径：先尝试 `configured workflow`，找不到则 `plan → review → execute`；否则才回退到 `AgentManager`。

## 9. 关键结论

1. **主数据流**：`入口 → Settings → ZGraphRuntime → Middleware → Intent → Capability → Guardian → Execute → Memory/Audit → Output`。
2. **执行路径二选一**：在线时调用 `AgentManager`（LLM 自主选工具）；离线时只生成静态结果。
3. **高风险控制**：`bash` / `delete` 会触发 `high` 风险；默认需要显式批准，`--auto-approve` 可自动过。
4. **有两套 I/O 层**：`zgraph/layer` 正在使用；`zgraph/core/adapter` 已定义但当前未接入。
5. **Workflow 引擎已启用**：`planner.py` / `executor.py` / `spec.py` / `registry.py` / `slots.py` 已接入 `runtime.py`。当能力编译器标记 `temporary_workflow` 或 skill 声明了配置 workflow 时，runtime 会优先进行确定性 workflow 执行，而不是让 LLM agent 自主选工具。
