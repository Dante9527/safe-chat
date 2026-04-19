# Scripts

本目錄放置 SafeChat 的輔助腳本。

## `fetch_laws.py` — 批次抓取工安法規

從**全國法規資料庫**（法務部）抓取法規條文，自動格式化為 Markdown 結構的 `.txt` 檔，可直接拖進 SafeChat 匯入。

### 資料來源

腳本採雙來源策略，自動 fallback：

1. **官方 Open API**（優先）— 分兩包下載：
   - `https://law.moj.gov.tw/api/Ch/Law/JSON`（法律，~6 MB）
   - `https://law.moj.gov.tw/api/Ch/Order/JSON`（法規命令，~25 MB）
   下載 ZIP 後本地解壓、合併，再篩選需要的 pcode
2. **社群鏡像**（備援）— `kong0107/mojLawSplitJSON`
   單一法規一份 JSON，檔案小、速度快

### 快速使用

```bash
# 本地執行
pip install requests
python scripts/fetch_laws.py

# 或在 Docker 部署中
make fetch-laws
```

執行後 `data/sample_docs/` 會產生：

```
N0060001_職業安全衛生法.txt
N0060004_勞工作業場所容許暴露標準.txt
N0060009_職業安全衛生設施規則.txt
N0060010_職業安全衛生教育訓練規則.txt
N0060014_營造安全衛生設施標準.txt
```

> **注意**：法務部法規資料庫的 pcode 對應法規名稱會隨法規異動更新，實際檔名以執行時 API 回傳的名稱為準。

### 進階參數

```bash
# 自訂 pcode 清單
python scripts/fetch_laws.py --pcodes N0060014 N0060001

# 改輸出目錄
python scripts/fetch_laws.py --out ./my_laws/

# 強制用備援鏡像（官方 API 慢或被擋時）
python scripts/fetch_laws.py --source mirror

# 只用官方 API（不 fallback）
python scripts/fetch_laws.py --source api
```

### pcode 對照表（營建工安常用）

| pcode | 法規名稱（2025/12 實查） | 用途 |
|-------|---------|------|
| `N0060001` | 職業安全衛生法 | 母法（通報、罰則、責任） |
| `N0060009` | 職業安全衛生設施規則 | 所有產業共通設施標準 |
| `N0060004` | 勞工作業場所容許暴露標準 | 化學品暴露限值 |
| `N0060014` | **營造安全衛生設施標準** | **營建核心（最重要）** |
| `N0060010` | 職業安全衛生教育訓練規則 | 教育訓練要求 |
| `N0060024` | 勞工健康保護規則 | 職業病、健康檢查 |
| `N0060034` | 職業安全衛生教育訓練規則（細則） | 教育訓練細節 |
| `N0060022` | 缺氧症預防規則 | 侷限空間作業 |
| `N0060041` | 職業災害勞工保護法 | 職災補償與復工 |

> pcode 與法規名稱的對應以[全國法規資料庫](https://law.moj.gov.tw/)為準，如有異動請重新確認。

要找更多 pcode：上 <https://law.moj.gov.tw/>，找到法規條文頁，網址末尾 `?pcode=XXXXXXXX` 就是。

### 輸出格式範例

```markdown
# 營造安全衛生設施標準

法規類別：勞動部
最新修正日期：20210706
生效日期：20220101

---

## 第 一 章 總則

### 第 1 條
本標準依職業安全衛生法第六條第三項規定訂定之。

### 第 2 條
本標準適用於從事營造作業之有關事業。
```

這個格式對 RAG 很友善——`RecursiveCharacterTextSplitter` 會優先在 `\n\n` 切分，確保一條條文不會被攔腰切斷，而標題層級也方便 LLM 引用具體條號。

### 完整 Demo 工作流

```bash
# 1. 抓法規
python scripts/fetch_laws.py

# 2. 啟動 SafeChat
uvicorn app.main:app --reload

# 3. 開瀏覽器 http://localhost:8000
#    把 data/sample_docs/*.txt 拖進左側上傳區

# 4. 提問測試：
#    「施工架搭設有哪些安全規範？」
#    「墜落事故的通報流程？」
```
