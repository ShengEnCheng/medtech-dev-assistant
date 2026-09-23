"""TFDA 品名反查：先用功能詞撈出真實品名，回饋給 LLM 修正候選。

## 問題

實測發現 LLM 抽的 TFDA 中文候選常對不上實際資料庫：
  心衰竭案 LLM 提「心臟血管監測器」→ 0 筆
          LLM 提「睡眠生理監測儀」→ 1 筆
          但資料庫實際有「生理監視器」39 筆（LLM 沒提這個）

LLM 不知道台灣 TFDA 的品名慣用語，只能用一般常識猜。

## 解法：用實際資料回饋

1. 先用「功能／部位」中文詞撈出 TFDA 真實品名
2. 把這些真實品名交給 LLM，讓它選出最接近的
3. 候選改為 LLM 從真實品名中挑選 → 命中率大幅提升

這樣 LLM 的工作從「憑空生成品名」變成「從真實清單中挑選」，
後者準確得多，而且**不可能編造出不存在的品名**。
"""

from __future__ import annotations


def fetch_real_product_names(db_path, seed_terms: list[str],
                             limit: int = 60) -> list[dict]:
    """用種子詞撈出 TFDA 真實品名（供 LLM 挑選）。

    種子詞用「部位＋功能」的通用詞（心臟、監視、監測、感測…），
    目的是把「可能的品名空間」撈出來，不是精確檢索。

    回傳 [{"name":..., "category":...}, ...]
    """
    import sqlite3

    if not db_path or not getattr(db_path, "exists", lambda: False)():
        return []

    seen: set[str] = set()
    out: list[dict] = []
    try:
        con = sqlite3.connect(db_path)
        try:
            for t in (seed_terms or []):
                t = (t or "").strip()
                if len(t) < 2:
                    continue
                try:
                    rows = con.execute(
                        "SELECT name_zh, category FROM lic "
                        "WHERE (name_zh LIKE ? OR category LIKE ?) "
                        "AND (cancelled IS NULL OR cancelled = '' "
                        "     OR cancelled = '未註銷') "
                        "LIMIT ?",
                        (f"%{t}%", f"%{t}%", limit)).fetchall()
                except Exception:
                    continue
                for name, cat in rows:
                    name = (name or "").strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    out.append({"name": name, "category": (cat or "").strip()})
        finally:
            con.close()
    except Exception:
        return []
    return out


def clean_query_term(name: str) -> str:
    """清理 TFDA 檢索詞：去品牌、去附註、去空格。

    **為什麼必須做**（實測嚴重 bug）：
    TFDA 品名格式為「“品牌” 核心品名 (附註)」，含空格。
    檢索函式會把查詢按空格拆成多詞並以 OR 連接，
    於是「“凱琳” 女用尿濕感測器 (未滅菌)」被拆成
    ['“凱琳”', '女用尿濕感測器', '(未滅菌)'] ——
    其中「未滅菌」單獨命中的許可證極多，
    結果該檢索詞回報 200 筆，台灣競品暴增到 14,676 件。

    清理後：「女用尿濕感測器」→ 正確命中 1 筆。
    """
    import re
    n = (name or "").strip()
    n = re.sub(r"^[“”\"\'‘’]+[^“”\"\'‘’]{1,20}[“”\"\'‘’]+\s*", "", n)
    n = re.sub(r"[（(][^）)]{0,24}[）)]", "", n)
    n = re.sub(r"\s+", "", n)          # 中文品名不該有空格
    n = n.strip(" -—·、,;")
    return n


def core_terms(name: str) -> list[str]:
    """從 TFDA 品名萃取「去品牌的核心詞」。

    為什麼需要：TFDA 品名格式是「“品牌”核心品名（附註）」，
    例如「“維塔康” 超音波膀胱掃描儀」。直接拿全名檢索只能命中
    自己那 1 筆（實測確認），但拿「超音波膀胱掃描儀」檢索
    才能撈到**整類**既有產品 —— 這才是競品分析要的。

    回傳依長度降序的核心詞候選（長的優先，越具體）。
    """
    import re
    n = clean_query_term(name)
    n = re.sub(r"\s*(及配件|暨配件|套件)\s*$", "", n)
    n = n.strip(" -—·")

    out: list[str] = []
    if len(n) >= 4:
        out.append(n)
    # 取尾段（核心名詞通常在後面）：超音波膀胱掃描儀 → 膀胱掃描儀
    for cut in range(1, min(4, len(n) - 2)):
        sub = n[cut:]
        if len(sub) >= 4:
            out.append(sub)
    # 去重保序
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))][:4]


def pick_from_real(product, real_names: list[dict],
                   timeout: int = 60) -> dict:
    """讓 LLM 從真實品名清單中挑選最接近的（而非憑空生成）。

    回傳 {"picks": [...], "reason": "..."} ；失敗回 {}
    """
    if not real_names:
        return {}

    from . import llm_terms

    base, key, model = llm_terms._config()
    if not key:
        return {}

    names_txt = "\n".join(f"{i + 1}. {r['name']}（{r['category'][:30]}）"
                          for i, r in enumerate(real_names[:60]))
    prompt = (
        "以下是台灣 TFDA 醫材許可證資料庫中已存在的真實品名。\n"
        "請從中選出**最接近**下列產品說明的品名（最多 3 個）。\n\n"
        "只輸出 JSON：{\"picks\":[\"品名1\",\"品名2\"],\"reason\":\"簡短理由\"}\n\n"
        "規則：\n"
        "1. **只能從清單中挑選，不可自行創造或修改品名。**\n"
        "2. 挑「用途或量測標的相近」的，不必完全相同（創新產品本來就沒有對應品名）。\n"
        "3. 若清單中確實沒有相近的，picks 給空陣列。\n\n"
        f"【真實品名清單】\n{names_txt}\n\n"
        f"【產品說明】\n{(product or '')[:2000]}"
    )

    import json
    import re
    import urllib.request
    try:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1, "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{base.rstrip('/')}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception:
        return {}

    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {}

    valid = {r["name"] for r in real_names}
    picks = [p for p in (d.get("picks") or [])
             if isinstance(p, str) and p in valid]

    # 附上去品牌的核心詞：檢索時用核心詞才能撈到整類產品，
    # 直接用品名全名只會命中它自己 1 筆（實測確認）。
    cores: list[str] = []
    for p in picks:
        for c in core_terms(p):
            if c not in cores:
                cores.append(c)

    return {"picks": picks[:3], "cores": cores[:6],
            "reason": str(d.get("reason", ""))[:200]}
