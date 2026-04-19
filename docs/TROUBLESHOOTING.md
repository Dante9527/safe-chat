# TROUBLESHOOTING

本文記錄部署常見問題與對應解法。按「症狀 → 原因 → 解法」組織。

---

## 首 Token 延遲偏高

### 症狀：發問後要等十幾秒才看到第一個字出現

首 token 延遲的 99% 來自 **Ollama prompt prefill（硬體限制）**，RAG 檢索僅需 ~0.2 秒。正確設定後，一般問題（~700 tokens）約 5 秒，章節擴展（~1000 tokens）約 7 秒（M5 32GB 基準）。逐項排查：

**排查 1：Flash Attention 是否啟用？**

這是影響最大的單一設定。未啟用時長 context 延遲增加 20-40%。

```bash
# macOS — 檢查是否已設定
launchctl getenv OLLAMA_FLASH_ATTENTION
# 應輸出 "1"

# 若未設定：
launchctl setenv OLLAMA_FLASH_ATTENTION 1
brew services restart ollama
```

```bash
# Linux — 檢查是否已設定
echo $OLLAMA_FLASH_ATTENTION
# 應輸出 "1"

# 若未設定：
echo "OLLAMA_FLASH_ATTENTION=1" | sudo tee -a /etc/environment
sudo systemctl restart ollama
```

**排查 2：Ollama 是否正在運作且模型已載入？**

```bash
ollama ps
# 預期：.env 中設定的模型已載入，Mac 上 PROCESSOR 應為 100% GPU，UNTIL 應為 Forever
```

若模型未載入或 UNTIL 不是 Forever：
```bash
# 載入模型並設為永不卸載（將 MODEL 替換為 .env 中的 OLLAMA_MODEL 值）
MODEL=$(grep OLLAMA_MODEL .env | cut -d= -f2)
curl -X POST http://localhost:11434/api/generate \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":-1}"
```

**排查 3：SafeChat 應用是否完成預熱？**

```bash
docker compose logs safe-chat | grep "就緒"
```

若看到 `RAG 引擎就緒 — 所有元件已載入，開始接受請求。`，代表預熱完成。模型載入約需 4-5 分鐘，期間瀏覽器會顯示載入頁面。

**排查 4：用 Ollama verbose 模式確認 prefill 時間**

```bash
MODEL=$(grep OLLAMA_MODEL .env | cut -d= -f2)
curl -X POST http://localhost:11434/api/generate \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"verbose\":true}}" | jq '.prompt_eval_duration,.eval_duration'
```

`prompt_eval_duration`（prefill 時間）過長代表硬體計算慢，需要換小模型或啟用 GPU。

**排查 5：確認 `.env` 設定是否正確**

```bash
grep -E "RAG_TOP_K|RAG_PROMPT_CHUNK_CHARS|OLLAMA_NUM_CTX|MAX_CHAPTER_ARTICLES" .env
```

應看到：
```
RAG_TOP_K=3
RAG_PROMPT_CHUNK_CHARS=500
OLLAMA_NUM_CTX=4096
MAX_CHAPTER_ARTICLES=8
```

`RAG_TOP_K=3` 搭配章節自動擴展：一般問題取 3 筆最相關結果，涉及整章的問題自動帶入同章條文（上限 `MAX_CHAPTER_ARTICLES=8`）。`OLLAMA_NUM_CTX=4096` 可容納擴展上限後的法規內容。

---

## 首次啟動慢

### 症狀：`make up` 後等了好幾分鐘還沒好

**正常情況！** 首次啟動有幾個耗時步驟：

| 步驟 | 時間 |
|------|------|
| Docker build（下載 Python 套件） | 2-5 分鐘 |
| 下載 `intfloat/multilingual-e5-large-instruct` 模型（~2.2 GB） | 3-8 分鐘 |
| SafeChat 背景預熱（multilingual-e5-large-instruct + ChromaDB 初始化） | 4-5 分鐘 |
| **總計** | **3-8 分鐘** |

Ollama 模型（~4.7 GB）需另外下載：
```bash
ollama pull llama3.1:8b    # 或 .env 中指定的模型，首次約 5-15 分鐘
```

