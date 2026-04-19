# SafeChat 部署指南（IT 維運用）

> **本文件為資訊部門、系統管理員的技術部署與維運文件。**
> 讀者應具備 Linux、Docker、網路基礎知識。

---

## 部署架構總覽

SafeChat 設計為**邊緣部署（Edge Deployment）**優先，適合安裝在工地辦公室的 Mini PC 或區域機房伺服器。

```
┌─────────────────────────────────────────────────┐
│          工地辦公室區域網路                       │
│  ┌───────────────────────────────────────────┐  │
│  │   工地主管電腦 / 平板 / 手機                │  │
│  │   瀏覽器 → http://192.168.X.X:8000         │  │
│  └──────────────────┬────────────────────────┘  │
│                     │                           │
│  ┌──────────────────▼────────────────────────┐  │
│  │     Mini PC / 伺服器（Edge Node）          │  │
│  │                                           │  │
│  │   ┌──────────────────┐  ┌─────────────┐  │  │
│  │   │  SafeChat        │──│  Ollama     │  │  │
│  │   │  (Docker)        │  │  (host)     │  │  │
│  │   │  Port 8000       │  │  Port 11434 │  │  │
│  │   └─────┬────────────┘  └─────────────┘  │  │
│  │         │                                 │  │
│  │   ┌─────▼────────────┐                    │  │
│  │   │  ChromaDB        │                    │  │
│  │   │  (file-based)    │                    │  │
│  │   └──────────────────┘                    │  │
│  │                                           │  │
│  │   Volume: ./data                          │  │
│  └───────────────────────────────────────────┘  │
│                                                 │
│   (選用) 對外只開 HTTPS，建議走內網即可          │
└─────────────────────────────────────────────────┘
```

### 部署模式選擇

| 模式 | 適用場景 | 複雜度 |
|------|---------|-------|
| **A. Docker + host Ollama**（推薦） | 單一工地或辦公室的 Mini PC | ⭐ |
| **B. Python 直接跑 + 外部 Ollama** | 已有 Ollama 伺服器，只部署應用層 | ⭐⭐ |
| **C. Kubernetes** | 多工地集中管理，需高可用 | ⭐⭐⭐⭐ |
| **D. 雲端（Cloud Run / ECS）** | 不顧資安、要彈性擴展 | ⭐⭐⭐ |

本文以 **A 模式** 為主。其他模式見文末附錄。

---

## 硬體需求

### 最低配置

| 元件 | 規格 | 說明 |
|------|------|------|
| CPU | x86_64，4 core 以上 | ARM64（Apple Silicon Mac）也支援 |
| RAM | **16 GB**（最低）/ 32 GB（推薦） | Llama 3.1 8B 約吃 8 GB |
| 儲存 | SSD 50 GB 以上 | 模型 5 GB + 資料 + 系統 |
| 網路 | 1 Gbps 內網 | 對外不需要 |
| 顯卡 | 選用 | NVIDIA GPU 可加速 10x；Mac 可用 Metal GPU |

### 推薦配置（工地辦公室）

- **Mini PC**：Intel NUC 13 Pro / Beelink SER7 / Mac mini M2
- **RAM**：32 GB（預留記憶體給未來升級模型）
- **儲存**：NVMe 1 TB
- **預算**：2-4 萬台幣

### 效能基準（參考）

採用 SSE 串流後，使用者看到**第一個 token 的時間**遠短於完整回答時間：

| 硬體 | 模型 | 首 token（一般問題） | 首 token（章節擴展） | 推論速度 |
|------|------|---------------------|---------------------|---------|
| Mac mini M5 (32GB) + Metal GPU | llama3.1:8b | **~5-10 秒** | **~10-13 秒** | ~40 tok/s |
| Mac mini M2 (16GB) + Metal GPU | llama3.1:8b | ~10-15 秒 | ~15-20 秒 | ~22 tok/s |
| Intel i7-13700K + RTX 4060 | llama3.1:8b | ~5-8 秒 | ~10-15 秒 | ~40 tok/s |
| Intel i5-12400 (16GB)，CPU only | llama3.1:8b | ~20-30 秒 | ~30-50 秒 | ~5 tok/s |

