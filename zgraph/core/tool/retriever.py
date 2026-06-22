from __future__ import annotations

from dataclasses import dataclass

from zgraph.core.register import Registry
from zgraph.core.tokenizer.base import BaseTokenizer, RankingDocument
from zgraph.core.tokenizer.word import WordTokenizer
from zgraph.core.tool.base import RuntimeTool


@dataclass(slots=True)
class ToolMatch:

    """工具匹配。"""
    tool: RuntimeTool
    score: float
    reasons: list[str]


class ToolRetriever:

    """工具检索器。"""
    def __init__(self, registry: Registry[RuntimeTool], tokenizer: BaseTokenizer | None = None) -> None:
        """初始化实例属性。
            参数:
                registry: 注册表（Registry[RuntimeTool]）
                tokenizer: 分词器，可选，默认为 None（BaseTokenizer | None）
            """

        self.registry = registry
        self.tokenizer = tokenizer or WordTokenizer()

    def search(self, query: str, *, top_k: int = 4, min_score: float = 0.0) -> list[ToolMatch]:
        """搜索。
            参数:
                query: query（str）
            返回:
                返回类型为 list[ToolMatch] 的结果
            """

        documents = [
            RankingDocument(
                id=tool.name,
                text=" ".join([tool.name, tool.description, " ".join(tool.tags)]),
                metadata={"tool": tool},
            )
            for tool in self.registry.values()
            if getattr(tool, "retrievable", True)
        ]
        ranked = self.tokenizer.rank(query, documents, top_k=top_k, min_score=min_score)
        return [
            ToolMatch(
                tool=result.metadata["tool"],
                score=result.score,
                reasons=result.reasons,
            )
            for result in ranked
            if "tool" in result.metadata
        ]

    def by_names(self, names: list[str]) -> list[RuntimeTool]:
        """bynames。
            参数:
                names: names（list[str]）
            返回:
                返回类型为 list[RuntimeTool] 的结果
            """

        selected: list[RuntimeTool] = []
        for name in names:
            tool = self.registry.get(name)
            if tool is not None and tool not in selected:
                selected.append(tool)
        return selected
