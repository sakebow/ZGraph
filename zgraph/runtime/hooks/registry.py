"""Phase 2.3：RuntimeHook 默认注册表。

设计：
- ``default_hooks()`` 返回一个按声明顺序串联的 RuntimeHook 列表，作为
  ``ZGraphRuntime`` 默认钩子链。
- 顺序约定：
  1. ``AuditHook`` — 把 Final 事件写 audit.json
  2. ``MetricsHook`` — 累计事件计数 / 长度
  3. ``PIIFilterHook`` — mask 邮箱 / 手机号 / 身份证
  4. ``GuardianHook`` — 在 Final 阶段做 risk / approve 二次校验
- 调用方可以用 ``Runtime(hooks=...)`` 整体覆盖；也可以用
  ``Runtime(hooks=[...default_hooks(), MyCustomHook()])`` 追加。

为什么独立成一个文件：
- 之前默认列表硬编码在 ``runtime/__init__.py:141-146``，散落在 Runtime 构造逻辑
  里。挪到这里之后，新增 / 移除 / 排序 默认 hook 都不用碰 Runtime 主体。
- 测试可以单独验证 ``default_hooks()`` 的内容（顺序、类型），不再需要构造
  完整的 Runtime。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zgraph.runtime.hooks import RuntimeHook


def default_hooks() -> "list[RuntimeHook]":
    """返回 Runtime 的默认钩子链（按声明顺序）。

    返回:
        ``[AuditHook, MetricsHook, PIIFilterHook, GuardianHook]`` 列表。
    """
    # 延迟导入避免循环依赖（hooks 包可能反向引用 runtime）
    from zgraph.runtime.hooks.builtin import AuditHook, MetricsHook, PIIFilterHook
    from zgraph.runtime.hooks.guardian_hook import GuardianHook

    return [
        AuditHook(),
        MetricsHook(),
        PIIFilterHook(),
        GuardianHook(),
    ]