> **效能關鍵設定**：啟用 `OLLAMA_FLASH_ATTENTION=1`（長 context 加速 20-40%）、設定 `OLLAMA_KEEP_ALIVE=-1`（模型常駐記憶體）。RAG 參數 `RAG_TOP_K`、`RAG_PROMPT_CHUNK_CHARS`、`MAX_CHAPTER_ARTICLES`、`OLLAMA_NUM_CTX` 控制 prompt 大小，直接影響首 token 延遲。各參數說明見 `.env.example`。

---

## 部署步驟

### Step 1：安裝 Docker

**Ubuntu 22.04 / 24.04：**
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker  # 或重新登入
```

**Windows / macOS：** 安裝 Docker Desktop (<https://docker.com/products/docker-desktop>)

### Step 2：安裝 Ollama

Ollama 安裝在 host 上，以利用 GPU 加速推論。

**macOS：**
```bash
brew install ollama

# 啟用 Flash Attention（長 context 加速 20-40%，2026 社群標準做法）
launchctl setenv OLLAMA_FLASH_ATTENTION 1
# 模型永不卸載，避免閒置後重載延遲 30-60s
launchctl setenv OLLAMA_KEEP_ALIVE "-1"

brew services start ollama
ollama pull llama3.1:8b       # 約 4.7 GB
```

**Linux：**
```bash
curl -fsSL https://ollama.com/install.sh | sh

# 啟用 Flash Attention + 永不卸載模型
sudo tee -a /etc/environment <<'EOF'
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KEEP_ALIVE=-1
EOF

sudo systemctl enable ollama
ollama pull llama3.1:8b       # 約 4.7 GB
```

確認模型已載入：
```bash
ollama ps
# 應顯示 llama3.1:8b，Mac 上 PROCESSOR 欄位為 100% GPU
```

### Step 3：取得程式碼與安全設定

```bash
git clone https://github.com/<your-org>/safe-chat.git
cd safe-chat
cp .env.example .env
```

依據部署環境修改 `.env`（各參數說明見 `.env.example`）：

```bash
# Docker 部署需改 Ollama 連線位址
OLLAMA_BASE_URL=http://host.docker.internal:11434

