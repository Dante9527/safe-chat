"""
SafeChat — AI 營建工地安全問答系統
==================================
基於檢索增強生成（RAG）的工地安全法規合規查詢系統。
上傳法規與安全手冊，即可用自然語言取得即時回答 — 專為現場施工人員設計。

技術堆疊：Python + FastAPI + LangChain + ChromaDB + Ollama
"""

import asyncio
import hmac
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from . import health as health_checks
from .config import get_settings, init_settings, setup_logging
from .rag_engine import RAGEngine
from .types import HealthResult, KBStats, SourceSummary

# 靜態檔案版本號 — 每次容器啟動時產生，強制瀏覽器載入最新版本
_STATIC_VERSION = uuid.uuid4().hex[:8]

setup_logging()
logger = logging.getLogger(__name__)

_UPLOAD_FILE = File()

# ---------------------------------------------------------------------------
# 路徑
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 設定 — 模組層級初始化（CORS、rate limiter 裝飾器需要在 import 時讀取）
# ---------------------------------------------------------------------------
settings = init_settings()

# ---------------------------------------------------------------------------
# 速率限制
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# RAG 引擎 — 背景載入，啟動期間先顯示載入頁面。
# ---------------------------------------------------------------------------
rag: RAGEngine | None = None
_rag_ready = False

_LOADING_HTML = (
    Path(__file__).resolve().parent.parent / "templates" / "loading.html"
).read_text(encoding="utf-8")


def _load_llm_background() -> None:
    """在背景 thread 載入 LLM 客戶端並驗證可達性，失敗則終止程序。"""
    global _rag_ready
    try:
        if rag is not None:
            _ = rag.llm  # 建構 ChatOllama
            result = health_checks.check_llm(get_settings())
            if not result.get("ok"):
                raise RuntimeError(
                    f"Ollama 模型 '{get_settings().ollama_model}' 不可用"
                )
            test_resp = rag.llm.invoke("ping")
            if not test_resp:
                raise RuntimeError("Ollama 模型無法產生回應")
            logger.info("LLM 推論測試通過")
        _rag_ready = True
        logger.info("RAG 引擎就緒 — 所有元件已載入，開始接受請求。")
    except Exception:
        logger.exception("LLM 載入失敗")
        import os
        os._exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """啟動時同步驗證 RAG 引擎，LLM 在背景載入。"""
    import threading
    global rag
    s = get_settings()
    logger.info("啟動中 — 預載 RAG 引擎（SentenceTransformer + ChromaDB）…")
    rag = RAGEngine(persist_dir=str(BASE_DIR / "data" / "chroma_db"), settings=s)
    threading.Thread(target=_load_llm_background, daemon=True).start()
    yield


def get_rag() -> RAGEngine:
    """取得全域 RAG 引擎實例。"""
    if not _rag_ready or rag is None:
        raise HTTPException(503, "SafeChat 正在啟動中，請稍候再試。")
    return rag


app = FastAPI(
    title="SafeChat",
    description="AI 營建工地安全法規問答系統 — 基於 RAG 技術",
    version="1.0.0",
    lifespan=lifespan,
)

# 註冊速率限制
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "請求過於頻繁，請稍後再試。"},
    )


# ---------------------------------------------------------------------------
# CORS 設定
# ---------------------------------------------------------------------------
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-Admin-Token", "Content-Type"],
    )


# ---------------------------------------------------------------------------
# 中介層 — 安全標頭
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """CSRF Origin 檢查與安全回應標頭。"""
    path = request.url.path
    if (
        path.startswith("/api/")
        and path != "/api/health"
        and request.method not in ("GET", "HEAD", "OPTIONS")
    ):
        origin = request.headers.get("origin", "")
        if origin and not origin.startswith(
            ("http://localhost:", "http://127.0.0.1:")
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "跨站請求已被拒絕。"},
            )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ---------------------------------------------------------------------------
# 資料模型
# ---------------------------------------------------------------------------
class QuestionRequest(BaseModel):
    """使用者提問請求。"""

    question: str = Field(..., min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=10)


class AnswerResponse(BaseModel):
    """回答結果，包含答案、來源與降級狀態。"""

    answer: str
    sources: list[SourceSummary]
    question: str
    degraded: bool = False


