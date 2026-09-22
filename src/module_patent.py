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

# FreePatentsOnline 支援的專利局選項（實測確認，2026-09）
# 這是本模組最重要的發現：FPO 不只美國專利，改參數就能涵蓋全球。
# 實測「urinary catheter」各局命中數：
#   US 27,057 ／ EP 27,129 ／ WO 64,470 ／ JP 24,233 ／ DE 2,645
#   全部合併 81,064
FPO_OFFICES: dict[str, str] = {
    "US": "uspat=on",      # 美國核准專利
    "USAPP": "usapp=on",   # 美國公開申請案
    "EP": "eupat=on",      # 歐洲專利局
    "WO": "wopat=on",      # PCT 國際專利
    "JP": "jp=on",         # 日本
    "DE": "depat=on",      # 德國
}

# 專利號格式 → 所屬專利局（用於解析結果）
_OFFICE_PATTERNS: list[tuple[str, str]] = [
    ("EP", r"\bEP\d{7}[AB]\d?\b"),
    ("WO", r"\bWO\d{4}[/\s]?\d{6}[A-Z]?\d?\b"),
    ("JP", r"\bJP\d{7,12}[A-Z]?\b"),
    ("DE", r"\bDE\d{7,12}[A-Z]\d?\b"),
    ("GB", r"\bGB\d{7,12}[A-Z]?\b"),
    ("FR", r"\bFR\d{7,12}[A-Z]?\b"),
    ("CN", r"\bCN\d{6,12}[A-Z]?\b"),
    ("KR", r"\bKR\d{7,12}[A-Z]?\b"),
    ("US", r"\bUS\d{7,11}(?:[A-Z]\d?)?\b"),
]


def _fpo_office_string(offices: list[str] | None) -> str:
    """組合 FPO 的專利局參數。預設全部（全球）。"""
    if not offices:
        offices = list(FPO_OFFICES)
    parts = [FPO_OFFICES[o] for o in offices if o in FPO_OFFICES]
    if not parts:
        parts = [FPO_OFFICES["US"]]
    return "&".join(parts)


def _detect_offices(html: str) -> dict[str, list[str]]:
    """由結果頁統計各專利局的號碼（判斷涵蓋範圍）。"""
    found: dict[str, list[str]] = {}
    for label, pat in _OFFICE_PATTERNS:
        hits = list(dict.fromkeys(re.findall(pat, html)))
        if hits:
            found[label] = hits
    return found


def _freepatentsonline(query: str, pages: int = 2,
                       offices: list[str] | None = None) -> dict:
    """FreePatentsOnline 檢索（HTML 爬取，支援全球六大專利局）。

    URL 格式注意（實測踩到的坑）：
    1. 必須用 query_txt=...&submit=&<專利局參數>&p=N。
    2. 站上分頁連結格式是 p=N 在前、query_txt 在後，
       與請求時的順序相反，抓頁數的正規表示式不能假設 query_txt 在前。
    3. 頁間與請求間需間隔，該站有嚴格的速率限制：
       實測連續請求會 Connection reset by peer，需 10-12 秒退避。
    4. 總命中數在 "Matches 1 - 50 out of 4059" 這種字串裡。
    5. 專利局參數預設全部（全球）；只給 US 會漏掉 EP/WO/JP/DE。

    實測「urinary catheter」涵蓋度：US 27,057 ／ EP 27,129 ／
    WO 64,470 ／ JP 24,233 ／ DE 2,645，合併 81,064。
    """
    hits: list[PatentHit] = []
    max_page = 0
    total_matches: int | None = None
    office_counts: dict[str, int] = {}
    q = query.replace(" ", "+")
    ofc = _fpo_office_string(offices)

    for page in range(1, pages + 1):
        url = f"{FPO_SEARCH_URL}?query_txt={q}&submit=&{ofc}&p={page}"
        body = get_with_retry(url, retries=4, base_delay=10.0, min_bytes=5000)
        if not body:
            continue

        if total_matches is None:
            tm = re.search(r"Matches[\s\S]{0,40}?out of\s*([\d,]+)", body, flags=re.I)
            if tm:
                total_matches = int(tm.group(1).replace(",", ""))

        # 統計本次結果涵蓋的專利局（供報告說明全球覆蓋範圍）
        for label, docs in _detect_offices(body).items():
            office_counts[label] = max(office_counts.get(label, 0), len(docs))

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
            time.sleep(10.0)

    seen: set[str] = set()
    uniq: list[PatentHit] = []
    for h in hits:
        if h.document in seen:
            continue
        seen.add(h.document)
        uniq.append(h)
    return {"max_page": max_page, "hits": uniq, "total_matches": total_matches,
            "office_counts": office_counts, "offices": offices or list(FPO_OFFICES)}


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


def run(device_query: str, pages: int = 2, include_google: bool = False,
        offices: list[str] | None = None) -> PatentResult:
    """執行專利檢索。

    include_google 預設 False：Google Patents xhr 實測持續 503
    （連 HTML 首頁都掛，確認非 IP 問題），已不可用。
    保留參數是為了將來若恢復時可快速啟用。

    offices 預設 None → 全部專利局（US/EP/WO/JP/DE），
    涵蓋全球；只給 ["US"] 會退回單一美國市場。
    """
    out = PatentResult(query=device_query, query_date=TODAY)
    out.search_terms = expand_patent_terms(device_query)

    # 主來源：FreePatentsOnline（支援全球六大專利局）
    fpo_total = None
    try:
        fpo = _freepatentsonline(device_query, pages=pages, offices=offices)
        out.hits = fpo["hits"]
        out.max_page = fpo["max_page"]
        fpo_total = fpo.get("total_matches")
        out.offices_searched = fpo.get("offices") or []
        out.office_counts = fpo.get("office_counts") or {}
        if fpo["hits"]:
            out.sources_used.append("FreePatentsOnline（全球多局）")
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

    # Google Patents 已於 2026-09 實測確認全面失效（含 HTML 首頁皆 503）
    # 預設不呼叫；需要 assignee 分群時改用付費資料庫或人工查詢
    if include_google:
        try:
            gp = _google_patents(device_query)
            out.total_hits = gp["total"]
            out.assignees = gp["assignees"]
            if gp["assignees"]:
                out.sources_used.append("Google Patents")
        except Exception as exc:
            out.errors.append({
                "source": "Google Patents（已停用）",
                "error": str(exc)[:100],
                "note": "2026-09 實測確認失效：xhr 與 HTML 頁面皆回 503。"
                        "申請人分群請改用付費專利資料庫或人工查詢。",
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
