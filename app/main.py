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
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .config import init_settings, get_settings, setup_logging
from .rag_engine import RAGEngine
from . import health as health_checks

setup_logging()
logger = logging.getLogger(__name__)

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
# RAG 引擎 — 透過 lifespan 預載，避免首次請求冷啟動延遲。
# ---------------------------------------------------------------------------
rag: RAGEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動前預載所有重型元件，確保首次請求不會冷啟動。"""
    global rag
    s = get_settings()
    logger.info("啟動中 — 預載 RAG 引擎（SentenceTransformer + ChromaDB）…")
    rag = RAGEngine(persist_dir=str(BASE_DIR / "data" / "chroma_db"), settings=s)
    _ = rag.llm  # 觸發 LLM 客戶端初始化
    logger.info("RAG 引擎就緒 — 所有元件已載入，開始接受請求。")
    yield


def get_rag() -> RAGEngine:
    """取得全域 RAG 引擎實例。"""
    if rag is None:
        raise RuntimeError("RAGEngine not initialized — server is still starting up")
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
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
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
# 中介層 — API Key 驗證 + 安全標頭
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """API Key 驗證（可選）與安全回應標頭。"""
    s = get_settings()
    if s.api_key:
        path = request.url.path
        needs_auth = (
            path.startswith("/api/")
            and path != "/api/health"
            and request.method != "OPTIONS"
        )
        if needs_auth:
            auth = request.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, f"Bearer {s.api_key}"):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "未授權：請提供有效的 API Key。"},
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
    sources: list[dict]
    question: str
    degraded: bool = False


# ---------------------------------------------------------------------------
# 路由 — 頁面
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    """提供主聊天介面 HTML。"""
    return HTMLResponse(
        content=(BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    )


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


async def _save_upload(file: UploadFile) -> Path:
    """將上傳檔案寫入磁碟，回傳儲存路徑。"""
    s = get_settings()
    file_id = uuid.uuid4().hex[:8]
    safe_name = re.sub(r"[^\w\-.]", "_", Path(file.filename or "upload").name)
    dest = UPLOAD_DIR / f"{file_id}_{safe_name}"
    content = await file.read()
    if len(content) > s.max_upload_bytes:
        raise HTTPException(413, f"檔案過大，上限為 {s.max_upload_mb} MB。")
    dest.write_bytes(content)
    logger.info("Saved upload → %s (%d bytes)", dest.name, len(content))
    return dest


@app.post("/api/upload")
@limiter.limit(settings.rate_limit)
async def upload_document(request: Request, file: UploadFile = File(...)):
    """接收使用者上傳的文件，驗證後存檔並匯入向量資料庫。"""
    _validate_upload(file.filename)
    dest = await _save_upload(file)

    try:
        num_chunks = get_rag().ingest_document(str(dest), original_name=file.filename)
    except Exception as e:
        logger.error("Ingest failed for %s: %s", dest.name, e)
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "文件處理失敗，請確認檔案格式正確。")
    return JSONResponse({
        "status": "ok",
        "filename": file.filename,
        "chunks": num_chunks,
        "message": f"已成功匯入 {file.filename}，切分為 {num_chunks} 個文本區塊。",
    })


@app.post("/api/ask", response_model=AnswerResponse)
@limiter.limit(settings.rate_limit)
async def ask_question(request: Request, req: QuestionRequest):
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


def _empty_kb_stream(question: str):
    """知識庫為空時，產生提示使用者上傳文件的 SSE 串流。"""
    empty_msg = "⚠️ 目前知識庫為空，請先上傳工安法規或安全手冊 PDF。"
    payload = json.dumps({
        "error": "knowledge base empty",
        "fallback_answer": empty_msg,
        "sources": [],
        "question": question,
        "degraded": False,
    }, ensure_ascii=False)
    yield f"event: error\ndata: {payload}\n\n"
    yield "event: done\ndata: {}\n\n"


def _llm_stream(engine: RAGEngine, question: str, top_k: int | None):
    """透過 LLM 串流產生回答，逐 token 以 SSE 格式送出。"""
    for item in engine.query_stream(question, top_k=top_k):
        data = json.dumps(item["data"], ensure_ascii=False)
        yield f"event: {item['event']}\ndata: {data}\n\n"


@app.post("/api/ask/stream")
@limiter.limit(settings.rate_limit)
async def ask_question_stream(request: Request, req: QuestionRequest):
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
async def knowledge_base_stats(request: Request):
    """回傳知識庫基本統計（文件數、區塊數）。"""
    engine = get_rag()
    return {
        "total_chunks": engine.collection_count(),
        "documents": engine.list_documents(),
    }


@app.get("/api/health")
@limiter.limit(settings.rate_limit)
async def health_check(request: Request):
    """系統健康檢查 — 回傳 LLM、向量資料庫、磁碟三項狀態。"""
    return health_checks.aggregate(get_settings(), get_rag(), UPLOAD_DIR)


@app.delete("/api/reset")
@limiter.limit(settings.rate_limit)
async def reset_knowledge_base(
    request: Request,
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
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
