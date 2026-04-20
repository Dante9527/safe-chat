"""
SafeChat RAG Engine — 文件匯入、向量檢索、LLM 問答。

LLM 後端使用 Ollama 本地模型（無需 API key，適合工地離線部署）。
嵌入模型預設使用 intfloat/multilingual-e5-large-instruct（Microsoft 開源），
原生支援中文營建安全文件。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Generator
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader

from .config import Settings
from .types import (
    ArticleChunk,
    ChunkMeta,
    LLMProtocol,
    QueryResult,
    SourceSummary,
    StreamEvent,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 嵌入函式（包裝 sentence-transformers 供 ChromaDB 使用）
# ---------------------------------------------------------------------------
class SentenceTransformerEmbedding(chromadb.EmbeddingFunction):
    """將 sentence-transformers 包裝為 ChromaDB 相容的嵌入函式。

    自動偵測 E5 系列模型，為輸入文本加上必要的前綴（"query: " / "passage: "）。
    """

    _E5_INSTRUCT_TASK = (
        "Given a construction safety question in Traditional Chinese, "
        "retrieve relevant regulatory passages"
    )

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self.model = SentenceTransformer(model_name)
        name_lower = model_name.lower()
        self._is_e5 = "e5" in name_lower
        self._is_e5_instruct = self._is_e5 and "instruct" in name_lower

    def __call__(self, input: list[str]) -> list[list[float]]:
        texts = [f"passage: {t}" for t in input] if self._is_e5 else input
        embeddings = self.model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def query(self, input: list[str]) -> list[list[float]]:
        """查詢用嵌入 — E5-instruct 使用任務描述前綴，E5-base 使用 'query: ' 前綴。"""
        if self._is_e5_instruct:
            texts = [
                f"Instruct: {self._E5_INSTRUCT_TASK}\nQuery: {t}"
                for t in input
            ]
        elif self._is_e5:
            texts = [f"query: {t}" for t in input]
        else:
            texts = input
        embeddings = self.model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()


# ---------------------------------------------------------------------------
# 提示詞模板 — 營建安全領域專用
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
你是營建工地安全顧問。嚴格根據參考資料回答，禁止自行編造或修改法規內容。用繁體中文。緊急狀況優先提供處置要點。\
"""

QA_PROMPT_TEMPLATE = """\
參考資料：
{context}

---
使用者問題：{question}

回答規則（務必遵守）：
1. 只能使用參考資料中的內容回答，嚴禁編造或推測。
2. 數字、距離、尺寸必須與原文完全一致，不得四捨五入或轉換。
3. 優先引用有明確條號的法規原文；手冊或指南僅作補充，不得與法規原文矛盾。
4. 來源標註規則：
   - 來源含條號 → 格式：「依據《法規名》第 X 條，…」
   - 來源不含條號 → 僅註明文件名稱，禁止猜測條號。
5. 以條列式精簡回答，每點一個重點，不重複，不分段。
6. 只有在參考資料完全無法回答問題時，才說明「參考資料中未涵蓋此內容」。
   已列出部分要點時不得加此句。
"""


