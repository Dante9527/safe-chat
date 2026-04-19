"""SafeChat 型別定義 — TypedDict 與 Protocol，消除 Any。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# LLM 介面
# ---------------------------------------------------------------------------
class LLMProtocol(Protocol):
    """LLM 客戶端需實作的最小介面（invoke + stream）。"""

    def invoke(self, messages: list[object]) -> object: ...
    def stream(self, messages: list[object]) -> Iterator[object]: ...


# ---------------------------------------------------------------------------
# RAG 引擎
# ---------------------------------------------------------------------------
class ArticleChunk(TypedDict):
    """條文切分後的單一區塊。"""

    text: str
    article: str
    chapter: str


class ChunkMeta(TypedDict):
    """向量資料庫中每個 chunk 的 metadata。"""

    source: str
    page: int
    chunk_index: int
    article: str
    chapter: str


class SourceSummary(TypedDict):
    """前端顯示用的來源摘要。"""

    source: str
    page: int
    relevance: float
    excerpt: str


class QueryResult(TypedDict):
    """RAG 問答結果。"""

    answer: str
    sources: list[SourceSummary]
    question: str
    degraded: bool


class StreamEvent(TypedDict):
    """SSE 串流事件。"""

    event: str
    data: dict[str, object]


# ---------------------------------------------------------------------------
# 健康檢查
# ---------------------------------------------------------------------------
class _HealthComponentOptional(TypedDict, total=False):
    backend: str
    model: str
    chunks: int
    free_mb: int
    error: str


class HealthComponent(_HealthComponentOptional):
    """單一健康檢查元件結果 — ok 必填，其餘依元件不同而異。"""

    ok: bool


class HealthResult(TypedDict):
    """彙整後的系統健康狀態。"""

    status: str
    components: dict[str, HealthComponent]


# ---------------------------------------------------------------------------
# API 回應
# ---------------------------------------------------------------------------
class KBStats(TypedDict):
    """知識庫統計資料。"""

    total_chunks: int
    documents: list[str]
