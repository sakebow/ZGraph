from __future__ import annotations

import math
import re

from zgraph.core.tokenizer.base import BaseTokenizer, RankingDocument, RankingResult


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "should",
    "the",
    "to",
    "with",
    "what",
    "when",
    "where",
    "who",
    "why",
    "you",
    "your",
}


class WordTokenizer(BaseTokenizer):
    """基于词元的本地分词器实现。
    """

    name = "word"

    def tokenize(self, text: str) -> set[str]:
        """对输入文本进行分词并返回词元集合。

            英文部分会过滤停用词并保留长度合适的词元；
            CJK 字符会被拆分为单字及 N-gram。

            参数:
                text: 待分词的输入文本。

            返回:
                分词结果集合。

        """
        tokens = {
            word
            for word in re.findall(r"[\w-]+", text.lower())
            if (len(word) >= 3 or word in {"qa", "io"}) and word not in STOPWORDS
        }
        tokens.update(_cjk_tokens(text))
        return tokens

    def rank(
        self,
        query: str,
        documents: list[RankingDocument],
        *,
        top_k: int,
        min_score: float,
    ) -> list[RankingResult]:
        """根据查询与文档的词元重叠情况打分并返回排序后的结果。

            参数:
                query: 查询文本。
                documents: 待排序的文档列表。
                top_k: 返回结果的最大数量。
                min_score: 返回结果的最小得分阈值。

            返回:
                按得分降序排列的结果列表。

        """

        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        results: list[RankingResult] = []
        for document in documents:
            document_tokens = self.tokenize(document.text)
            reasons = sorted(query_tokens & document_tokens)
            if not reasons:
                continue

            score = len(reasons) / math.sqrt(max(len(query_tokens), 1))
            if score >= min_score:
                results.append(
                    RankingResult(
                        id=document.id,
                        score=score,
                        reasons=reasons,
                        metadata=document.metadata,
                    )
                )

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]


def _cjk_tokens(text: str) -> set[str]:
    """从文本中提取 CJK（中日韩）字符词元。

        对每段连续 CJK 字符生成单字及长度为 2、3、4 的 N-gram。

        参数:
            text: 待处理的输入文本。

        返回:
            CJK 词元集合。

    """
    tokens: set[str] = set()
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(char for char in segment if char.strip())
        for size in (2, 3, 4):
            if len(segment) < size:
                continue
            tokens.update(segment[index : index + size] for index in range(0, len(segment) - size + 1))
    return tokens