# ---------------------------------------------------------------------------
# RAG 引擎
# ---------------------------------------------------------------------------
class RAGEngine:
    """端對端 RAG 流程：匯入 → 切分 → 嵌入 → 儲存 → 檢索 → 生成。"""

    def __init__(self, persist_dir: str, settings: Settings) -> None:
        self._settings = settings
        self.persist_dir = persist_dir
        self._embed_fn = SentenceTransformerEmbedding(settings.embedding_model)
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=settings.collection_name,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self._validate_embedding_model(settings)
        self._llm: LLMProtocol | None = None
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        )
        logger.info(
            "RAGEngine ready — collection '%s' has %d chunks",
            settings.collection_name,
            self._collection.count(),
        )

    # -- LLM（延遲載入）-----------------------------------------------------
    def _build_llm(self) -> LLMProtocol:
        """建立 Ollama 本地 LLM 實例。"""
        from langchain_ollama import ChatOllama

        s = self._settings
        logger.info(
            "Using Ollama model: %s @ %s (num_ctx=%d)",
            s.ollama_model,
            s.ollama_base_url,
            s.ollama_num_ctx,
        )
        return ChatOllama(  # type: ignore[return-value]
            model=s.ollama_model,
            base_url=s.ollama_base_url,
            temperature=s.llm_temperature,
            num_ctx=s.ollama_num_ctx,
        )

    @property
    def llm(self) -> LLMProtocol:
        """取得 LLM 實例（首次存取時初始化）。"""
        if self._llm is None:
            self._llm = self._build_llm()
        return self._llm

    # -- 文件匯入 -----------------------------------------------------------

    # 法規條文標題正則 — 匹配 "### 第 X 條" 或 "### 第 X-Y 條"
    _ARTICLE_RE = re.compile(r"^### (第 [\d\-]+ 條)", re.MULTILINE)
    # 章節標題正則 — 匹配 "## 第 X 章 ..."
    _CHAPTER_RE = re.compile(r"^## (第 .+ 章.*)", re.MULTILINE)

    def _load_raw(self, filepath: str) -> list[object]:
        """載入文件（PDF / TXT / MD），回傳 LangChain Document 列表。"""
        path = Path(filepath)
        ext = path.suffix.lower()

        if ext == ".pdf":
            loader = PyPDFLoader(str(path))
        elif ext in (".txt", ".md"):
            loader = TextLoader(str(path), encoding="utf-8")
        else:
            raise ValueError(f"Unsupported format: {ext}")

        return loader.load()  # type: ignore[return-value]

    def _build_chapter_map(self, text: str) -> list[tuple[int, str]]:
        """建立 (position, chapter_name) 列表，用於判斷條文所屬章節。"""
        return [(m.start(), m.group(1)) for m in self._CHAPTER_RE.finditer(text)]

    def _find_chapter(self, pos: int, chapter_map: list[tuple[int, str]]) -> str:
        """根據文本位置查詢所屬章節名稱。"""
        chapter = ""
        for ch_pos, ch_name in chapter_map:
            if ch_pos <= pos:
                chapter = ch_name
            else:
                break
        return chapter

    def _split_by_article(self, text: str) -> list[ArticleChunk]:
        """依法規條文邊界切分文本，保留完整條文與章節歸屬。

        回傳 [{"text": ..., "article": "第 X 條", "chapter": "第 四 章 ..."}, ...]。
        若單條超過 1200 字元，用 RecursiveCharacterTextSplitter 再切分。
        """
        positions = [(m.start(), m) for m in self._ARTICLE_RE.finditer(text)]
        if not positions:
            return []

        chapter_map = self._build_chapter_map(text)

        chunks: list[ArticleChunk] = []
        for idx, (start, match) in enumerate(positions):
            end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
            section = text[start:end].strip()
            article = match.group(1)
            chapter = self._find_chapter(start, chapter_map)

            if len(section) <= 1200:
                chunks.append(
                    ArticleChunk(text=section, article=article, chapter=chapter)
                )
            else:
                sub_docs = self._splitter.create_documents([section])
                for sub in sub_docs:
                    chunks.append(
                        ArticleChunk(
                            text=sub.page_content,  # type: ignore[union-attr]
                            article=article,
                            chapter=chapter,
                        )
                    )

        return chunks

    def _validate_embedding_model(self, settings: Settings) -> None:
        """啟動時驗證 collection 的 embedding 模型是否一致。"""
        meta = self._collection.metadata or {}
        stored = meta.get("embedding_model")
        current = settings.embedding_model
        if stored is None:
            self._collection.modify(metadata={**meta, "embedding_model": current})
            logger.info("Stamped collection with embedding_model='%s'", current)
            return
        if stored != current:
            raise RuntimeError(
                f"Embedding 模型不符：collection 使用 '{stored}'，"
                f"設定為 '{current}'。"
                f"請刪除 data/chroma_db 目錄後重新啟動並重新匯入文件。"
            )

    def verify_embedding_dim(self) -> None:
        """驗證 embedding 維度與 collection 相容。"""
        if self._collection.count() == 0:
            return
        test_emb = self._embed_fn(["test"])[0]
        sample = self._collection.peek(limit=1)
        if sample["embeddings"] and len(sample["embeddings"][0]) != len(test_emb):
            raise RuntimeError(
                f"Embedding 維度不符：模型產出 {len(test_emb)}，"
                f"collection 為 {len(sample['embeddings'][0])}"
            )

    def _delete_stale_versions(
        self, source_label: str, current_version: str,
    ) -> None:
        """刪除指定來源中不屬於當前版本的舊區塊。"""
        existing = self._collection.get(
            where={"source": source_label}, include=["metadatas"],
        )
        stale_ids = [
            eid for eid, meta in zip(existing["ids"], existing["metadatas"])
            if meta.get("version") != current_version
        ]
        if stale_ids:
            self._collection.delete(ids=stale_ids)
            logger.info("Deleted %d stale chunks for '%s'", len(stale_ids), source_label)

    def _ingest_law(self, raw_text: str, source_label: str, version: str) -> int:
        """法規文件匯入 — 依條文邊界切分。"""
        article_chunks = self._split_by_article(raw_text)
        if not article_chunks:
            return 0

        ids = [
            f"{source_label}::v{version}::{c['article']}::chunk_{i}"
            for i, c in enumerate(article_chunks)
        ]
        texts = [c["text"] for c in article_chunks]
        metadatas: list[ChunkMeta] = [
            ChunkMeta(
                source=source_label,
                page=0,
                chunk_index=i,
                article=c["article"],
                chapter=c["chapter"],
                version=version,
            )
            for i, c in enumerate(article_chunks)
        ]
        self._collection.upsert(ids=ids, documents=texts, metadatas=metadatas)  # type: ignore[arg-type]
        return len(article_chunks)

    def _ingest_general(self, raw_docs: list[object], source_label: str, version: str) -> int:
        """一般文件匯入 — 用 RecursiveCharacterTextSplitter 切分。"""
        chunks = self._splitter.split_documents(raw_docs)  # type: ignore[arg-type]
        if not chunks:
            return 0

        ids = [f"{source_label}::v{version}::chunk_{i}" for i in range(len(chunks))]
        texts = [c.page_content for c in chunks]  # type: ignore[union-attr]
        metadatas: list[ChunkMeta] = [
            ChunkMeta(
                source=source_label,
                page=c.metadata.get("page", 0),  # type: ignore[union-attr]
                chunk_index=i,
                article="",
                chapter="",
                version=version,
            )
            for i, c in enumerate(chunks)
        ]
        self._collection.upsert(ids=ids, documents=texts, metadatas=metadatas)  # type: ignore[arg-type]
        return len(chunks)

    def ingest_document(self, filepath: str, original_name: str = "") -> int:
        """匯入文件：載入 → 切分 → 寫入新版 → 刪除舊版，回傳區塊數。"""
        source_label = original_name or Path(filepath).name
        raw_docs = self._load_raw(filepath)
        if not raw_docs:
            logger.warning("No content loaded from %s", filepath)
            return 0

        full_text = "\n\n".join(
            d.page_content  # type: ignore[attr-defined]
            for d in raw_docs
        )

        version = uuid.uuid4().hex[:8]

        if self._ARTICLE_RE.search(full_text):
            count = self._ingest_law(full_text, source_label, version)
        else:
            count = self._ingest_general(raw_docs, source_label, version)

        if count == 0:
            logger.warning("No chunks produced from %s", source_label)
            return 0

        self._delete_stale_versions(source_label, version)
        logger.info("Ingested %s → %d chunks (v=%s)", source_label, count, version)
        return count

    # -- 向量檢索 -----------------------------------------------------------
    @staticmethod
    def _truncate_at_boundary(text: str, max_chars: int) -> str:
        """在句號或分號等語意邊界處截斷，避免切在數字或關鍵資訊中間。"""
        if len(text) <= max_chars:
            return text
        # 在 max_chars 範圍內找最後一個句子邊界
        boundary_chars = "。；\n"
        best = -1
        for i in range(min(max_chars, len(text)) - 1, max_chars // 2, -1):
            if text[i] in boundary_chars:
                best = i + 1
                break
        if best > 0:
            return text[:best] + "…"
        # 找不到句子邊界，退回硬截斷
        return text[:max_chars] + "…"

    @staticmethod
    def _is_regulation(source: str) -> bool:
        """判斷來源是否為法規檔案（以 N 開頭的編號檔名）。"""
        return bool(re.match(r"^N\d+_", source))

    def _build_prompt_context(
        self,
        docs: list[str],
        metas: list[ChunkMeta],
        chapter_header: str = "",
    ) -> str:
        """將檢索到的文本區塊組建為注入 LLM 提示詞的上下文字串。

        法規條文排在手冊/指南之前，讓 LLM 優先參考法規原文。
        """
        max_chars = self._settings.rag_prompt_chunk_chars
        # 法規優先：法規在前，手冊在後，各自保留原始順序
        paired = list(zip(docs, metas, strict=True))
        paired.sort(
            key=lambda x: 0 if self._is_regulation(x[1].get("source", "")) else 1
        )
        docs = [p[0] for p in paired]
        metas = [p[1] for p in paired]

        parts: list[str] = []
        if chapter_header:
            parts.append(chapter_header)
        for i, (doc, meta) in enumerate(zip(docs, metas, strict=True)):
            truncated = self._truncate_at_boundary(doc, max_chars)
            source = meta.get("source", "?")
            # 去除副檔名；法規檔（N006 開頭）再去除編號前綴
            display = source.rsplit(".", 1)[0]
            is_reg = self._is_regulation(source)
            if is_reg:
                display = display.split("_", 1)[1]
            article = meta.get("article", "")
            tag = "法規" if is_reg else "參考"
            label = f"{display} {article}" if article else display
            parts.append(f"[資料 {i + 1}]（{tag}：{label}）\n{truncated}")
        return "\n\n".join(parts)

    @staticmethod
    def _build_source_list(
        docs: list[str],
        metas: list[ChunkMeta],
        dists: list[float],
    ) -> list[SourceSummary]:
        """將檢索結果轉為前端顯示用的來源摘要清單。"""
        return [
            SourceSummary(
                source=meta.get("source", "unknown"),
                page=meta.get("page", 0),
                relevance=round(1 - dist, 3),
                excerpt=doc[:200] + "…" if len(doc) > 200 else doc,
            )
            for doc, meta, dist in zip(docs, metas, dists, strict=True)
        ]

    def _retrieve(
        self, question: str, top_k: int | None = None
    ) -> tuple[str, list[SourceSummary]]:
        """對向量資料庫執行語意檢索，回傳 (上下文字串, 來源清單)。

        當 top_k 結果中有 2+ 筆來自同一章節時，自動擴展帶入該章所有條文。
        """
        k = top_k if top_k is not None else self._settings.rag_top_k
        query_embedding = self._embed_fn.query([question])
        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=min(k, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        docs: list[str] = results["documents"][0] if results["documents"] else []
        metas: list[ChunkMeta] = results["metadatas"][0] if results["metadatas"] else []  # type: ignore[assignment]
        dists: list[float] = results["distances"][0] if results["distances"] else []  # type: ignore[assignment]

        # --- 同章擴展 ---
        chapter_header = ""
        chapters = [m.get("chapter", "") for m in metas if m.get("chapter")]
        if chapters:
            from collections import Counter

            ch_counts = Counter(chapters)
            dominant_ch, count = ch_counts.most_common(1)[0]
            if count >= 2 and dominant_ch:
                # 找出該章的 source
                ch_source = next(
                    m["source"] for m in metas if m.get("chapter") == dominant_ch
                )
                # 撈出同章所有條文
                ch_results = self._collection.get(
                    where={
                        "$and": [
                            {"chapter": {"$eq": dominant_ch}},
                            {"source": {"$eq": ch_source}},
                        ]
                    },
                    include=["documents", "metadatas"],
                )
                # 用已檢索到的 article 集合做去重，擴展條文存入獨立清單
                existing = {
                    (d, m.get("article", "")) for d, m in zip(docs, metas, strict=True)
                }
                extra_docs: list[str] = []
                extra_metas: list[ChunkMeta] = []
                for doc, meta in zip(
                    ch_results["documents"],
                    ch_results["metadatas"],
                    strict=True,
                ):
                    key = (doc, meta.get("article", ""))
                    if key not in existing:
                        extra_docs.append(doc)
                        extra_metas.append(meta)  # type: ignore[arg-type]
                        existing.add(key)

                # 章節擴展上限：只限制擴展條文，保留原始 top_k 語意匹配
                max_extra = max(self._settings.max_chapter_articles - len(docs), 0)
                if len(extra_docs) > max_extra:
                    logger.info(
                        "Chapter cap: %d expanded → %d (keeping %d original)",
                        len(extra_docs),
                        max_extra,
                        len(docs),
                    )
                    extra_docs = extra_docs[:max_extra]
                    extra_metas = extra_metas[:max_extra]

                docs.extend(extra_docs)
                metas.extend(extra_metas)
                dists.extend(0.0 for _ in extra_docs)

                # 按條號排序（有條號的在前，按數字排序）
                def _article_sort_key(
                    item: tuple[str, ChunkMeta, float],
                ) -> tuple[int, int]:
                    meta = item[1]
                    article = meta.get("article", "")
                    m = re.search(r"(\d+)", article)
                    return (0, int(m.group(1))) if m else (1, 0)

                combined = sorted(
                    zip(docs, metas, dists, strict=True),
                    key=_article_sort_key,
                )
                docs = [c[0] for c in combined]
                metas = [c[1] for c in combined]
                dists = [c[2] for c in combined]

                # 章節標題
                display_source = ch_source.rsplit(".", 1)[0]
                if re.match(r"^N\d+_", display_source):
                    display_source = display_source.split("_", 1)[1]
                chapter_header = f"[以下為《{display_source}》{dominant_ch} 完整條文]"
                logger.info(
                    "Chapter expansion: %s %s → %d chunks",
                    ch_source,
                    dominant_ch,
                    len(docs),
                )

        context = self._build_prompt_context(docs, metas, chapter_header)
        sources = self._build_source_list(docs, metas, dists)
        return context, sources

    # -- 問答生成 -----------------------------------------------------------
    @staticmethod
    def _build_qa_messages(context: str, question: str) -> list[object]:
        """組建傳送給 LLM 的系統提示與使用者問題訊息列表。"""
        from langchain_core.messages import HumanMessage, SystemMessage

        return [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=QA_PROMPT_TEMPLATE.format(
                    context=context,
                    question=question,
                )
            ),
        ]

    def query(self, question: str, top_k: int | None = None) -> QueryResult:
        """檢索相關文本並透過 LLM 產生回答；LLM 離線時自動降級為純檢索模式。"""
        context, sources = self._retrieve(question, top_k)
        messages = self._build_qa_messages(context, question)

        try:
            response = self.llm.invoke(messages)
            answer = (
                response.content  # type: ignore[union-attr]
                if hasattr(response, "content")
                else str(response)
            )
            return QueryResult(
                answer=answer,
                sources=sources,
                question=question,
                degraded=False,
            )
        except Exception as e:
            logger.warning("LLM unavailable, falling back to retrieval-only: %s", e)
            return QueryResult(
                answer=self._build_degraded_answer(sources),
                sources=sources,
                question=question,
                degraded=True,
            )

    def query_stream(
        self, question: str, top_k: int | None = None
    ) -> Generator[StreamEvent, None, None]:
        """以串流方式產生 SSE 事件：token → sources → done（或 error 降級）。"""
        context, sources = self._retrieve(question, top_k)
        messages = self._build_qa_messages(context, question)

        try:
            for chunk in self.llm.stream(messages):
                content = (
                    chunk.content  # type: ignore[union-attr]
                    if hasattr(chunk, "content")
                    else str(chunk)
                )
                if content:
                    yield StreamEvent(event="token", data={"token": content})
            yield StreamEvent(
                event="sources",
                data={
                    "sources": sources,
                    "question": question,
                    "degraded": False,
                },
            )
        except Exception as e:
            logger.warning("LLM stream failed, falling back to retrieval-only: %s", e)
            yield StreamEvent(
                event="error",
                data={
                    "error": "AI 推論服務暫時無法使用",
                    "degraded": True,
                    "fallback_answer": self._build_degraded_answer(sources),
                    "sources": sources,
                    "question": question,
                },
            )
        yield StreamEvent(event="done", data={})

    def _build_degraded_answer(self, sources: list[SourceSummary]) -> str:
        """LLM 離線時，組建純檢索結果的降級回答。"""
        if not sources:
            return (
                "⚠️ **系統目前以離線模式運作**\n\n"
                "AI 推論服務暫時無法使用，且在知識庫中未找到相關資料。\n"
                "請稍後再試，或聯繫系統管理員。"
            )

        lines = [
            "⚠️ **系統目前以離線模式運作（AI 推論服務暫時無法連線）**",
            "",
            "為了不耽誤現場查詢需求，以下直接提供檢索到的相關法規原文，請自行參閱：",
            "",
        ]
        for i, s in enumerate(sources, 1):
            lines.append(
                f"**{i}. {s['source']}** (p.{s['page']}, "
                f"相關度 {int(s['relevance'] * 100)}%)"
            )
            lines.append(s["excerpt"])
            lines.append("")

        lines.append("---")
        lines.append("💡 待 AI 服務恢復後，可重新提問以取得整合性回答。")
        return "\n".join(lines)

    # -- 工具方法 -----------------------------------------------------------
    def collection_count(self) -> int:
        """回傳向量資料庫中的區塊總數。"""
        return self._collection.count()  # type: ignore[no-any-return]

    def list_documents(self) -> list[str]:
        """回傳知識庫中所有不重複的文件名稱。"""
        if self._collection.count() == 0:
            return []
        all_meta = self._collection.get(include=["metadatas"])
        names = sorted(
            {m.get("source", "?") for m in all_meta["metadatas"]}  # type: ignore[union-attr]
        )
        return names

    def reset(self) -> None:
        """刪除並重建向量集合（清空知識庫）。"""
        name = self._settings.collection_name
        self._client.delete_collection(name)  # type: ignore[no-untyped-call]
        self._collection = self._client.get_or_create_collection(  # type: ignore[no-untyped-call]
            name=name,
            embedding_function=self._embed_fn,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self._settings.embedding_model,
            },
        )
        logger.info("Collection '%s' reset.", name)
