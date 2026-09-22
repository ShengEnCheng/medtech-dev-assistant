"""M1 專利與 FTO 初篩。

對應 Biodesign 教科書 Stage 4.1 Intellectual Property Basics。

做什麼：
  1. 檢索詞擴充（同義詞、上位詞）
  2. 專利檢索（FreePatentsOnline 為主、Google Patents 為輔）
  3. 依標題重疊度標記高相關專利與風險等級

來源選擇的實測依據：
  Google Patents 的 xhr 端點是非官方 API，實測在連續請求後開始持續回 503，
  退避重試也救不回來，不適合當主要來源。
  FreePatentsOnline 是 HTML 站，檢索結果頁穩定可爬（需注意速率限制）。
  因此以 FPO 為主、Google Patents 為輔（只有它還拿得到 assignee 分群）。

重要限制：
  FPO 檢索結果頁不含 assignee 欄位；單篇專利頁會擋爬蟲。
  因此本模組只能給「高相關專利清單」，無法給完整的申請人佈局分析。
  申請人分群依賴 Google Patents（不穩定）或付費專利資料庫。

非正式 FTO 檢索，不取代專利師。
"""

from __future__ import annotations

import html as html_mod
import re
import time

from .http_client import get_json, get_with_retry
from .query_terms import expand_patent_terms
from .schema import AssigneeGroup, PatentHit, PatentResult, TODAY

FPO_SEARCH_URL = "https://www.freepatentsonline.com/result.html"
GP_XHR_URL = "https://patents.google.com/xhr/query"


def _freepatentsonline(query: str, pages: int = 2) -> dict:
    """FreePatentsOnline 檢索（HTML 爬取）。

    URL 格式注意（實測踩到的坑）：
    1. 必須用 query_txt=...&submit=&patents=on&p=N
       用 result.html?p=N 的簡寫會被擋。
    2. 站上的分頁連結格式是 p=N 在前、query_txt 在後
       （result.html?p=2&query_txt=...），與請求時的順序相反，
       所以抓頁數的正規表示式不能假設 query_txt 在前。
    3. 頁間需間隔 4 秒以上（該站有速率限制），並配合退避重試。
    4. 總命中數在 "Matches 1 - 50 out of 4059" 這種字串裡。
    """
    hits: list[PatentHit] = []
    max_page = 0
    total_matches: int | None = None
    q = query.replace(" ", "+")

    for page in range(1, pages + 1):
        url = f"{FPO_SEARCH_URL}?query_txt={q}&submit=&patents=on&p={page}"
        body = get_with_retry(url, retries=4, base_delay=4.0, min_bytes=5000)
        if not body:
            continue

        if total_matches is None:
            tm = re.search(r"Matches[\s\S]{0,40}?out of\s*([\d,]+)", body, flags=re.I)
            if tm:
                total_matches = int(tm.group(1).replace(",", ""))

        # 分頁連結：p=N 在前（站上格式與請求格式順序相反）
        for m in re.finditer(r"result\.html\?p=(\d+)&amp;query_txt=", body):
            max_page = max(max_page, int(m.group(1)))
        for m in re.finditer(r"result\.html\?query_txt=[^&]*&amp;patents=on&amp;p=(\d+)", body):
            max_page = max(max_page, int(m.group(1)))

        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.S):
            dm = re.search(r"<td[^>]*>\s*([0-9A-Z]{6,})\s*</td>", row)
            if not dm:
                continue
            doc = dm.group(1)
            tm = re.search(r'<a href="/([^"]+)\.html">(.*?)</a>', row, flags=re.S)
            title = ""
            if tm:
                title = html_mod.unescape(re.sub(r"<[^>]+>", "", tm.group(2))).strip()
            am = re.search(r"</a>\s*&nbsp;\s*<br/?>\s*(.*)", row, flags=re.S)
            abstract = ""
            if am:
                abstract = html_mod.unescape(re.sub(r"<[^>]+>", " ", am.group(1)))
                abstract = re.sub(r"\s+", " ", abstract).strip()[:220]
            hits.append(PatentHit(
                document=doc,
                title=title,
                abstract=abstract,
                url=f"https://www.freepatentsonline.com/{doc}.html",
            ))
        if page < pages:
            time.sleep(4.0)

    seen: set[str] = set()
    uniq: list[PatentHit] = []
    for h in hits:
        if h.document in seen:
            continue
        seen.add(h.document)
        uniq.append(h)
    return {"max_page": max_page, "hits": uniq, "total_matches": total_matches}


