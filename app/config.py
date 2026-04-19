"""
SafeChat 設定 — .env 唯一權威來源。

所有環境變數的讀取集中於此模組，其他模組一律透過 Settings 存取設定值，
不得直接呼叫 os.getenv()。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """應用程式設定（不可變）。欄位對應 .env.example 中的環境變數。"""

    # LLM / Ollama
    ollama_model: str
    ollama_base_url: str
    ollama_num_ctx: int
    llm_temperature: float

    # RAG
    embedding_model: str
    rag_top_k: int
    rag_prompt_chunk_chars: int
    max_chapter_articles: int
    collection_name: str
    chunk_size: int
    chunk_overlap: int

    # Security
    admin_token: str
    api_key: str
    max_upload_mb: int
    cors_origins: tuple[str, ...]
    rate_limit: str

    def __post_init__(self) -> None:
        if self.max_chapter_articles < 1:
            raise ValueError(
                f"MAX_CHAPTER_ARTICLES={self.max_chapter_articles} 必須 >= 1"
            )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        """從環境變數建立 Settings 實例。預設值與 .env.example 一致。"""
        return cls(
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_num_ctx=_env_int("OLLAMA_NUM_CTX", "8192"),
            llm_temperature=_env_float("LLM_TEMPERATURE", "0.1"),
            embedding_model=os.getenv(
                "EMBEDDING_MODEL", "intfloat/multilingual-e5-large-instruct"
            ),
            rag_top_k=_env_int("RAG_TOP_K", "5"),
            rag_prompt_chunk_chars=_env_int("RAG_PROMPT_CHUNK_CHARS", "600"),
            max_chapter_articles=_env_int("MAX_CHAPTER_ARTICLES", "8"),
            collection_name="construction_safety",
            chunk_size=600,
            chunk_overlap=120,
            admin_token=os.getenv("ADMIN_TOKEN", ""),
            api_key=os.getenv("API_KEY", ""),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", "20"),
            cors_origins=tuple(
                o for o in os.getenv("CORS_ORIGINS", "").split(",") if o
            ),
            rate_limit=os.getenv("RATE_LIMIT", "30/minute"),
        )


def _env_int(name: str, default: str) -> int:
    """讀取環境變數並轉為 int，轉換失敗時提供明確錯誤訊息。"""
    val = os.getenv(name, default)
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"環境變數 {name}={val!r} 必須是整數") from None


def _env_float(name: str, default: str) -> float:
    """讀取環境變數並轉為 float，轉換失敗時提供明確錯誤訊息。"""
    val = os.getenv(name, default)
    try:
        return float(val)
    except ValueError:
        raise ValueError(f"環境變數 {name}={val!r} 必須是數字") from None


def setup_logging() -> None:
    """統一設定根 logger 格式。啟動時呼叫一次。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    # ChromaDB posthog 套件有已知 bug，即使停用遙測仍會輸出無害錯誤，直接靜默
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)


_settings: Settings | None = None


def init_settings() -> Settings:
    """讀取 .env 並初始化全域 Settings singleton。"""
    global _settings
    _settings = Settings.from_env()
    return _settings


def get_settings() -> Settings:
    """取得已初始化的 Settings 實例。"""
    if _settings is None:
        raise RuntimeError("Settings not initialized — call init_settings() first")
    return _settings