# 設定管理員 token（務必修改）
ADMIN_TOKEN=$(openssl rand -hex 16)
```

### Step 4：啟動服務

```bash
make up
# 或者不用 make：
docker compose up -d
```

**背後會自動完成：**
1. 建置 SafeChat image（約 2-3 分鐘）
2. 啟動 SafeChat 容器，連接 host Ollama

首次啟動約 2-3 分鐘（建置 image）。之後重啟只要數秒。

### Step 5：驗證運作

```bash
make status
# 或者
curl http://localhost:8000/api/health | jq .
```

預期輸出：
```json
{
  "status": "ok",
  "components": {
    "llm":    {"ok": true, "backend": "ollama", "model": "llama3.1:8b"},
    "vector": {"ok": true, "chunks": 0},
    "disk":   {"ok": true, "free_mb": 45231}
  }
}
```

開瀏覽器 **http://<伺服器 IP>:8000**

### Step 6：匯入工安法規（可選）

```bash
make fetch-laws
# 或者
docker compose exec safe-chat python scripts/fetch_laws.py
```

完成後從 UI 上傳 `data/sample_docs/*.txt`。

---

## 資安設定

### 內建安全機制

SafeChat 內建多層安全防護，部署前務必修改 `.env` 中的安全相關參數。

所有設定項的用途與調校建議見 **`.env.example`**（唯一權威來源）。

**部署前最少要做：**

```bash
# 在 .env 中設定管理員 token（必要）
ADMIN_TOKEN=$(openssl rand -hex 16)

# 若非封閉內網，建議也設定 API Key
API_KEY=$(openssl rand -hex 16)
```

| 安全機制 | 說明 |
|---------|------|
| **Admin Token** | `/api/reset`（清空知識庫）需帶 `X-Admin-Token` 標頭 |
| **API Key 驗證** | 所有 `/api/*` 端點（`/api/health` 除外）需帶 `Authorization: Bearer <key>` |
| **速率限制** | 每個 IP 的請求頻率上限 |
| **檔案上傳限制** | 大小上限、副檔名白名單（.pdf, .txt, .md）、檔名消毒 |
| **輸入驗證** | 提問長度限制、top_k 範圍限制 |
| **安全標頭** | `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy` |
| **CORS** | 限制跨來源請求 |
| **錯誤資訊隱藏** | API 回應只含通用錯誤訊息，細節僅記錄在 server log |

> 各機制的環境變數名稱、預設值、詳細說明均見 `.env.example`。

### 內網部署（推薦）

**不要**把 SafeChat 直接放到公網。正確做法：

```yaml
# docker-compose.yml — 只綁定內網介面
services:
  safe-chat:
    ports:
      - "192.168.1.100:8000:8000"  # 改成實際內網 IP
```

即使是內網部署，仍建議設定 `ADMIN_TOKEN`（防止誤操作清空知識庫）。
`API_KEY` 在封閉內網可留空；若同一網段有不受信任的設備則建議設定。

### 若必須對外，加上反向代理 + HTTPS

```nginx
# /etc/nginx/sites-available/safechat
server {
    listen 443 ssl http2;
    server_name safechat.yourcompany.com;

    ssl_certificate     /etc/letsencrypt/live/safechat.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/safechat.yourcompany.com/privkey.pem;

    # 限制上傳大小（應用層也有限制，nginx 作為第二道防線）
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;  # 等 LLM 回答
    }

    # SSE 串流端點 — 必須關閉 buffering，否則 token 會被 nginx 攢批推送
    location /api/ask/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
    }

    # 額外鎖定 reset API 只允許公司 IP（應用層已有 Admin Token 驗證）
    location /api/reset {
        allow 203.0.113.0/24;
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }
}
```

對外部署時**務必**設定 `API_KEY`，配合 nginx HTTPS 使用。

### 基本認證（額外防護）

除了應用內建的 API Key 外，也可在 nginx 加 basic auth 做雙層認證：

```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd safechat
```

```nginx
location / {
    auth_basic "SafeChat";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:8000;
}
```

### 未來規劃（正式版）

當專案走向正式產品化時建議加入：
- **SSO 整合**：串 Google Workspace / Azure AD
- **角色權限**：工地主任、作業員、訪客分層
- **Audit log**：記錄誰問了什麼、匯入什麼文件
- **備份策略**：`data/chroma_db/` 每日備份

---

## 維運監控

### 健康檢查端點

```bash
# 一次檢查所有元件
curl http://localhost:8000/api/health | jq .
```

回應範例：

```json
{
  "status": "ok",
  "components": {
    "llm": {
      "ok": true,
      "backend": "ollama",
      "model": "llama3.1:8b"
    },
    "vector": {
      "ok": true,
      "chunks": 847
    },
    "disk": {
      "ok": true,
      "free_mb": 45231
    }
  }
}
```

### 建議的監控設置

**選項 A：簡易腳本（適合單點部署）**

```bash
# /etc/cron.d/safechat-healthcheck
*/5 * * * * root curl -sf http://localhost:8000/api/health | grep -q '"status":"ok"' \
  || echo "SafeChat degraded at $(date)" | mail -s "SafeChat Alert" admin@example.com
```

**選項 B：Prometheus + Grafana（多工地統一監控）**

在 `app/main.py` 加 prometheus_client，暴露 `/metrics`。詳見 Roadmap。

### 日誌查看

```bash
# SafeChat 應用 log
docker compose logs -f safe-chat

# Ollama log（host 服務）
cat /opt/homebrew/var/log/ollama.log    # macOS
journalctl -u ollama -f                 # Linux

