#!/usr/bin/env python3
"""
fetch_laws.py — 批次從全國法規資料庫抓取法規條文，存成 SafeChat 可匯入的 txt 檔
===================================================================================

資料來源：
  1. 法務部全國法規資料庫官方 Open API（優先，資料最新）
     https://law.moj.gov.tw/api/
  2. kong0107/mojLawSplitJSON（備援，單檔 JSON 較輕量）
     https://kong0107.github.io/mojLawSplitJSON/

用法：
  # 抓預設的營建工安相關法規（建議 SafeChat 匯入這組）
  python fetch_laws.py

  # 抓指定的法規 pcode
  python fetch_laws.py --pcodes N0060014 N0060001 N0060009

  # 指定輸出目錄
  python fetch_laws.py --out ./my_laws/

  # 強制使用備援鏡像（官方 API 被擋時）
  python fetch_laws.py --source mirror

pcode 對照（營建工安常用）：
  N0060001  職業安全衛生法                 ← 母法
  N0060009  職業安全衛生法施行細則
  N0060004  職業安全衛生設施規則           ← 所有產業共通
  N0060014  營造安全衛生設施標準           ← 營建產業核心（最重要）
  N0060010  職業安全衛生管理辦法
  N0060024  勞工健康保護規則
  N0060034  職業安全衛生教育訓練規則
  N0060022  缺氧症預防規則
  N0060041  機械設備器具安全標準
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_laws")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# 官方 API：整包 ZIP 含所有法規。下載一次、解壓、取出要的 pcode
MOJ_LAW_API_URL = "https://law.moj.gov.tw/api/Ch/Law/JSON"    # 法律
MOJ_ORDER_API_URL = "https://law.moj.gov.tw/api/Ch/Order/JSON"  # 法規命令/行政規則
MOJ_API_URL = MOJ_LAW_API_URL  # kept for log messages

# kong0107 備援鏡像：一法規一 JSON 檔
MIRROR_BASE = "https://kong0107.github.io/mojLawSplitJSON/FalVMingLing"

# 預設抓取的營建工安相關 pcode
DEFAULT_PCODES: dict[str, str] = {
    "N0060001": "職業安全衛生法",
    "N0060014": "營造安全衛生設施標準",
    "N0060004": "職業安全衛生設施規則",
    "N0060010": "職業安全衛生管理辦法",
    "N0060009": "職業安全衛生法施行細則",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 60
RETRY_DELAY = 3


# ---------------------------------------------------------------------------
# PCode extraction helper
# ---------------------------------------------------------------------------
def get_pcode(law: dict) -> Optional[str]:
    """Extract PCode from a law dict, checking direct fields then LawURL."""
    pcode = law.get("PCode") or law.get("pcode")
    if pcode:
        return pcode
    url = law.get("LawURL") or law.get("lawURL") or ""
    m = re.search(r"pcode=([A-Z0-9]+)", url, re.IGNORECASE)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Mirror Chinese → English field normalizer
# ---------------------------------------------------------------------------
_ZH_TO_EN: dict[str, str] = {
    "法規名稱": "LawName",
    "法規類別": "LawCategory",
    "最新異動日期": "LawModifiedDate",
    "生效日期": "LawEffectiveDate",
    "法規內容": "LawArticles",
    "法規網址": "LawURL",
    "法規性質": "LawLevel",
}


def _normalize_fields(law: dict) -> dict:
    """將鏡像回傳的中文欄位名稱對應為英文鍵值。"""
    out: dict = {}
    for zh_key, en_key in _ZH_TO_EN.items():
        if zh_key in law:
            out[en_key] = law[zh_key]
    return out


def _normalize_articles(raw_articles: list[dict]) -> list[dict]:
    """將鏡像的條文陣列重組為統一的英文鍵值結構。"""
    articles: list[dict] = []
    for art in raw_articles:
        if "編章節" in art:
            articles.append({
                "ArticleType": "C",
                "ArticleNo": "",
                "ArticleContent": art["編章節"],
            })
        else:
            articles.append({
                "ArticleType": "A",
                "ArticleNo": art.get("條號", ""),
                "ArticleContent": art.get("條文內容", ""),
            })
    return articles


def normalize_law(law: dict) -> dict:
    """將鏡像的中文鍵值法規 JSON 正規化為英文鍵值格式。"""
    if "法規名稱" not in law:
        return law
    out = _normalize_fields(law)
    out["LawArticles"] = _normalize_articles(law.get("法規內容", []))
    return out


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def http_get(url: str, *, binary: bool = False, retries: int = 3):
    """GET with retry logic."""
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.content if binary else resp.text
        except requests.RequestException as e:
            last_err = e
            log.warning("  Attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


# ---------------------------------------------------------------------------
# Source A: Official MOJ API (ZIP of everything)
# ---------------------------------------------------------------------------
def _fetch_zip_laws(url: str) -> list[dict]:
    """Download a MOJ ZIP archive and return its law list."""
    zip_bytes = http_get(url, binary=True)
    log.info("  Downloaded %.1f MB from %s", len(zip_bytes) / 1_048_576, url)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        json_names = [n for n in zf.namelist() if n.lower().endswith(".json")]
        if not json_names:
            raise RuntimeError(f"No JSON file found inside ZIP from {url}")
        with zf.open(json_names[0]) as f:
            data = json.load(f)
    return data.get("Laws") or data.get("laws") or []


def fetch_all_laws_from_api() -> list[dict]:
    """Download laws + orders from the MOJ API and return combined list."""
    log.info("Downloading law archives from MOJ API…")
    log.info("  (法律: %s)", MOJ_LAW_API_URL)
    log.info("  (法規命令: %s)", MOJ_ORDER_API_URL)

    laws = _fetch_zip_laws(MOJ_LAW_API_URL)
    log.info("  法律 archive: %d laws", len(laws))
    orders = _fetch_zip_laws(MOJ_ORDER_API_URL)
    log.info("  法規命令 archive: %d orders", len(orders))

    combined = laws + orders
    log.info("  Combined total: %d", len(combined))
    return combined


def filter_by_pcodes(all_laws: list[dict], pcodes: list[str]) -> list[dict]:
    """Pick out the laws we want by their pcode."""
    wanted = set(pcodes)
    out = []
    for law in all_laws:
        if get_pcode(law) in wanted:
            out.append(law)

    found_pcodes = {get_pcode(l) for l in out}
    missing = wanted - found_pcodes
    if missing:
        log.warning("  Not found in archive: %s", ", ".join(sorted(missing)))

    return out


# ---------------------------------------------------------------------------
# Source B: Mirror (single-law JSON)
# ---------------------------------------------------------------------------
def fetch_law_from_mirror(pcode: str) -> Optional[dict]:
    """Fetch a single law from the kong0107 mirror."""
    url = f"{MIRROR_BASE}/{pcode}.json"
    log.info("  Fetching %s from mirror", pcode)
    try:
        text = http_get(url)
        return normalize_law(json.loads(text))
    except Exception as e:
        log.error("  ✗ Failed to fetch %s: %s", pcode, e)
        return None


# ---------------------------------------------------------------------------
# Formatting: JSON → plain text
# ---------------------------------------------------------------------------
def _format_law_header(law: dict) -> list[str]:
    """產生法規標題區塊：名稱、類別、修正日期、生效日期。"""
    name = (law.get("LawName") or law.get("lawName") or "未知法規").strip()
    category = (law.get("LawCategory") or law.get("lawCategory") or "").strip()
    modified = (law.get("LawModifiedDate") or law.get("lawModifiedDate") or "").strip()
    effective = (law.get("LawEffectiveDate") or law.get("lawEffectiveDate") or "").strip()

    lines: list[str] = [f"# {name}", ""]
    if category:
        lines.append(f"法規類別：{category}")
    if modified:
        lines.append(f"最新修正日期：{modified}")
    if effective:
        lines.append(f"生效日期：{effective}")
    lines.extend(["", "---", ""])
    return lines


def _format_article(art: dict) -> list[str]:
    """格式化單一條文或章節標題，回傳 Markdown 行列表。"""
    art_type = art.get("ArticleType") or art.get("articleType") or "A"

    if art_type == "C":
        chapter = (
            art.get("ArticleChapter") or art.get("ArticleContent")
            or art.get("articleChapter") or art.get("articleContent") or ""
        ).strip()
        return ["", f"## {chapter}", ""] if chapter else []

    no = (art.get("ArticleNo") or art.get("articleNo") or "").strip()
    content = (
        art.get("ArticleContent") or art.get("articleContent") or ""
    ).strip()
    if not content:
        return []
    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r"\n{3,}", "\n\n", content)

    lines: list[str] = []
    if no:
        lines.append(f"### {no}")
    lines.extend([content, ""])
    return lines


def format_law_as_text(law: dict) -> str:
    """將法規 JSON 轉為乾淨的純文字格式，便於後續文本切分。"""
    lines = _format_law_header(law)

    articles = (
        law.get("LawArticles") or law.get("lawArticles")
        or law.get("articles") or []
    )
    if not articles:
        lines.append("（本法規無條文資料）")
        return "\n".join(lines)

    for art in articles:
        lines.extend(_format_article(art))

    return "\n".join(lines).strip() + "\n"


def safe_filename(name: str, pcode: str) -> str:
    """Build a filesystem-safe filename."""
    clean = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    return f"{pcode}_{clean}.txt"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _try_fetch_from_api(pcodes: list[str]) -> list[dict]:
    """嘗試從官方 API 批次下載並篩選指定法規。"""
    all_laws = fetch_all_laws_from_api()
    laws = filter_by_pcodes(all_laws, pcodes)
    log.info("  Matched %d/%d requested laws from API", len(laws), len(pcodes))
    return laws


def _fetch_from_mirror(pcodes: list[str]) -> list[dict]:
    """從備援鏡像逐一抓取指定法規。"""
    log.info("Using mirror source (single JSON per law)")
    laws: list[dict] = []
    for pc in pcodes:
        law = fetch_law_from_mirror(pc)
        if law:
            laws.append(law)
        time.sleep(0.5)
    return laws


def _fetch_laws(pcodes: list[str], source: str) -> list[dict]:
    """依來源策略（api / mirror / auto）選擇抓取方式，回傳法規列表。"""
    if source == "mirror":
        return _fetch_from_mirror(pcodes)

    if source in ("api", "auto"):
        try:
            laws = _try_fetch_from_api(pcodes)
            if laws:
                return laws
        except Exception as e:
            log.warning("API fetch failed: %s", e)
            if source == "api":
                log.error("Exiting (--source=api was explicit).")
                return []
            log.info("Falling back to mirror…")

    return _fetch_from_mirror(pcodes)


def _write_laws_to_disk(laws: list[dict], out_dir: Path) -> int:
    """將法規列表寫入磁碟為純文字檔，回傳成功寫入的數量。"""
    log.info("Writing %d law(s) to disk…", len(laws))
    written = 0
    for law in laws:
        pcode = get_pcode(law) or "UNKNOWN"
        name = (law.get("LawName") or law.get("lawName") or pcode).strip()
        filename = safe_filename(name, pcode)
        path = out_dir / filename
        try:
            text = format_law_as_text(law)
            path.write_text(text, encoding="utf-8")
            size_kb = path.stat().st_size / 1024
            log.info("  ✓ %s  (%.1f KB)", filename, size_kb)
            written += 1
        except Exception as e:
            log.error("  ✗ %s failed: %s", filename, e)
    return written


def run(pcodes: list[str], out_dir: Path, source: str) -> int:
    """主流程：抓取指定法規並寫入輸出目錄。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Output directory: %s", out_dir.resolve())
    log.info("Fetching %d laws: %s", len(pcodes), ", ".join(pcodes))

    laws = _fetch_laws(pcodes, source)
    if not laws:
        log.error("No laws fetched. Aborting.")
        return 1

    written = _write_laws_to_disk(laws, out_dir)
    log.info("Done! %d/%d files written to %s", written, len(laws), out_dir)
    log.info("Next step: upload these .txt files into SafeChat")
    log.info("  1. Start the server:  uvicorn app.main:app --reload")
    log.info("  2. Open http://localhost:8000")
    log.info("  3. Drag the .txt files into the upload zone")
    return 0


def parse_args():
    p = argparse.ArgumentParser(
        description="批次抓取全國法規資料庫法規，存為 SafeChat 可匯入的 txt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--pcodes",
        nargs="+",
        default=list(DEFAULT_PCODES.keys()),
        help=f"法規 pcode 列表（預設：{' '.join(DEFAULT_PCODES.keys())}）",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("data/sample_docs"),
        help="輸出目錄（預設：data/sample_docs）",
    )
    p.add_argument(
        "--source",
        choices=["auto", "api", "mirror"],
        default="auto",
        help="資料來源：auto=先試 API 再備援、api=只用官方 API、mirror=只用鏡像",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args.pcodes, args.out, args.source))
