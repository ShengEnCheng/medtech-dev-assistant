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
        limit: int = 12, include_arxiv: bool = True) -> PaperResult:
    """執行論文先前技術檢索。

    參數
    ----
    product : 產品說明原文（記錄用）
    query   : 英文檢索詞。未提供時由呼叫端用 derive_device_query 推導。
              建議 3-5 個具體詞的片語，避免泛用詞。
    """
    res = PaperResult(product=product, query=query or "")

    if not query:
        res.degraded = True
        res.note = "未提供英文檢索詞，無法查詢論文先前技術"
        return res

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
