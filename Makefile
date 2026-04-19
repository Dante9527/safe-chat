# ============================================================================
# SafeChat — Makefile
# ============================================================================
# 常用指令：
#   make up         一鍵啟動整組服務（會自動下載模型、匯入測試法規）
#   make logs       查看所有服務 log
#   make status     檢查系統健康
#   make down       停止所有服務
#   make clean      停止並刪除所有資料（慎用）
#   make shell      進入 SafeChat 容器
#   make test       測試 RAG 端對端流程
# ============================================================================

.PHONY: help up build down logs logs-app status clean shell test fetch-laws pull-model rebuild lint format format-check typecheck

# 預設顯示說明
help:
	@echo "SafeChat — 一鍵部署工具"
	@echo ""
	@echo "快速開始："
	@echo "  make build      建立 / 更新 image（首次或改依賴時執行）"
	@echo "  make up         啟動所有服務（不重 build，秒啟）"
	@echo "  make status     檢查部署狀態"
	@echo "  open http://localhost:8000"
	@echo ""
	@echo "其他指令："
	@echo "  make logs           查看所有服務 log"
	@echo "  make logs-app       只看 SafeChat app log"
	@echo "  make down           停止所有服務（保留資料）"
	@echo "  make clean          停止並刪除所有資料（含向量庫）"
	@echo "  make shell          進入 SafeChat 容器"
	@echo "  make test           端對端測試 RAG 流程"
	@echo "  make fetch-laws     抓取工安法規到 data/sample_docs/"
	@echo "  make pull-model     下載/更新 Ollama 模型（host）"
	@echo "  make rebuild        清除所有 cache 並重新 build"

# ---------------------------------------------------------------------------
# 核心指令
# ---------------------------------------------------------------------------
build:
	@echo "🔨 Build SafeChat image..."
	@if [ ! -f .env ]; then \
		echo "📝 未偵測到 .env，自動從 .env.example 複製..."; \
		cp .env.example .env; \
	fi
	DOCKER_BUILDKIT=1 docker compose build

up:
	@echo "🚀 啟動 SafeChat..."
	@if [ ! -f .env ]; then \
		echo "📝 未偵測到 .env，自動從 .env.example 複製..."; \
		cp .env.example .env; \
	fi
	docker compose up -d
	@echo ""
	@echo "⏳ 等待服務就緒..."
	@echo "   用 'make logs' 追蹤進度"
	@echo ""
	@sleep 5
	@$(MAKE) --no-print-directory status
	@echo ""
	@echo "✅ 啟動完成後請開啟: http://localhost:8000"

down:
	@echo "🛑 停止 SafeChat 服務..."
	docker compose down
	@echo "✅ 已停止（資料保留於 ./data）"

clean:
	@echo "⚠️  這會刪除所有資料（含向量庫、已下載模型），確定嗎？[y/N]"
	@read -r REPLY; \
	if [ "$$REPLY" = "y" ] || [ "$$REPLY" = "Y" ]; then \
		docker compose down -v; \
		rm -rf data/uploads/* data/chroma_db/*; \
		echo "✅ 已完全清除"; \
	else \
		echo "❌ 取消"; \
	fi

rebuild:
	docker compose down
	DOCKER_BUILDKIT=1 docker compose build --no-cache
	docker compose up -d

# ---------------------------------------------------------------------------
# 監控與除錯
# ---------------------------------------------------------------------------
logs:
	docker compose logs -f

logs-app:
	docker compose logs -f safe-chat

status:
	@echo "📊 SafeChat 狀態"
	@echo "================"
	@docker compose ps
	@echo ""
	@echo "🏥 健康檢查："
	@curl -sf http://localhost:8000/api/health 2>/dev/null | python3 -m json.tool 2>/dev/null \
		|| echo "   ⚠️  服務尚未就緒，請稍候再試 (make logs 查看進度)"

shell:
	docker compose exec safe-chat /bin/bash

# ---------------------------------------------------------------------------
# 輔助指令
# ---------------------------------------------------------------------------
fetch-laws:
	@echo "📥 抓取工安法規..."
	docker compose exec safe-chat python scripts/fetch_laws.py
	@echo "✅ 法規已存入 data/sample_docs/，可從 UI 上傳"

pull-model:
	@MODEL=$${OLLAMA_MODEL:-llama3.1:8b}; \
	echo "📥 下載模型 $$MODEL..."; \
	ollama pull $$MODEL

test:
	@echo "🧪 執行端對端測試..."
	@echo ""
	@echo "[1/3] 檢查健康端點..."
	@curl -sf http://localhost:8000/api/health > /dev/null \
		&& echo "    ✅ /api/health OK" \
		|| (echo "    ❌ /api/health 失敗" && exit 1)
	@echo ""
	@echo "[2/3] 檢查知識庫統計..."
	@curl -sf http://localhost:8000/api/stats | python3 -m json.tool
	@echo ""
	@echo "[3/3] 測試一個 RAG 查詢..."
	@curl -sf -X POST http://localhost:8000/api/ask \
		-H "Content-Type: application/json" \
		-d '{"question":"施工架搭設有哪些安全規範？"}' \
		| python3 -m json.tool | head -30
	@echo ""
	@echo "✅ 測試完成"

test-quality:
	@echo "🧪 執行 RAG 品質回歸測試（20 題）..."
	@python3 tests/test_rag_quality.py

# ---------------------------------------------------------------------------
# 程式碼品質
# ---------------------------------------------------------------------------
lint:
	ruff check app/ tests/ scripts/

format:
	ruff format app/ tests/ scripts/

format-check:
	ruff format --check app/ tests/ scripts/

typecheck:
	pyright app/
