from __future__ import annotations

from zgraph.config import Settings
from zgraph.core.tokenizer.base import BaseTokenizer
from zgraph.core.tokenizer.rerank import RerankTokenizer
from zgraph.core.tokenizer.word import WordTokenizer


def build_tokenizer(settings: Settings) -> BaseTokenizer:
    """根据运行时配置构建并返回对应的分词器实例。
        当配置的策略为 rerank 时，返回使用远程重排序服务的 RerankTokenizer，
        否则返回基于词元的本地 WordTokenizer。
        参数:
            settings: 运行时配置对象，包含分词策略与重排序服务参数。
        返回:
            构建完成的分词器实例。
    """

    strategy = settings.tokenizer_strategy.strip().lower()
    word = WordTokenizer()
    if strategy == "rerank":
        return RerankTokenizer(
            base_url=settings.rerank_base_url,
            api_key=settings.rerank_api_key,
            model_name=settings.rerank_model_name,
            timeout=settings.rerank_timeout,
            document_char_limit=settings.rerank_document_char_limit,
            batch_size=settings.rerank_batch_size,
            fallback=word,
        )
    return word
