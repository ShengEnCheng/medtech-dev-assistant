"""M5 論文先前技術模組（non-patent literature）。

對應 Biodesign Stage 4.1（智財保護）。專利模組只看專利，但實務上
**破壞新穎性的先前技術有一大部分是論文**，而且校園團隊最常見的
致命風險是「自己的老師已發表過」。

本模組產出四塊：
  1. 可能構成先前技術的論文清單（含預印本標記與發表日期）
  2. 機構自我碰撞 —— 長庚體系自己的論文（最高風險項）
  3. 各國優惠期對照表（學術發表是否適用，各國差異極大）
  4. 時序建議（申請在先、發表在後）

**不做法律判斷**：是否真的構成新穎性障礙需比對申請專利範圍與
論文的技術揭露，屬專利師專業範圍。本模組只攤證據與法源。
"""

from __future__ import annotations

from . import source_papers as sp
from .schema import PaperResult


def _risk_flag(paper: dict, cutoff_year: int | None = None) -> str:
    """標示風險等級（僅依型別與日期，非法律判斷）。"""
    t = paper.get("type", "")
    if t in ("preprint", "posted-content"):
        return "高"
    if t in ("proceedings-article", "proceedings", "dissertation"):
        return "中"
    return "低"


def run(product: str, query: str | None = None,
        limit: int = 12, include_arxiv: bool = True,
        plan: dict | None = None) -> PaperResult:
    """執行論文先前技術檢索。

    參數
    ----
    product : 產品說明原文（記錄用，也是驗證概念詞的來源）
    query   : 英文檢索詞（單一詞；未提供 plan 時的退路）
    plan    : 檢索詞計畫。提供時改走「多候選 + 自動驗證」流程 ——
              依序試多個候選詞，用文件中的中英對照驗證結果，
              通過才採用。理由：檢索詞錯了整份報告就錯了，
              且實測多個候選會撈到不同文獻（詞形差異導致）。
    """
    res = PaperResult(product=product, query=query or "")

    if not query and not plan:
        res.degraded = True
        res.note = "未提供英文檢索詞，無法查詢論文先前技術"
        return res

    # 走驗證流程（有 plan 時）
    verified = None
    if plan:
        try:
            from . import relevance
            verified = relevance.search_verified(
                product, plan,
                search_fn=lambda q, lim: sp.search_papers(q, limit=lim),
                limit=limit, deep=True,
            )
            if verified.get("chosen"):
                res.query = verified["chosen"]
                query = verified["chosen"]
        except Exception:
            verified = None

    if not query:
        res.degraded = True
        res.note = "無可用檢索詞"
        return res

    if verified and verified.get("papers"):
        # 驗證流程已取得結果，直接組裝（避免重複查詢）
        papers = verified["papers"]
        data = {"papers": papers, "counts": {}, "errors": [],
                "self_collision": []}
        try:
            data["self_collision"] = sp.self_collision_check(query, limit=6)
        except Exception:
            pass
    else:
        try:
            data = sp.search_papers_all(query, limit=limit)
        except Exception as exc:
            res.degraded = True
            res.note = f"論文檢索失敗：{type(exc).__name__}: {str(exc)[:120]}"
            return res

    papers = data.get("papers") or []
    for p in papers:
        p = dict(p)
        p["risk"] = _risk_flag(p)
        res.papers.append(p)

    res.counts = data.get("counts") or {}
    res.errors = data.get("errors") or []
    res.self_collision = data.get("self_collision") or []
    res.preprint_count = sum(
        1 for p in papers
        if p.get("type") in ("preprint", "posted-content"))
    res.grace_periods = sp.grace_period_matrix()
    res.order_note = sp.submission_order_note()

    # 檢索品質資訊（供報告與介面顯示）
    if verified:
        res.relevance = verified.get("relevance") or {}
        res.verified_query = verified.get("chosen") or ""
        res.query_source = verified.get("chosen_source") or ""
        res.attempts = verified.get("attempts") or []
        res.verify_note = verified.get("note") or ""
        res.verify_warning = verified.get("warning") or ""

    if res.self_collision:
        res.note = (f"偵測到長庚體系 {len(res.self_collision)} 篇同主題論文，"
                    "請優先確認是否構成己方先前技術")
    elif not papers:
        res.degraded = True
        res.note = "各來源均未回傳結果，請調整檢索詞"
    else:
        res.note = f"共 {len(papers)} 篇，其中預印本 {res.preprint_count} 篇"

    return res


def to_rows(result: PaperResult) -> list[dict]:
    """轉為介面用的列（供 st.dataframe）。"""
    rows = []
    for p in result.papers:
        rows.append({
            "型別": p.get("type_label") or p.get("type", ""),
            "標題": (p.get("title") or "")[:90],
            "發表日": p.get("date") or "",
            "期刊／來源": (p.get("venue") or "")[:40],
            "引用": p.get("cites") or 0,
            "風險": p.get("risk", ""),
            "來源": ",".join(p.get("sources") or [p.get("source", "")]),
        })
    return rows


def collision_rows(result: PaperResult) -> list[dict]:
    """機構自我碰撞的列。"""
    rows = []
    for p in result.self_collision:
        rows.append({
            "機構": p.get("institution", ""),
            "標題": (p.get("title") or "")[:90],
            "發表日": p.get("date") or "",
            "期刊": (p.get("venue") or "")[:38],
            "DOI": p.get("doi") or "",
        })
    return rows
