from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from zgraph.core.tokenizer.base import BaseTokenizer, RankingDocument, RankingResult
from zgraph.core.tokenizer.word import WordTokenizer


class RerankTokenizer(BaseTokenizer):
    """基于远程重排序服务的分词器实现"""

    name = "rerank"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: int = 30,
        document_char_limit: int = 240,
        batch_size: int = 2,
        fallback: BaseTokenizer | None = None,
    ) -> None:
        """初始化新的 RerankTokenizer 实例。

            参数:
                base_url: 重排序服务的基础 URL。
                api_key: 用于认证的 API 密钥。
                model_name: 调用的模型名称。
                timeout: 单次请求超时时间，单位为秒。
                document_char_limit: 每篇文档截断后的最大字符数，至少为 80。
                batch_size: 每批提交给远程服务的文档数量，至少为 1。
                fallback: 远程服务不可用时使用的回退分词器，默认使用 WordTokenizer。

        """

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.timeout = timeout
        self.document_char_limit = max(document_char_limit, 80)
        self.batch_size = max(batch_size, 1)
        self.fallback = fallback or WordTokenizer()
        self.logger = logging.getLogger("zgraph.rerank")

    def tokenize(self, text: str) -> set[str]:
        """对输入文本进行分词并返回词元集合。
            重排序分词器本身不进行本地分词，而是委托给回退分词器处理。
            参数:
                text: 待分词的输入文本。
            返回:
                分词结果集合
        """
        return self.fallback.tokenize(text)

    def rank(
        self,
        query: str,
        documents: list[RankingDocument],
        *,
        top_k: int,
        min_score: float,
    ) -> list[RankingResult]:
        """根据查询对文档进行打分并返回排序后的结果。
            首先尝试调用远程重排序服务；若缺少必要配置或远程调用失败，
            则回退到本地词元分词器。最后将本地结果与远程结果合并，
            按得分阈值和 top_k 过滤后返回。
            参数:
                query: 查询文本。
                documents: 待排序的文档列表。
                top_k: 返回结果的最大数量。
                min_score: 返回结果的最小得分阈值。
            返回:
                按得分降序排列的结果列表。
        """

        if not self.base_url or not self.model_name or not documents:
            fallback_results = self.fallback.rank(query, documents, top_k=top_k, min_score=min_score)
            self.logger.info(
                "stage=rerank:fallback reason=missing_config docs=%s top_k=%s min_score=%.3f hits=%s",
                len(documents),
                top_k,
                min_score,
                _format_hits(fallback_results),
            )
            return fallback_results

        started = time.perf_counter()
        self.logger.info(
            "stage=rerank:start docs=%s top_k=%s min_score=%.3f timeout=%s batch_size=%s",
            len(documents),
            top_k,
            min_score,
            self.timeout,
            self.batch_size,
        )
        local_results = self.fallback.rank(query, documents, top_k=len(documents), min_score=0.0)
        try:
            remote_results = self._rank_remote_batched(query, documents, top_k=len(documents))
        except Exception as exc:
            fallback_results = self.fallback.rank(query, documents, top_k=top_k, min_score=min_score)
            self.logger.warning(
                "stage=rerank:error elapsed_ms=%.2f error=%s fallback=word hits=%s",
                (time.perf_counter() - started) * 1000,
                exc,
                _format_hits(fallback_results),
            )
            return fallback_results

        merged: dict[str, RankingResult] = {}
        for result in remote_results:
            merged[result.id] = result

        for result in local_results:
            existing = merged.get(result.id)
            if existing is None:
                merged[result.id] = result
                continue
            if result.score > existing.score:
                existing.score = result.score
            existing.reasons = list(dict.fromkeys([*existing.reasons, *result.reasons]))

        filtered = [result for result in merged.values() if result.score >= min_score]
        filtered.sort(key=lambda item: item.score, reverse=True)
        hits = filtered[:top_k]
        self.logger.info(
            "stage=rerank:end elapsed_ms=%.2f remote=%s local=%s returned=%s hits=%s",
            (time.perf_counter() - started) * 1000,
            len(remote_results),
            len(local_results),
            len(hits),
            _format_hits(hits),
        )
        return hits

    def _rank_remote_batched(
        self,
        query: str,
        documents: list[RankingDocument],
        *,
        top_k: int,
    ) -> list[RankingResult]:
        """分批调用远程重排序服务并对文档打分。
            参数:
                query: 查询文本。
                documents: 待排序的文档列表。
                top_k: 每批返回结果的最大数量。
            返回:
                远程服务返回的排序结果列表。
        """

        clipped = [
            RankingDocument(
                id=document.id,
                text=self._clip_document(document.text),
                metadata=document.metadata,
            )
            for document in documents
        ]
        results: list[RankingResult] = []
        for index in range(0, len(clipped), self.batch_size):
            batch = clipped[index : index + self.batch_size]
            if not batch:
                continue
            batch_index = index // self.batch_size
            self.logger.info(
                "stage=rerank.batch:start index=%s docs=%s",
                batch_index,
                len(batch),
            )
            results.extend(self._rank_remote(query, batch, top_k=min(top_k, len(batch))))
            self.logger.info(
                "stage=rerank.batch:end index=%s cumulative=%s",
                batch_index,
                len(results),
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def _rank_remote(
        self,
        query: str,
        documents: list[RankingDocument],
        *,
        top_k: int,
    ) -> list[RankingResult]:
        """向远程重排序服务发送单次请求并返回排序结果。
            参数:
                query: 查询文本。
                documents: 待排序的文档列表。
                top_k: 返回结果的最大数量。
            返回:
                远程服务返回的排序结果列表。
            异常:
                RuntimeError: 当 HTTP 请求返回错误状态码时抛出。
        """

        payload = {
            "model": self.model_name,
            "query": query,
            "documents": [document.text for document in documents],
            "top_n": min(top_k, len(documents)),
        }
        started = time.perf_counter()
        self.logger.info(
            "stage=rerank.remote:start url=%s docs=%s top_n=%s",
            f"{self.base_url}/rerank",
            len(documents),
            payload["top_n"],
        )
        request = urllib.request.Request(
            f"{self.base_url}/rerank",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        self.logger.info(
            "stage=rerank.remote:end elapsed_ms=%.2f bytes=%s",
            (time.perf_counter() - started) * 1000,
            len(raw),
        )
        return self._parse_response(json.loads(raw), documents)

    def _headers(self) -> dict[str, str]:
        """构造远程请求的请求头。
            返回:
                包含 Content-Type 与可选 Authorization 的请求头字典。

        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _parse_response(self, payload: object, documents: list[RankingDocument]) -> list[RankingResult]:
        """解析远程重排序服务返回的响应数据。
            参数:
                payload: 远程服务返回的原始响应对象。
                documents: 请求时提交的文档列表。
            返回:
                解析后的排序结果列表。
        """
        if isinstance(payload, dict):
            raw_results = payload.get("results") or payload.get("data") or payload.get("rankings") or []
        elif isinstance(payload, list):
            raw_results = payload
        else:
            raw_results = []

        results: list[RankingResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if index is None:
                index = item.get("document_index")
            try:
                document = documents[int(index)]
            except (TypeError, ValueError, IndexError):
                continue

            score = item.get("relevance_score")
            if score is None:
                score = item.get("score")
            if score is None:
                score = item.get("similarity")
            try:
                numeric_score = float(score)
            except (TypeError, ValueError):
                numeric_score = 0.0

            results.append(
                RankingResult(
                    id=document.id,
                    score=numeric_score,
                    reasons=["rerank"],
                    metadata=document.metadata,
                )
            )
        return results

    def _clip_document(self, text: str) -> str:
        """将文档文本压缩并按字符长度限制截断。
            参数:
                text: 原始文档文本。
            返回:
                截断后的文档文本。
        """
        compact = " ".join(text.split())
        if len(compact) <= self.document_char_limit:
            return compact
        return compact[: self.document_char_limit]


def _format_hits(results: list[RankingResult], *, limit: int = 6) -> str:
    """将排序结果格式化为简短的可读字符串。
        参数:
            results: 排序结果列表。
            limit: 最多显示的条目数。
        返回:
            格式化后的字符串。
    """
    if not results:
        return "[]"
    items: list[str] = []
    for result in results[:limit]:
        label = result.id
        metadata = result.metadata or {}
        skill = metadata.get("skill")
        tool = metadata.get("tool")
        if skill is not None:
            label = getattr(skill, "name", label)
        elif tool is not None:
            label = getattr(tool, "name", label)
        reasons = ",".join(result.reasons)
        items.append(f"{label}:{result.score:.3f}" + (f"({reasons})" if reasons else ""))
    if len(results) > limit:
        items.append(f"...+{len(results) - limit}")
    return "[" + ", ".join(items) + "]"
