"""
SafeChat 健康檢查 — LLM、向量資料庫、磁碟三項狀態。

將原本分散在 main.py 與 rag_engine.py 的健康檢查邏輯集中於此。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .rag_engine import RAGEngine

import httpx

from .config import Settings
from .types import HealthComponent, HealthResult

logger = logging.getLogger(__name__)


def check_llm(settings: Settings) -> HealthComponent:
    """透過輕量 GET /api/tags 檢查 Ollama 服務是否可用。"""
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        loaded = [m["name"] for m in resp.json().get("models", [])]
        model_loaded = any(settings.ollama_model in m for m in loaded)
        return HealthComponent(
            ok=model_loaded,
            backend="ollama",
            model=settings.ollama_model,
        )
    except Exception as e:
        logger.warning("LLM health check failed: %s", e)
        return HealthComponent(
            ok=False,
            backend="ollama",
            model=settings.ollama_model,
            error="連線失敗",
        )


def check_vector(engine: RAGEngine) -> HealthComponent:
    """檢查向量資料庫（ChromaDB）是否正常運作。"""
    try:
        count = engine.collection_count()
        return HealthComponent(ok=True, chunks=count)
    except Exception as e:
        logger.error("Vector DB health check failed: %s", e)
        return HealthComponent(ok=False, error="向量資料庫異常")


def check_disk(upload_dir: Path) -> HealthComponent:
    """檢查上傳目錄所在磁碟的可用空間，低於 100 MB 時標記為異常。"""
    try:
        stat = shutil.disk_usage(upload_dir)
        free_mb = stat.free // (1024 * 1024)
        return HealthComponent(ok=free_mb > 100, free_mb=free_mb)
    except Exception as e:
        logger.error("Disk health check failed: %s", e)
        return HealthComponent(ok=False, error="磁碟檢查異常")


def aggregate(settings: Settings, engine: RAGEngine, upload_dir: Path) -> HealthResult:
    """彙整 LLM、向量資料庫、磁碟三項狀態，回傳統一格式。"""
    llm_status = check_llm(settings)
    vector_status = check_vector(engine)
    disk_status = check_disk(upload_dir)

    overall_ok = llm_status["ok"] and vector_status["ok"] and disk_status["ok"]
    return HealthResult(
        status="ok" if overall_ok else "degraded",
        components={
            "llm": llm_status,
            "vector": vector_status,
            "disk": disk_status,
        },
    )
