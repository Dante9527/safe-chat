# ⛑ SafeChat

**AI-Powered Construction Safety Q&A — 工地安全 AI 助理**

> 上傳工安法規 / 安全手冊 PDF，讓工地現場人員用自然語言即時查詢安全規範。
> 預設採用**地端 LLM 部署**，資料不出場、免 API 費用，為營建現場的資安與網路限制而設計。

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3.4-42b883?logo=vue.js&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-0.3-green)
![ChromaDB](https://img.shields.io/badge/ChromaDB-0.5-orange)
![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-000)

---

## 🎯 What is SafeChat?

SafeChat 是一個 **Retrieval-Augmented Generation (RAG)** 系統，專為營建產業設計。

**為什麼選擇地端部署？** 營建現場有三個痛點讓雲端 API 方案不適用：

1. **資安合規** — 施工圖、合約、法規註記等機密資料不能傳到第三方
2. **網路不穩** — 工地、偏遠工區的網路品質難以保證
3. **成本可控** — 不隨查詢量增加而累積 API 費用

預設使用 **Ollama + Llama 3.1** 在本機運行，**完全離線、免 API key、資料不出場**。

### 適用場景

| 場景 | 範例問題 |
|------|---------|
| 施工安全查詢 | 「施工架搭設有哪些安全規範？」 |
| 危害預防 | 「開挖作業前需要做哪些安全檢查？」 |
| 事故通報 | 「工地發生墜落事故的通報流程為何？」 |
| 電氣安全 | 「電氣作業的防護措施有哪些？」 |
| 高溫作業 | 「高溫作業的危害預防措施？」 |

---

## 🚀 Quick Start

選一個方式：

### 方式 A：Docker 一鍵部署（推薦）

**前置需求：** Docker + Docker Compose

```bash
git clone https://github.com/<your-username>/safe-chat.git
cd safe-chat
make up        # 或 docker compose up -d
```

背後會自動啟動 Ollama、下載 Llama 3.1 8B 模型（約 4.7 GB）、啟動 SafeChat。開 **http://localhost:8000** 即可使用。

> 💡 首次執行約 5-15 分鐘（視網速下載模型）。用 `make logs` 看進度。
> 完整的 Docker 指令清單見 [下方 Docker 章節](#docker)。

### 方式 B：本地開發

**前置需求：** Python 3.10+、16 GB RAM、已安裝 [Ollama](https://ollama.ai)

```bash
git clone https://github.com/<your-username>/safe-chat.git
cd safe-chat
pip install -r requirements.txt

ollama pull llama3.1:8b       # 約 4.7 GB

cp .env.example .env          # 所有設定與說明見 .env.example
python scripts/fetch_laws.py  # (選用) 抓真實工安法規當測試資料

uvicorn app.main:app --reload
```

開瀏覽器 **http://localhost:8000** → 把 `data/sample_docs/*.txt` 拖到左側上傳區 → 開始提問。

> 💡 **其他選項：** [完整部署指南](DEPLOYMENT.md)

---

## 🎬 Demo 提問

匯入法規後，試試這些問題，每個都展示不同的 RAG 能力：

| 提問 | 展示重點 |
|------|---------|
| 「施工架搭設有哪些安全規範？」 | 檢索具體條文 + 引用來源法規 |
| 「工地發生墜落事故的通報流程？」 | 跨法規整合（職安法 + 營造標準） |
| 「高度 3 公尺作業需要什麼防護？」 | 條件理解 + 相關度排序 |
| 「電氣作業的防護措施有哪些？」 | 專項法規檢索 |

回答下方會顯示 `📎 來源文件 p.X 85%` 的標籤——**這就是 RAG 跟純 LLM 的關鍵差別：答案可追溯、可驗證。**

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────┐
│            SafeChat UI (Vue 3 SPA)                  │
│  Chat + 文件上傳 + 快速提問 + 來源引用                │
└───────────────────┬─────────────────────────────────┘
                    │ HTTP / REST  (POST /api/ask/stream → SSE)
┌───────────────────▼─────────────────────────────────┐
│              FastAPI Server                         │
│                                                     │
│  ┌─────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Upload  │  │  RAG Engine  │  │  Health / API │  │
│  └────┬────┘  └──────┬───────┘  └───────────────┘  │
│       │              │                              │
│  ┌────▼────┐  ┌──────▼───────┐                      │
│  │ PyPDF   │  │  LangChain   │                      │
│  │ Loader  │  │  Messages    │                      │
│  └────┬────┘  └──────┬───────┘                      │
│       │              │                              │
│  ┌────▼──────────────▼──────┐   ┌────────────────┐  │
│  │  RecursiveTextSplitter   │   │ Ollama (Llama) │  │
│  │  (chunk=600, overlap=120)│   │ Ollama (Llama) │  │
│  └────────────┬─────────────┘   └────────────────┘  │
│               │                                     │
│  ┌────────────▼─────────────┐                       │
│  │    ChromaDB (cosine)     │                       │
│  │  + SentenceTransformer   │                       │
│  └──────────────────────────┘                       │
└─────────────────────────────────────────────────────┘
```

### Tech Stack

| 層級 | 技術 | 選用原因 |
|------|------|---------|
| 前端 | **Vue 3 (Composition API)** | 反應式狀態、動畫過渡、CDN 免 build |
| 後端 | **FastAPI** | 高效能、async、自動 Swagger docs |
| RAG | **LangChain** | Pipeline 標準化、多 LLM 後端 |
| 向量庫 | **ChromaDB** | 嵌入式、零部署、cosine search |
| Embedding | **multilingual-e5-base** (sentence-transformers) | 本地執行、多語言中文支援、免 API key |
| LLM | **Ollama + Llama 3.1** | 地端部署、資料不出場、零 API 費用 |
| PDF | **PyPDF** | 中文解析穩定 |

完整選型推理見 [`docs/TECH_DECISIONS.md`](docs/TECH_DECISIONS.md)（為什麼選 Llama 3.1、chunk 參數怎麼調的、Gemma 4 實測結果等）。

---

## 🧩 RAG Pipeline

### Chunking Strategy

```python
RecursiveCharacterTextSplitter(
    chunk_size=600,       # 涵蓋多數完整法規條文 (avg 300-800 chars)
    chunk_overlap=120,    # 避免條文編號與內容跨 chunk 切斷
    separators=["\n\n", "\n", "。", "；", "，", " ", ""],
)
```

針對法規文件特性調校的中文分隔符優先順序，確保條文邊界不被切斷。

### Retrieval & Generation

- **Cosine similarity** 向量檢索，預設 top-5 + 章節自動擴展（可透過 `RAG_TOP_K` 調整）
- 法規條文級切分，每條保留完整內容與章節歸屬，檢索到同章 2+ 條文時自動帶入全章
- 回傳來源文件名 + 條號 + 相關度 %，可追溯原文
- 多語言 embedding 模型（`intfloat/multilingual-e5-base`），原生支援中文語意檢索

### 🛡 Resilience Features（邊緣部署特化）

為**工地不穩定網路環境**設計的容錯機制：

- **Graceful degradation** — LLM 斷線時，系統仍回傳檢索到的法規原文，不會整個癱瘓
- **Health check endpoint** — `/api/health` 檢查 LLM、向量庫、磁碟空間狀態
- **UI 即時狀態指示** — 右上角燈號顯示綠/黃/紅三級警示
- **Degraded message banner** — 離線模式下的回答有黃色警示邊條
- **Lifespan 預熱** — 應用啟動時預載 SentenceTransformer、ChromaDB、LLM 客戶端，healthcheck 通過後用戶首次請求無冷啟動
- **OLLAMA_KEEP_ALIVE=-1** — 模型永久留在記憶體，閒置不卸載，避免 30-60s 重載延遲
- **OLLAMA_FLASH_ATTENTION=1** — 長 context 加速 20-40%，啟用 KV-cache 量化（2026 社群標準做法）
- **MAX_CHAPTER_ARTICLES=8** — 章節擴展上限，避免 prompt 過大拖慢回應且稀釋相關性

```bash
# 實測降級模式
brew services stop ollama           # 模擬 AI 服務斷線
# 到 UI 提問 → 會看到「系統目前以離線模式運作」+ 檢索結果
# UI 右上角燈號變黃
brew services start ollama          # 恢復
# 30 秒內燈號自動轉綠
```

---

## 🔧 API Endpoints

| Method | Path | 說明 | 認證 |
|--------|------|------|------|
| `GET` | `/` | SafeChat Web UI | — |
| `GET` | `/api/health` | 健康檢查（LLM / 向量庫 / 磁碟狀態） | — |
| `POST` | `/api/upload` | 上傳文件（multipart/form-data） | API Key* |
| `POST` | `/api/ask` | 提問（JSON: `{question, top_k}`），等待完整回答 | API Key* |
| `POST` | `/api/ask/stream` | 提問（SSE 串流），逐 token 即時推送 | API Key* |
| `GET` | `/api/stats` | 知識庫統計 | API Key* |
| `DELETE` | `/api/reset` | 清空知識庫 | Admin Token |

> \* API Key 為可選認證——設定 `API_KEY` 後才啟用。`/api/health` 免認證（供 Docker healthcheck 使用）。
> `/api/reset` 需要 `ADMIN_TOKEN`（透過 `X-Admin-Token` 標頭傳送），未設定時此功能停用。

### 安全機制

內建 API Key 驗證、Admin Token、速率限制、檔案上傳限制、輸入驗證、安全標頭、CORS、錯誤資訊隱藏。

各機制的環境變數與調校方式見 **`.env.example`**，部署指引見 **[`DEPLOYMENT.md`](DEPLOYMENT.md)**。

Interactive docs: **http://localhost:8000/docs**

---

<a id="docker"></a>
## 🐳 Docker 一鍵部署

**真正的一鍵：連模型下載都自動處理。** 適合部署到工地辦公室的 Mini PC 或邊緣裝置。

```bash
make up
```

就這樣。背後會自動完成：

1. 啟動 Ollama 推論服務
2. 下載 Llama 3.1 8B 模型（約 4.7 GB，首次 5-15 分鐘）
3. 啟動 SafeChat FastAPI + Vue UI
4. 等健康檢查通過才對外提供服務

完成後開 **http://localhost:8000** 即可使用。

### 其他常用指令

```bash
make status        # 檢查所有服務健康狀態
make logs          # 即時查看所有 log
make fetch-laws    # 容器內自動抓取工安法規
make test          # 端對端測試 RAG 流程
make down          # 停止（保留資料）
make clean         # 徹底清除（含向量庫、模型）
make help          # 看所有指令
```

### 沒有 make 怎麼辦？

直接用 `docker compose` 也一樣可以：

```bash
docker compose up -d            # 一鍵啟動（含自動下載模型）
docker compose ps               # 看狀態
docker compose logs -f          # 看 log
docker compose down             # 停止
```

### 遇到問題？

Docker 部署常見問題詳見 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)，包含：

- **macOS Docker 推論慢** — Metal GPU 限制與 workaround
- **首次啟動要等多久** — 各步驟時間分佈
- **Ollama healthcheck 失敗** — 常見原因
- **埠口衝突** — 如何改埠號

完整部署說明（硬體需求、GPU 加速、資安設定、監控告警）見 [`DEPLOYMENT.md`](DEPLOYMENT.md)。

---

## 📂 Project Structure

```
safe-chat/
├── app/
│   ├── main.py             # FastAPI routes & API endpoints
│   └── rag_engine.py       # RAG core: chunk → embed → retrieve → generate
├── templates/
│   └── index.html          # Vue 3 SPA UI
├── scripts/
│   ├── fetch_laws.py       # 批次抓取全國法規資料庫
│   └── README.md
├── docs/
│   ├── TECH_DECISIONS.md   # 技術選型推理紀錄
│   └── TROUBLESHOOTING.md  # Docker 部署問題排解
├── data/
│   ├── sample_docs/        # Demo 文件（fetch_laws.py 輸出位置）
│   ├── uploads/            # 使用者上傳（自動建立）
│   └── chroma_db/          # 向量資料庫（自動建立）
├── README.md               # 技術文件（本檔）
├── PLAYBOOK.md             # 使用手冊（工地主任）
├── DEPLOYMENT.md           # 部署指南（IT 部門）
├── BUSINESS_CASE.md        # 效益評估（管理層）
├── Makefile                # 一鍵指令包裝
├── Dockerfile              # Multi-stage build
├── docker-compose.yml      # SafeChat 容器編排（連接 host Ollama）
├── .dockerignore
├── .env.example
├── requirements.txt
└── LICENSE
```

---

## 📚 Documentation

本專案按**受眾分層**組織文件：

| 文件 | 受眾 | 內容 |
|------|------|------|
| [`README.md`](README.md) | 開發者 | 架構、安裝、API、技術細節 |
| [`docs/TECH_DECISIONS.md`](docs/TECH_DECISIONS.md) | 開發者 / 架構師 | 選型推理、替代方案、trade-offs |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | 開發者 / IT | Docker 部署常見問題排解 |
| [`PLAYBOOK.md`](PLAYBOOK.md) | 工地主任 / 安全官 | 白話操作指南、常見問題、狀態燈號 |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | IT / 維運 | 部署步驟、硬體需求、資安設定、監控 |
| [`BUSINESS_CASE.md`](BUSINESS_CASE.md) | 管理層 | 效益試算、ROI、導入時程、風險評估 |
| [`scripts/README.md`](scripts/README.md) | 開發者 | fetch_laws.py 腳本使用說明 |

---

## 🗺 Roadmap

| 方向 | 說明 |
|------|------|
| **IoT 整合** | GPS 車輛座標 + 地理圍欄 → 車輛進入危險區自動推送安全規範 |
| **多模態** | 上傳工地照片，AI 判斷安全合規狀態 |
| ✅ **Streaming** | SSE 串流回答（`/api/ask/stream`），逐 token 即時顯示，已實現 |
| ✅ **基礎安全** | API Key 驗證、Admin Token、速率限制、檔案上傳限制、輸入驗證、安全標頭，已實現 |
| **權限管理** | 依角色（工地主任/安全官/作業員）分層查詢 |
| **語音介面** | Whisper 整合，現場人員語音提問 |
| **RAG 評估** | RAGAS / DeepEval 評估 faithfulness & relevancy |

---

## 👤 About

8 年 GPS 車輛監控調度系統開發經驗（IoT / GPRS / UDP），服務營建與工程車輛管理客戶。SafeChat 展示將 AI / LLM 技術落地到實體產業現場的能力。

---

## License

MIT
