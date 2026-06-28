from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RankingDocument:
    """用于排序的文档对象。
        参数:
            id: 文档唯一标识符。
            text: 文档文本内容。
            metadata: 附加元数据字典。
    """

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RankingResult:
    """排序结果对象。
        参数:
            id: 对应文档的唯一标识符。
            score: 相关性得分。
            reasons: 命中的关键词或原因列表。
            metadata: 附加元数据字典。
    """

    id: str
    score: float
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTokenizer(ABC):
    """分词器抽象基类，定义 tokenize 与 rank 接口"""

    name = "base"

    @abstractmethod
    def tokenize(self, text: str) -> set[str]:
        """对输入文本进行分词并返回词元集合。
            参数:
                text: 待分词的输入文本。
            返回:
                分词结果集合。
            异常:
                NotImplementedError: 当子类未实现该方法时抛出。
        """
        raise NotImplementedError

    @abstractmethod
    def rank(
        self,
        query: str,
        documents: list[RankingDocument],
        *,
        top_k: int,
        min_score: float,
    ) -> list[RankingResult]:
        """根据查询对文档进行打分并返回排序后的结果。
            参数:
                query: 查询文本。
                documents: 待排序的文档列表。
                top_k: 返回结果的最大数量。
                min_score: 返回结果的最小得分阈值。
            返回:
                排序后的结果列表。
            异常:
                NotImplementedError: 当子类未实现该方法时抛出。
        """
        raise NotImplementedError
