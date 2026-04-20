# 🤔 技術決策紀錄

本文記錄 SafeChat 關鍵技術選型的判斷過程。不是為了說服誰，而是留下**決策脈絡**——讓未來的維護者（包括未來的我）知道「當時為什麼這樣選」。

---

## 1. 為什麼用 Llama 3.1 8B？

### 決策：Llama 3.1 8B 是 32GB 機器的最佳選擇

SafeChat 選擇 Llama 3.1 8B 作為預設模型。這不是「暫時湊合」，而是經過完整實測後的結論。

**選擇原因：**
- 發布近 2 年（2024/7），穩定性經過充分驗證
- Apple Silicon MLX 完整支援，無已知問題
- RAM 僅需 ~8GB，在 32GB 機器上留有充裕空間
- 中文法規場景多次生產驗證，能力穩定
- 搭配 `top_k=5` 達到 20/20 品質測試通過率

### 實測排除的替代方案

| 模型 | 公司 | RAM (Q4) | 實測結果 |
|------|------|----------|---------|
| **Gemma 4 26B MoE** | Google | ~19GB | **OOM** — 模型 19GB + 系統 + Docker 容器超過 32GB，exit code 137 |
| **Aya Expanse 32B** | Cohere | ~20GB | 未測（RAM 更大，預期同樣 OOM） |
| **Gemma 4 31B Dense** | Google | ~20GB | 未測（同上） |
| Llama 3.2/3.3/4 | Meta | 各異 | 中文不在官方支援語言列表中 |
| 中國公司模型 | — | — | 政策因素排除 |

**Gemma 4 26B 詳細實測紀錄（2026/4/19）：**