重啟後模型需重新載入（約 4-5 分鐘），期間會顯示載入頁面，就緒後自動跳轉。Docker image 和 Ollama 模型已 cached，不需重新下載。

**追蹤進度：**
```bash
docker compose logs -f safe-chat    # 看 app 啟動進度
docker compose ps                   # 看服務狀態
```

---

## 磁碟空間問題

### 症狀：`/api/health` 顯示 `disk: {ok: false}`

**原因：** 磁碟剩餘 < 100 MB

**檢查：**
```bash
df -h /                              # host 磁碟
docker system df                     # Docker 占用
du -sh ./data/                       # SafeChat 資料
du -sh ~/.ollama/models/             # Ollama 模型檔
```

**清理：**
```bash
docker system prune -a              # 刪除未使用的 image/container/network
find data/uploads -mtime +30 -delete  # 刪 30 天前的上傳檔
```

---

## 埠口衝突

### 症狀：`make up` 報錯 `bind: address already in use`

**檢查：**
```bash
lsof -i :8000
```

**解法：改 compose 埠號**
```yaml
services:
  safe-chat:
    ports:
      - "8001:8000"                # 改成 8001
```

---

## 快速自我檢查清單

部署完跑一遍確認沒問題：

```bash
# 1. Ollama 模型狀態
ollama ps
# 預期：.env 中指定的模型已載入，UNTIL: Forever

# 2. 容器狀態
docker compose ps
# 預期：safe-chat (healthy)

# 3. API 健康
curl http://localhost:8000/api/health | jq .
# 預期：{"status":"ok", ...}

# 4. 端對端測試
make test

# 5. UI 能打開
open http://localhost:8000
```

任何一步失敗，依上述章節排查。

---

## API 回傳 401 Unauthorized

### 症狀：所有 API 請求都回 `{"detail": "未授權：請提供有效的 API Key。"}`

**原因：** `.env` 中設定了 `API_KEY`，但請求未帶正確的 Authorization 標頭。

**解法：**

```bash
# 確認 API_KEY 設定
grep API_KEY .env

# 測試帶 API Key 的請求
curl -H "Authorization: Bearer <你的API_KEY>" http://localhost:8000/api/stats
```

**前端使用者：** 在瀏覽器 Console 設定 API Key：
```javascript
sessionStorage.setItem('safechat_api_key', '你的API_KEY')
```
然後重新整理頁面。

**若不需要 API Key 驗證：** 將 `.env` 中的 `API_KEY=` 留空，重啟服務即可。

---

## API 回傳 403 Forbidden（清空知識庫）

### 症狀：點「清空知識庫」後顯示「管理員 token 無效」或「此功能已停用」

**原因 A：** `ADMIN_TOKEN` 未設定 → 功能被停用

```bash
grep ADMIN_TOKEN .env
# 若為空，設定一個：
echo "ADMIN_TOKEN=$(openssl rand -hex 16)" >> .env
docker compose restart safe-chat
```

**原因 B：** 輸入的 token 不正確 → 向 IT 確認正確的 Admin Token

---

## API 回傳 429 Too Many Requests

### 症狀：頻繁操作後回傳「請求過於頻繁，請稍後再試。」

**原因：** 超過速率限制（預設 `RATE_LIMIT=30/minute`，可在 `.env` 調整）

**解法：** 等待 1 分鐘後重試。若正常使用下頻繁觸發，可能有多人共用同一 IP（NAT 環境），可在 `.env` 調高 `RATE_LIMIT`。

---

## 上傳檔案回傳 413

### 症狀：上傳 PDF 時顯示「檔案過大」

**原因：** 檔案超過 `MAX_UPLOAD_MB` 設定的上限

**解法：** 在 `.env` 調高 `MAX_UPLOAD_MB`（例如 `MAX_UPLOAD_MB=50`），然後 `docker compose restart safe-chat`。若使用 nginx 反向代理，也需調整 `client_max_body_size`。

---

## 還是搞不定？

- **GitHub Issue** → 附上 `docker compose logs > logs.txt` 和 `ollama ps` 的輸出
- **Stack Overflow** → 用 `ollama safechat` 搜尋