# ---------------------------------------------------------------------------
# 路由 — 頁面
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """啟動期間回傳載入頁面，就緒後回傳主介面（附加靜態資源版本號）。"""
    if not _rag_ready:
        return HTMLResponse(content=_LOADING_HTML)
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        '.css"', f'.css?v={_STATIC_VERSION}"'
    ).replace(
        '.js"', f'.js?v={_STATIC_VERSION}"'
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# 路由 — API
# ---------------------------------------------------------------------------
def _validate_upload(filename: str | None) -> str:
    """驗證上傳檔案名稱與副檔名，回傳合法的副檔名。"""
    if not filename:
        raise HTTPException(400, "Missing filename")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md"}:
        raise HTTPException(
            400, f"Unsupported file type: {suffix}. Use PDF, TXT, or MD."
        )
    return suffix


_UPLOAD_CHUNK = 64 * 1024  # 64 KB


async def _save_upload(file: UploadFile) -> Path:
    """串流寫入上傳檔案，超過大小上限立即中斷。"""
    s = get_settings()
    file_id = uuid.uuid4().hex[:8]
    safe_name = re.sub(r"[^\w\-.]", "_", Path(file.filename or "upload").name)
    dest = UPLOAD_DIR / f"{file_id}_{safe_name}"
    total = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > s.max_upload_bytes:
                    raise HTTPException(413, f"檔案過大，上限為 {s.max_upload_mb} MB。")
                f.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    logger.info("Saved upload → %s (%d bytes)", dest.name, total)
    return dest


@app.post("/api/upload")
@limiter.limit(settings.rate_limit)
async def upload_document(
    request: Request, file: UploadFile = _UPLOAD_FILE
) -> JSONResponse:
    """接收使用者上傳的文件，驗證後存檔並匯入向量資料庫。"""
    _validate_upload(file.filename)
    dest = await _save_upload(file)

    try:
        num_chunks = await asyncio.to_thread(
            get_rag().ingest_document,
            str(dest), file.filename or "",
        )
    except Exception as e:
        logger.error("Ingest failed for %s: %s", dest.name, e)
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "文件處理失敗，請確認檔案格式正確。") from e
    if num_chunks == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "檔案未包含可搜尋的文字內容。")
    return JSONResponse(
        {
            "status": "ok",
            "filename": file.filename,
            "chunks": num_chunks,
            "message": f"已成功匯入 {file.filename}，切分為 {num_chunks} 個文本區塊。",
        }
    )


@app.post("/api/ask", response_model=AnswerResponse)
@limiter.limit(settings.rate_limit)
async def ask_question(request: Request, req: QuestionRequest) -> AnswerResponse:
    """透過 RAG 回答營建安全問題。"""
    engine = get_rag()

    if engine.collection_count() == 0:
        return AnswerResponse(
            answer="⚠️ 目前知識庫為空，請先上傳工安法規或安全手冊 PDF。",
            sources=[],
            question=req.question,
        )

    result = await asyncio.to_thread(engine.query, req.question, req.top_k)
    return AnswerResponse(**result)


def _empty_kb_stream(question: str) -> Generator[str, None, None]:
    """知識庫為空時，產生提示使用者上傳文件的 SSE 串流。"""
    empty_msg = "⚠️ 目前知識庫為空，請先上傳工安法規或安全手冊 PDF。"
    payload = json.dumps(
        {
            "error": "knowledge base empty",
            "fallback_answer": empty_msg,
            "sources": [],
            "question": question,
            "degraded": False,
        },
        ensure_ascii=False,
    )
    yield f"event: error\ndata: {payload}\n\n"
    yield "event: done\ndata: {}\n\n"


def _llm_stream(
    engine: RAGEngine, question: str, top_k: int | None
) -> Generator[str, None, None]:
    """透過 LLM 串流產生回答，逐 token 以 SSE 格式送出。"""
    for item in engine.query_stream(question, top_k=top_k):
        data = json.dumps(item["data"], ensure_ascii=False)
        yield f"event: {item['event']}\ndata: {data}\n\n"


@app.post("/api/ask/stream")
@limiter.limit(settings.rate_limit)
async def ask_question_stream(
    request: Request, req: QuestionRequest
) -> StreamingResponse:
    """以 Server-Sent Events 串流方式回答工安問題。"""
    engine = get_rag()

    if engine.collection_count() == 0:
        return StreamingResponse(
            _empty_kb_stream(req.question),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        _llm_stream(engine, req.question, req.top_k),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/stats")
@limiter.limit(settings.rate_limit)
async def knowledge_base_stats(request: Request) -> KBStats:
    """回傳知識庫基本統計（文件數、區塊數）。"""
    engine = get_rag()
    return KBStats(
        total_chunks=engine.collection_count(),
        documents=engine.list_documents(),
    )


@app.get("/api/health", response_model=None)
async def health_check(request: Request) -> HealthResult | JSONResponse:
    """系統健康檢查 — 啟動期間回傳 503，就緒後回傳完整狀態。"""
    if not _rag_ready:
        return JSONResponse(
            status_code=503,
            content={"status": "starting", "message": "AI 模型載入中，請稍候…"},
        )
    return health_checks.aggregate(get_settings(), get_rag(), UPLOAD_DIR)


@app.delete("/api/reset")
@limiter.limit(settings.rate_limit)
async def reset_knowledge_base(
    request: Request,
    x_admin_token: str = Header("", alias="X-Admin-Token"),
) -> dict[str, str]:
    """清空整個向量資料庫（需管理員 token 驗證）。"""
    s = get_settings()
    if not s.admin_token:
        raise HTTPException(403, "管理員 token 未設定，此功能已停用。")
    if not hmac.compare_digest(x_admin_token, s.admin_token):
        raise HTTPException(403, "管理員 token 無效。")
    engine = get_rag()
    engine.reset()
    logger.info("Knowledge base reset by admin.")
    return {"status": "ok", "message": "知識庫已清空。"}


# ---------------------------------------------------------------------------
# 靜態檔案（CSS / JS）— 放在路由之後，避免覆蓋 API 路徑
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