Flash Attention bug (#15368) 已修復，模型可正常載入（Ollama v0.20.7）。但 19GB 模型 + Docker 容器（embedding 1.1GB + app）= 超過 32GB 統一記憶體上限。`docker exec` 內的 Python 進程被 OOM killer 終止（exit code 137）。Ollama API 直接呼叫（不經 Docker）可正常運作（prompt eval 1.1s，37 tok/s），但無法與 SafeChat Docker 容器共存。

**結論：Gemma 4 26B 需要 64GB+ RAM 才能與 SafeChat 穩定共存。**

### 設計保障：一行 `.env` 切換

SafeChat 把 LLM 完全抽象化，換模型不需動程式碼：

```bash
# .env — 切換模型只改這一行
OLLAMA_MODEL=llama3.1:8b
```

未來若客戶有 64GB+ 機器，改一行即可升級到 Gemma 4 26B 或更大模型。

### 延伸：那為什麼不追 Llama 4？

Llama 4（2025/4 發布）不適合邊緣部署：

- **Scout**：109B 總參數 / 17B 激活（MoE），最小需求是**單張 H100 GPU**
- **Maverick**：400B 總參數，需要**整台 H100 伺服器**
- 量化到 Q4 後 Scout 仍需 55 GB+ RAM——32GB 裝不下
- 中文不在官方 12 支援語言中

Llama 4 是資料中心級模型，SafeChat 的目標使用場景（工地 Mini PC、16-32 GB RAM）根本用不上。**選型要貼合使用情境**，而不是追 benchmark 排行榜。

---

## 2. 為什麼 chunk_size=600、overlap=120？

這組參數針對**法規文件結構**調校，不是通用預設。

### 觀察

營建安全相關法規條文長度分佈：
- 短條文（< 200 字）：約 30%，如定義、適用範圍
- 標準條文（300-800 字）：約 55%，絕大多數實質規範
- 長條文（> 800 字）：約 15%，含多項列舉或附件

### 決策

**chunk_size=600**：能容納絕大多數完整條文，避免把「第 11 條 高度 2 公尺以上之處所進行作業，應採取下列墜落防止措施：一、...」切成兩半。

**overlap=120**：確保條文編號與內容不會分家。實測若 overlap < 80，約 8% 的 chunk 會出現「編號在上一個 chunk、內容在下一個」的問題，嚴重影響 AI 引用的準確度。

**分隔符順序 `["\n\n", "\n", "。", "；", "，", " ", ""]`**：優先按段落、次按句子、再按分號/逗號。中文沒有空白分詞，這個順序能確保切分點落在語意邊界。

### 替代方案（未採用）

- **chunk_size=1000**：能放下大多數長條文，但增加 embedding 雜訊，實測檢索相關度下降 5-8%
- **chunk_size=300**：速度快，但條文容易被切斷

---

## 3. 為什麼用 ChromaDB 而不是 Pinecone / Weaviate？

### 目標場景：地端部署的邊緣裝置

Pinecone / Weaviate 都是**雲端託管**或**自建叢集**為主，對 SafeChat 的場景太重：

- **Pinecone** — SaaS，資料要傳雲端，違反「資料不出場」原則
- **Weaviate** — 要自建、要 Docker Swarm/K8s，一台 Mini PC 部署太複雜
- **Milvus** — 類似 Weaviate 問題

### ChromaDB 的優勢

- **嵌入式運行**（embedded mode）—— 跟 SQLite 一樣，不需要獨立的 server 進程
- **Python 原生**—— 直接 `pip install chromadb`，單一機器零部署成本
- **資料持久化**—— 寫到本地 `data/chroma_db/` 目錄，備份只要 `tar` 打包

### 擴展路徑

當客戶規模變大、要跨工地共享知識庫時，ChromaDB 也支援 client-server 模式，可以平滑升級到中央部署。**不會變成技術債**。

---

## 4. 為什麼 RAG 回應要保留「離線模式」？

### 問題場景

工地網路不穩是常態。GPS 監控系統 8 年經驗告訴我：**你永遠會遇到網路斷線**。

傳統 RAG 實作遇到 LLM 斷線會直接 500 錯誤，UI 顯示「系統錯誤」——但對現場使用者來說，他可能正站在施工架下急需查規範。**完全當機 = 不可用**。

### 解法：Graceful Degradation

參考 IoT 系統處理 GPRS 封包遺失的設計哲學：**寧可降級也不要停止服務**。

```python
# app/rag_engine.py
try:
    response = self.llm.invoke(messages)
    answer = response.content
    return {"answer": answer, "sources": sources, "degraded": False}
except Exception as e:
    # LLM 斷線，回傳純檢索結果
    return {
        "answer": self._build_degraded_answer(sources),
        "sources": sources,
        "degraded": True,
    }
```

**使用者體驗：**
- ✅ 仍會看到相關法規原文
- ✅ UI 右上角燈號變黃，清楚告知
- ✅ 回答有黃色邊條，標示「離線模式」
- ✅ 恢復後 30 秒內自動偵測

### 為什麼這個設計值得特別提出？

**這是實體產業現場經驗帶來的直覺。** 純雲原生工程師預設「網路會通」，IoT/邊緣工程師預設「網路會斷」。同一個問題，兩種完全相反的設計起點。

---

## 5. 為什麼用 SSE 串流而不是讓使用者等待完整回答？

### 問題場景

使用 Ollama 本地模型（Llama 3.1 8B）時，生成完整回答需要 15-25 秒。在這段時間內，UI 只顯示「打字中」動畫，使用者完全沒有進度回饋。現場工地人員在壓力下無法判斷「系統是在想還是卡死了」。

### 解法：Server-Sent Events (SSE) 串流

LLM 本身就是逐 token 生成的，只需把這個過程暴露給前端。

```
傳統：[等 20 秒] → 顯示完整回答
串流：[< 1 秒顯示第一個 token] → 字一個個蹦出來 → [約 20 秒顯示完畢]
```

**感知延遲從 20 秒降到 < 1 秒**——實際總時間不變，但使用者體驗完全不同。

### 為什麼選 SSE 而不是 WebSocket？

| | SSE | WebSocket |
|--|-----|-----------|
| 方向 | 單向（server → client） | 雙向 |
| HTTP 兼容 | ✅ 標準 HTTP，可過 nginx/代理 | 需要升級協議 |
| 實作成本 | FastAPI `StreamingResponse` 即可 | 需要 websocket handler |
| 自動重連 | ✅ 瀏覽器原生支援 | 需自己實作 |
| 適用場景 | ✅ LLM token 串流（單向推送） | 雙向即時通訊 |

SafeChat 是「問 → 答」模式，根本不需要雙向通訊。SSE 是最小複雜度的正確選擇。

### 為什麼用 `fetch` + `ReadableStream` 而不是 `EventSource`？

`EventSource` API 只支援 **GET 請求**，但 `/api/ask/stream` 需要接收 JSON 請求體（問題內容）。改用 `fetch` 加上 `response.body.getReader()` 手動解析 SSE 格式，能同時保留 POST 語意和串流能力。

### SSE 事件協議設計

```
event: token          ← 每個 LLM chunk
data: {"token": "..."}

event: sources        ← 生成結束後送一次
data: {"sources": [...], "degraded": false}

event: error          ← LLM 失敗時的降級回答
data: {"fallback_answer": "...", "degraded": true, ...}

event: done           ← 流結束信號
data: {}
```

把 sources 放在最後送，是因為 sources 在 retrieval 階段就已知，但要等 LLM 確認「有完整回答」才一起送，避免「回答失敗但 sources 已顯示」的混亂狀態。

### 向後兼容

原 `/api/ask` 端點完全保留，現有整合（如 `make test` 腳本、Swagger 測試）不受影響。

---

## 7. 首 Token 延遲優化：為什麼要做這些改動？

### 問題

上線後發現首 token 延遲高達 **1 分 30 秒**，遠超預期。根因分析確認是**多個瓶頸疊加**。優化後首 token 仍需 15-20 秒（一般問題）或 30-40 秒（章節擴展），這是 **Ollama prompt prefill 的硬體限制**（佔 99% 時間），RAG 檢索僅需 ~0.2 秒。

### 根因分析

| 瓶頸 | 延遲 | 解法 |
|------|------|------|
| Ollama 閒置卸載模型（KEEP_ALIVE 預設 5m）| 30-60s | `KEEP_ALIVE=-1` 永不卸載 |
| SentenceTransformer + ChromaDB lazy init 堆在首次請求 | 10-30s | FastAPI `lifespan` 預熱 |
| `langchain_community.ChatOllama` 已廢棄（v0.3.1 起） | 未知 | 遷移到 `langchain_ollama.ChatOllama` |
| `num_ctx` 未設定，llama3.1:8b 預設 128K context，KV-cache 分配極慢 | 一次性 60-90s | `OLLAMA_NUM_CTX=8192` |
| Prompt tokens 太多 | 每次請求 ~3s | `RAG_TOP_K=5` + 章節自動擴展 + 精簡 system prompt |
| 健康檢查每 30s 做完整 LLM 推理，與真實請求搶佔 Ollama | 佇列延遲 | `/api/health` 改為 `GET /api/tags` 輕量 ping |

### 修復 1：`OLLAMA_KEEP_ALIVE=-1`

Ollama 模型卸載後，下次請求需重新從磁碟載入，4.7 GB 的 llama3.1:8b 需 30-60 秒。設為 `-1` 後模型常駐記憶體，閒置不影響響應速度。

```bash
# macOS
launchctl setenv OLLAMA_KEEP_ALIVE "-1"

# 或透過 API 單次設定
curl -X POST http://localhost:11434/api/generate \
  -d '{"model":"llama3.1:8b","prompt":"hi","stream":false,"keep_alive":-1}'
```

### 修復 2：同步驗證 + 背景載入

```python
# app/main.py — lifespan 同步驗證 RAG，LLM 在背景載入並驗證可達性
def _load_llm_background():
    global _rag_ready
    if rag is not None:
        _ = rag.llm            # 建構 ChatOllama（lazy，不驗證連線）
        result = check_llm(get_settings())  # 呼叫 /api/tags 確認模型存在
        if not result.get("ok"):
            raise RuntimeError("Ollama 模型不可用")
    _rag_ready = True
    # 失敗則 os._exit(1) — 啟動時 LLM 不可用就直接終止

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag
    rag = RAGEngine(...)       # SentenceTransformer + ChromaDB + Embedding 驗證（同步）
    threading.Thread(target=_load_llm_background, daemon=True).start()
    yield
    # RuntimeError（如 Embedding 模型不符）會從 lifespan 冒出，FastAPI 不啟動
```

設計考量：
- **Embedding 模型驗證**在 lifespan 同步執行，不符則 RuntimeError → 程序不啟動
- **LLM 載入**是慢操作（e5-large-instruct ~2.2GB，Docker CPU 需約 4-5 分鐘），放背景 thread
- **LLM 可達性驗證**建構後呼叫 Ollama `/api/tags` 確認模型存在，不做推理
- **LLM 載入或驗證失敗**則 `os._exit(1)` 終止程序，不降級
- **運行中 Ollama 暫時斷線**仍會降級（只回檢索結果，不合成回答）— 這是合理的，因為 Ollama 可能只是暫時重啟

載入期間行為不變：
- 載入期間：`/` 回傳載入頁面（`templates/loading.html`），每 5 秒輪詢 `/api/health`
- `/api/health` 回傳 503 + `{"status": "starting"}`
- 就緒後：頁面自動跳轉到主介面，首次請求無冷啟動

### 修復 3：遷移到 `langchain_ollama.ChatOllama`

`langchain_community.chat_models.ChatOllama` 自 v0.3.1 起已廢棄。官方推薦遷移到獨立的 `langchain-ollama` 包，直接替換 import 即可。

### 修復 4：設定 `num_ctx=4096`

`llama3.1:8b` 的內建 context window 為 **128K tokens**。未設定 `num_ctx` 時，Ollama 預設使用完整 128K 分配 KV-cache，僅此一步就需要 60-90 秒。設為 `4096` 可容納章節擴展上限後的法規內容（8 條文 × 500 字元 ≈ 1333 tokens + 回答空間），同時大幅減少 KV-cache 記憶體佔用。

### 修復 5：章節感知檢索 + 擴展上限

檢索採用兩階段策略：先以 `RAG_TOP_K=3` 做語意檢索，若偵測到 2+ 筆結果屬同一章節，自動擴展帶入該章條文，但上限為 `MAX_CHAPTER_ARTICLES=8` 條。每條截取前 `RAG_PROMPT_CHUNK_CHARS=500` 字元。

原本無上限的章節擴展（最多 31 chunks）是延遲和品質的雙重瓶頸：大量無關條文不僅拖慢回應（prompt 過大），還會稀釋 LLM 對相關條文的注意力，導致回答引用錯誤條號。

透過 `RAG_TOP_K`、`RAG_PROMPT_CHUNK_CHARS` 和 `MAX_CHAPTER_ARTICLES` 環境變數調整。

### 修復 7：啟用 `OLLAMA_FLASH_ATTENTION=1`

2026 年社群標準做法。Flash Attention 在長 context 上可加速 20-40%，同時啟用 KV-cache 量化（Q8_0），減少約 50% 的 KV-cache 記憶體佔用且品質損失 < 1%。

```bash
# macOS
launchctl setenv OLLAMA_FLASH_ATTENTION 1

# Linux
echo "OLLAMA_FLASH_ATTENTION=1" | sudo tee -a /etc/environment
sudo systemctl restart ollama
```

### 修復後效能基準（Mac mini M5 32GB）

| 測試類型 | 修復前 TTFT | 修復後 TTFT | 改善 |
|---------|------------|------------|------|
| 一般查詢（3 chunks） | ~40s+ | **4.6-5.8s** | >85%↓ |
| 章節擴展（cap 8 chunks） | ~55s+ | **7.2s** | >85%↓ |

修復前章節擴展無上限（最多 31 chunks），修復後限制為 8 chunks。

### 修復 6：健康檢查改為輕量 HTTP ping

```python
# 舊：每 30 秒觸發一次完整 LLM 推理
response = self.llm.invoke([HumanMessage(content="hi")])

# 新：只檢查 Ollama 服務是否存活、模型是否已載入
resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
```

`/api/health` 原本對 Ollama 執行一次 `invoke("hi")`，推理本身就需要數秒。每 30 秒呼叫一次，在 Ollama 只能單工處理請求的情況下，健康檢查與用戶請求相互競爭，造成額外佇列延遲。

改為 `GET /api/tags` 後：不做推理、回應時間 < 5ms，完全不影響用戶請求。

---

## 8. 小型 LLM + top_k=3 的結構性限制

### 問題

經實測驗證，目前的模型與檢索組合存在以下已知限制，這些是 8B 參數量模型和少量檢索結果的固有取捨，**無法單靠調參解決**。

### 限制 1：語意相近但不同領域的條文誤檢索

embedding 模型以語意相似度檢索，無法區分同一關鍵字在不同法規脈絡中的含義。

**實測案例**：問「高空作業需要什麼防護設備？」→ 檢索命中第 282 條（呼吸防護具，用於缺氧/有害氣體場所），而非第 23 條（安全帶，用於墜落防護）。原因是「高空」同時出現在墜落防護和缺氧作業的語意場域中。

### 限制 2：top_k=3 的覆蓋率限制

只檢索 3 筆結果，一旦前 3 筆偏離，沒有後續結果可以修正。提高到 top_k=5 可增加覆蓋率，但會增加 ~2-3 秒 TTFT。

### 限制 3：8B 模型的相關性判斷力

當 prompt 中包含多條法規時，8B 級模型（如 Llama 3.1 8B）傾向逐條摘要（「條文目錄」模式），而非篩選最相關的條文深入回答。較大模型（如 Gemma 4 26B MoE，啟用 3.8B 但總知識量 25.2B）或 32B+ 密集模型在多文件間的相關性判斷顯著更好。

### 因應措施

1. **UI 免責聲明**：輸入框下方永久顯示「本系統僅供參考，不構成法律意見。回答可能不完整或引用不相關條文，請以法規原文為準。」
2. **來源強制顯示**：每個回答下方顯示來源條號和相關度，使用者可自行驗證。
3. **品質回歸測試**：`tests/test_rag_quality.py` 包含 20 題測試集，已知限制案例標記為 `xfail`，上線前和版本更新後執行 `make test-quality`。
4. **來源優先排序**：法規條文在 prompt 中排在手冊/指南之前，降低 LLM 偏好摘要而非原文的傾向。

### 升級路徑

| 方向 | 預期改善 | 代價 |
|------|---------|------|
| `RAG_TOP_K=5`（已採用） | 覆蓋率提升，20/20 通過 | TTFT +2-3 秒（可接受） |
| 升級到 64GB+ RAM 機器 | 可用 Gemma 4 26B 或更大模型 | 硬體成本 |
| Ollama MLX backend | Apple Silicon 上 2-4x 加速 | 模型支援持續擴展中 |

---

## 9. Reranker 實測結果：弊大於利

### 背景

2026 社群標準 RAG pipeline 推薦 retrieve k=15 → reranker → top k=5 的架構。實測了 `mixedbread-ai/mxbai-rerank-base-v2`（德國 Mixedbread，0.5B 參數，Apache 2.0，100+ 語言，中文 83.70 分）。

### 實測數據

| 指標 | 無 reranker (top_k=5) | 有 reranker (retrieve=15 → top_k=5) |
|------|----------------------|--------------------------------------|
| 品質測試通過率 | **20/20** | **14/20** |
| 延遲 | ~15-35s | ~25-50s |
| Q5 高空作業（之前 xfail） | PASS | XFAIL（排到錯誤條文） |

### 品質下降原因

通用型 reranker 對中文法規專業術語的判斷不如 embedding 模型（`intfloat/multilingual-e5-large-instruct` 是針對中文最佳化的）。Reranker 把原本排對的結果重新排錯：

- Q3 鋼管施工架：第 59 條被排掉
- Q7 安全帶：第 23 條被排掉
- Q13 墜落防止計畫：第 17 條被排掉
- Q15 施工架物料：第 46 條被排掉

### 結論

**在中文法規 RAG 場景下，通用 reranker 弊大於利。** Embedding 模型（multilingual-e5-large-instruct）對法規術語的語意排序已經足夠好，cross-encoder reranker 反而引入雜訊。

此結論可能不適用於：
- 更大的知識庫（數千篇文件而非 ~700 chunks）
- 針對中文法規微調過的 reranker
- 非法規領域的 RAG 應用

---

## 6. 為什麼用 Vue 3 本地載入而不是 Vite + npm build？

### 決策：單一 HTML 檔部署，完全離線運作

`templates/index.html` 引用本地的 Vue 檔案（`static/js/vendor/vue.global.prod.min.js`）與自行託管的字型（`static/fonts/`）。**不需要 node_modules、不需要 build step、不需要 webpack 設定、不需要網路連線**。

### 理由

1. **完全離線** — 專案部署在無網路的工地環境，所有資源必須本地載入
2. **部署到 Mini PC 容易** — 不需在邊緣裝置裝 Node.js
3. **Jinja2 直接 render** — FastAPI 的 `templates.TemplateResponse` 一行搞定
4. **未來可升級** — 要改用 Vite + SFC（`.vue` 單檔元件）隨時可以，Composition API 寫法完全相容

### 權衡

犧牲了型別檢查、熱重載、tree-shaking 的開發體驗。但對 < 1000 行的單頁應用，這些效益比不上「零建置複雜度」。

**決策邏輯：工具的複雜度要配得上專案的規模。** over-engineering 跟 under-engineering 一樣糟。

---

## 10. 為什麼從 e5-base 升級到 e5-large-instruct？

### 決策：升級至 `intfloat/multilingual-e5-large-instruct`

原本使用 `intfloat/multilingual-e5-base`（278M 參數、768 維），升級至 `intfloat/multilingual-e5-large-instruct`（560M 參數、1024 維）。

### 實測數據（2026/4/20）

| 指標 | e5-base | e5-large-instruct |
|------|---------|-------------------|
| MIRACL 中文 nDCG@10 | 51.5 | **56.2**（+9%） |
| 品質測試通過率 | 20/20 | **20/20** |
| 模型大小 | ~1.1 GB | ~2.2 GB |
| 單次查詢延遲 | ~50ms | ~100ms |

### 升級原因

1. **中文檢索品質提升 9%** — 對法規術語的語意理解更精確
2. **Instruct 格式** — 支援任務描述前綴，讓模型知道「這是工安法規檢索」而非通用搜尋
3. **MIT 授權** — 無商用限制
4. **32GB RAM 足夠** — 模型 2.2GB + Llama 3.1 8B ~8GB，仍有充裕空間

### 排除的替代方案

| 模型 | 排除原因 |
|------|----------|
| BAAI/bge-m3 | 中國公司（BAAI），政策因素排除 |
| Qwen3-Embedding | 中國公司（阿里巴巴），政策因素排除 |
| jina-embeddings-v3 | CC BY-NC 授權，不適合商用 |
| e5-mistral-7b | 7B 參數，CPU 推論過慢 |
| e5-small | 中文檢索品質下降明顯（45.9 vs 51.5） |

### 升級注意事項

- **Embedding 維度不同（768 → 1024）**，升級後必須清除 ChromaDB 索引並重建
- **Instruct 前綴格式不同**：查詢使用 `Instruct: <任務描述>\nQuery: <問題>`，passage 前綴不變
- 程式碼已自動偵測 instruct 變體並套用正確前綴（`rag_engine.py` `SentenceTransformerEmbedding`）
