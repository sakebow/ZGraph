---
name: exam-plan
description: 通过严格工作流创建完整考试计划：题库 -> 试题 -> 试卷 -> 导入 -> 发布 -> 计划。
homepage: https://zbzn-vue-saas2-dev.cnzbai.com/
icon: https://zbzn-vue-saas2-dev.cnzbai.com/favicon.ico
required_tools: adapter.call, datetime
validations: strict-workflow
tags: exam, plan, create, paper, import, database, publish, high-risk, workflow, adapter
workflow: workflow
workflow_mode: strict
---

# 创建考试计划

这个技能必须通过本 app 内的 `workflow.yaml` 执行，不允许由单个 agent 在运行中自由决定步骤。

## 架构边界

- `workflow.yaml` 声明业务顺序、依赖、输入和断言。
- `adapters.yaml` 声明业务 API 路径、认证、请求体、响应归一化、输入校验和错误解释。
- 框架只提供 `adapter.call` 通用工具，不包含考试、ZBZN 或题库等业务代码。
- workflow 不直接调用 `curl`、`bash` 或底层 HTTP URL。

## 固定顺序

1. 创建题库，输出 `BANK_ID`。
2. 创建试题，输出 `QUESTION_ID`。
3. 创建试卷，输出 `PAPER_ID`。
4. 导入试题到试卷。
5. 发布试卷。
6. 创建考试计划，输出 `PLAN_ID`。

任一步失败、关键 ID 缺失或断言失败，都必须立即停止。

## 日期时间

凡是涉及今天、当前时间、发布日期、考试时间等相对日期时间时，必须先调用 `datetime` 工具获取，不要根据模型上下文猜测，也不要使用 shell 命令获取日期。

## 自动补全

允许通过 `fix` workflow 补全题库名、题干、试题选项、试卷名、考试计划名等内容型输入。单选题选项必须作为 `questionOptions` 列表补全，至少 2 个选项，且只能有 1 个正确答案。

`storeId` 和 `storeName` 属于外部系统实体信息；如果用户没有给出，当前 workflow 使用配置中声明的默认占位值。后续更稳的方向是增加门店查询 app/adapter，而不是让模型凭空猜门店实体。