def _google_patents(query: str, limit: int = 25) -> dict:
    """Google Patents xhr（非官方，實測不穩定，僅取 assignee 分群）。"""
    inner = "q=" + query.replace(" ", "+")
    from urllib.parse import quote
    url = f"{GP_XHR_URL}?url={quote(inner, safe='')}"
    data = get_json(url, timeout=30.0)
    res = data.get("results", {})
    agg: dict[str, dict] = {}
    for cluster in res.get("cluster", []):
        for it in cluster.get("result", [])[:limit]:
            pt = it.get("patent", {})
            ap = (pt.get("assignee") or "（未載明）").strip() or "（未載明）"
            a = agg.setdefault(ap, {"assignee": ap, "count": 0, "latest": "", "samples": []})
            a["count"] += 1
            d = pt.get("grant_date") or pt.get("publication_date") or ""
            if d > a["latest"]:
                a["latest"] = d
            if len(a["samples"]) < 2:
                a["samples"].append({
                    "publication_number": pt.get("publication_number"),
                    "title": (pt.get("title") or "")[:80],
                })
    return {
        "total": res.get("total_num_results"),
        "assignees": [
            AssigneeGroup(**v) for v in sorted(agg.values(), key=lambda x: -x["count"])
        ][:12],
    }


def run(device_query: str, pages: int = 2, include_google: bool = True) -> PatentResult:
    out = PatentResult(query=device_query, query_date=TODAY)
    out.search_terms = expand_patent_terms(device_query)

    # 主來源：FreePatentsOnline
    fpo_pages_real = 0
    fpo_total = None
    try:
        fpo = _freepatentsonline(device_query, pages=pages)
        out.hits = fpo["hits"]
        out.max_page = fpo["max_page"]
        fpo_pages_real = fpo["max_page"]
        fpo_total = fpo.get("total_matches")
        if fpo["hits"]:
            out.sources_used.append("FreePatentsOnline")
        # 風險標記：標題與檢索詞的重疊度
        terms = [w.lower() for w in device_query.replace("-", " ").split() if len(w) > 3]
        for h in fpo["hits"]:
            title = (h.title or "").lower()
            overlap = sum(1 for w in terms if w in title)
            if overlap >= 1:
                h.overlap_terms = overlap
                h.risk = "高" if overlap >= 2 else "中"
                out.high_risk.append(h)
        out.high_risk.sort(key=lambda x: -x.overlap_terms)
    except Exception as exc:
        out.errors.append({"source": "FreePatentsOnline", "error": str(exc)})

    # 次要來源：Google Patents（assignee 分群）
    if include_google:
        try:
            gp = _google_patents(device_query)
            out.total_hits = gp["total"]
            out.assignees = gp["assignees"]
            if gp["assignees"]:
                out.sources_used.append("Google Patents")
        except Exception as exc:
            out.errors.append({
                "source": "Google Patents",
                "error": str(exc),
                "note": "非官方端點，實測常回 503；assignee 分群可改用人工查詢",
            })

    out.degraded = not (out.hits or out.assignees)
    out.fpo_total_matches = fpo_total
    if out.degraded:
        out.errors.append({
            "source": "M1 overall",
            "fallback": "兩個來源皆未取得；已產出檢索詞表，"
                        "請至 patents.google.com 或 freepatentsonline.com 人工檢索",
        })

    out.to_confirm = [
        "各高風險專利的獨立項（independent claim）實際保護範圍",
        "權利狀態（有效／到期／放棄）與剩餘保護期",
        "台灣對應案（TW 專利）是否存在 — 影響本地實施自由度",
        "是否有可迴避設計（design-around）空間",
    ]
    return out