# 只看錯誤
docker compose logs safe-chat | grep -E "ERROR|WARN"
```

### 常見問題排解

| 症狀 | 原因 | 解法 |
|------|------|------|
| 啟動時 OOM | RAM 不足 | 改用 `llama3.2:3b` 或加記憶體 |
| 回答極慢 | 無 GPU、模型太大 | 換小模型、或加 GPU |
| `/api/health` 回 `llm.ok: false` | Ollama 沒啟動 | `brew services restart ollama`（macOS）或 `systemctl restart ollama`（Linux） |
| 磁碟滿 | 上傳太多 PDF | 清 `data/uploads/` 舊檔 |
| UI 讀不到 Chunks | ChromaDB 損毀 | 從備份還原 `data/chroma_db/` |

---

## 升級與維護

### 升級 SafeChat

```bash
cd safe-chat
git pull
make rebuild        # 或 docker compose up -d --build
```

### 升級 / 切換 LLM 模型

```bash
# 改 .env 後重新拉模型
vim .env            # OLLAMA_MODEL=llama3.2:3b
ollama pull llama3.2:3b
docker compose restart safe-chat
```

### 定期清理

```bash
# 刪除 30 天前的上傳檔
find data/uploads -mtime +30 -delete

# 清理 Docker 佔用
docker system prune -a
```

### 備份

```bash
# 每日備份向量庫 + 原始文件
tar czf /backup/safechat-$(date +%Y%m%d).tar.gz data/

# 建議保留 30 天，異地備份更佳
```

---

## 進階：多工地統一部署

當有多個工地要導入時：

### 方案 A：各工地獨立部署（最簡單）

每個工地裝一台 Mini PC，各自管理自己的知識庫。**優點：** 資料最隔離、斷網照常運作。**缺點：** 沒中央統計。

### 方案 B：中央 LLM + 邊緣向量庫

中央機房一台大伺服器跑 Ollama（可共享），各工地只跑 SafeChat app。適合內網互通的企業。

```yaml
# 工地端的 .env
USE_OLLAMA=1
OLLAMA_BASE_URL=http://central-llm.corp.example:11434
```

### 方案 C：Kubernetes 集中管理

```yaml
# 簡化版 k8s manifest
apiVersion: apps/v1
kind: Deployment
metadata:
  name: safe-chat
spec:
  replicas: 3
  selector:
    matchLabels: {app: safe-chat}
  template:
    metadata:
      labels: {app: safe-chat}
    spec:
      containers:
      - name: safe-chat
        image: safe-chat:latest
        env:
        - name: USE_OLLAMA
          value: "1"
        - name: OLLAMA_BASE_URL
          value: "http://ollama-service:11434"
```

---

## 部署檢查清單

完成下列項目代表部署 OK：

### 基本功能
- [ ] `ollama ps` 顯示模型已載入（Mac 應為 100% GPU）
- [ ] `docker compose ps` 顯示 `safe-chat` 為 `running (healthy)`
- [ ] `make status`（或 `curl http://localhost:8000/api/health`）回 `status: ok`
- [ ] `make test` 端對端測試通過
- [ ] `make test-quality` RAG 品質回歸測試通過（20 題，已知限制標記為 xfail）
- [ ] 瀏覽器能打開 UI
- [ ] 確認輸入框下方顯示免責聲明
- [ ] 測試上傳一份 PDF 成功
- [ ] 測試提問能得到帶來源標籤的回答
- [ ] 停止 Ollama（`brew services stop ollama`），提問仍能看到檢索結果（**降級模式測試**）
- [ ] 重啟 Ollama 後，健康狀態恢復綠燈

### 安全設定
- [ ] `.env` 中 `ADMIN_TOKEN` 已設定為強隨機字串（`openssl rand -hex 16`）
- [ ] 若對外部署：`API_KEY` 已設定
- [ ] 測試：無 Admin Token 的 `DELETE /api/reset` 回傳 403
- [ ] 測試：上傳超過 `MAX_UPLOAD_MB` 的檔案回傳 413
- [ ] 若設定 `API_KEY`：無 Bearer token 的 API 請求回傳 401

### 維運
- [ ] 設定定期備份 cron
- [ ] 設定健康檢查告警

---

## 支援

- **程式碼 Issue** → GitHub repo issues
- **使用問題** → 參考 `PLAYBOOK.md`
- **架構/擴展諮詢** → 聯繫數位團隊
