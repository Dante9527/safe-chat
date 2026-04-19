"""
RAG 回答品質回歸測試。

每題定義「必須命中的條號」，驗證回答中是否有引用。
用於上線前檢查和版本更新後的回歸驗證。

執行方式：
    make test-quality
    # 或
    python tests/test_rag_quality.py [--base-url http://localhost:8000]

已知限制（Llama 3.1 8B 結構性限制）：
    - 語意相近但不同領域的條文可能被誤檢索（如「高空」→ 呼吸防護而非墜落防護）
    - 8B 模型在多條法規間的相關性判斷力有限
    - LLM 回答措辭可能與法條原文不同（如「護蓋」→「加蓋」）
    標記為 xfail 的測試案例即為此類限制的已知案例。
    must_have 支援 tuple 同義詞（任一命中即可），用於處理措辭差異。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE_URL = "http://localhost:8000"

# (問題, 必須命中的條號/關鍵字, 不應出現的條號/關鍵字, 是否為已知限制)
# must_have 元素為 str 時須完全命中；為 tuple[str, ...] 時任一命中即可（同義詞）。
TEST_CASES: list[tuple[str, list[str | tuple[str, ...]], list[str], bool]] = [
    # --- 施工架 ---
    (
        "施工架搭設有哪些安全規範？",
        ["第 39 條", "施工架"],
        [],
        False,
    ),
    (
        "施工架高度超過多少公尺需要設置護欄？",
        ["二公尺"],
        [],
        False,
    ),
    (
        "鋼管施工架的設置規定有哪些？",
        ["第 59 條", "CNS"],
        [],
        False,
    ),
    # --- 墜落防護 ---
    (
        "高處作業的安全規定有哪些？",
        ["第 17 條", "墜落"],
        [],
        False,
    ),
    (
        "護欄的設置規格為何？",
        ["九十公分", "第 20 條"],
        [],
        False,
    ),
    (
        "安全網的設置規定？",
        ["第 22 條"],
        [],
        False,
    ),
    (
        "安全帶的使用規定有哪些？",
        ["安全帶"],
        [],
        False,
    ),
    # --- 開口 / 開挖 ---
    (
        "開口部分應如何防護？",
        [("護蓋", "加蓋", "封閉")],
        [],
        False,
    ),
    (
        "露天開挖作業的安全規定？",
        ["開挖"],
        [],
        False,
    ),
    # --- 模板支撐 ---
    (
        "模板支撐的安全規定有哪些？",
        ["模板"],
        [],
        False,
    ),
    # --- 屋頂作業 ---
    (
        "屋頂作業的安全措施有哪些？",
        ["第 18 條", "屋頂"],
        [],
        False,
    ),
    # --- 通用安全 ---
    (
        "工作場所應如何設置圍籬？",
        ["第 8 條", "圍籬"],
        [],
        False,
    ),
    (
        "勞工墜落災害防止計畫應包含哪些內容？",
        ["第 17 條"],
        [],
        False,
    ),
    (
        "警示線的設置規定？",
        ["第 24 條", "警示線"],
        [],
        False,
    ),
    (
        "施工架上物料堆放有什麼限制？",
        ["第 46 條", "荷重"],
        [],
        False,
    ),
    # --- 跨法規 ---
    (
        "高度二公尺以上作業場所的防護設備？",
        [("護欄", "欄杆", "女兒牆")],
        [],
        False,
    ),
    (
        "營造工程的危害調查評估規定？",
        ["調查"],
        [],
        False,
    ),
    (
        "施工架組配作業主管的職責？",
        ["第 41 條", "作業主管"],
        [],
        False,
    ),
    # --- 語意檢索邊緣案例（top_k=5 已解決，不再是 xfail） ---
    (
        "勞工從事高空作業需要什麼防護設備？",
        ["安全帶"],
        ["防毒面具", "呼吸器"],
        False,
    ),
    (
        "電氣作業的防護措施有哪些？",
        ["電氣"],
        [],
        False,
    ),
]


def ask(question: str, base_url: str = BASE_URL) -> dict:
    """Send question to /api/ask and return response."""
    req = urllib.request.Request(
        f"{base_url}/api/ask",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode())
        except (ConnectionError, OSError):
            if attempt < 2:
                time.sleep(5)
            else:
                raise


def run_tests(base_url: str = BASE_URL) -> None:
    passed = 0
    failed = 0
    xfail = 0
    xpass = 0
    total = len(TEST_CASES)

    print(f"SafeChat RAG 品質回歸測試 ({total} 題)")
    print(f"Base URL: {base_url}")
    print("=" * 70)
    print()

    for i, (question, must_have, must_not_have, is_known_limit) in enumerate(
        TEST_CASES, 1
    ):
        start = time.time()
        try:
            result = ask(question, base_url)
        except Exception as e:
            print(f"[{i:2d}] FAIL (request error): {question}")
            print(f"     Error: {e}")
            failed += 1
            continue
        elapsed = time.time() - start
        answer = result.get("answer", "")

        missing = [
            kw
            for kw in must_have
            if (
                isinstance(kw, tuple)
                and not any(syn in answer for syn in kw)
            )
            or (isinstance(kw, str) and kw not in answer)
        ]
        unwanted = [kw for kw in must_not_have if kw in answer]

        ok = not missing and not unwanted

        if ok and is_known_limit:
            tag = "XPASS"
            xpass += 1
        elif ok:
            tag = "PASS"
            passed += 1
        elif is_known_limit:
            tag = "XFAIL"
            xfail += 1
        else:
            tag = "FAIL"
            failed += 1

        print(f"[{i:2d}] {tag:5s} ({elapsed:.1f}s) {question}")
        if missing:
            print(f"     缺少: {missing}")
        if unwanted:
            print(f"     不應出現: {unwanted}")

    print()
    print("=" * 70)
    print(
        f"結果: {passed} passed, {failed} failed, "
        f"{xfail} xfail, {xpass} xpass / {total} total"
    )
    print()
    if failed:
        print("⚠  有測試失敗，請檢查回答品質。")
        sys.exit(1)
    else:
        print("所有測試通過（xfail 為已知限制，不計為失敗）。")


if __name__ == "__main__":
    url = BASE_URL
    for arg in sys.argv[1:]:
        if arg.startswith("--base-url"):
            url = sys.argv[sys.argv.index(arg) + 1]
        elif not arg.startswith("--"):
            url = arg
    run_tests(url)
