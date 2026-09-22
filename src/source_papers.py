"""論文先前技術（non-patent literature, NPL）檢索。

為什麼專利模組需要論文檢索
--------------------------
專利審查的「先前技術」**不只有專利**。學術論文、會議論文、預印本、
甚至學位論文，都會破壞新穎性。對校園育成團隊尤其關鍵，因為最常見的
致命情況不是「別人先申請」，而是：

    老師（或團隊成員）自己已經發表過相關論文。

這會造成兩種後果：
  1. 若超過優惠期 → 自己的論文成為自己的專利障礙
  2. 預印本（preprint）的公開日通常比正式發表早 6-12 個月
     → 用正式發表日算優惠期會算錯

各國優惠期差異極大，且**是否涵蓋學術發表完全不同**（詳見
grace_period_matrix()）。實務上最危險的是 EPO：歐洲專利公約
Art. 55 只有 6 個月，且僅限「明顯濫用」與「官方國際展覽」，
**學術發表不在適用範圍**。論文一旦公開，歐洲即無救。

本模組的定位
-----------
只攤證據，不做法律判斷：
  - 列出可能構成先前技術的論文，附發表日期與型別
  - 標示預印本（日期更早，風險更高）
  - 標出「機構自我碰撞」（長庚自己的論文）
  - 提供各國優惠期對照表供團隊與專利師判斷

**不做的事**：不判斷某篇論文是否真的破壞新穎性（需要比對
申請專利範圍與論文的技術揭露內容，超出本工具能力）。

資料來源（全部免 API key，2026-09 實測）
----------------------------------------
  Europe PMC  — 生醫核心，含預印本，有引用端點
  OpenAlex    — 跨領域，可依機構篩選（自我碰撞檢測的關鍵）
  Crossref    — DOI 後設資料最完整，含學位論文
  arXiv       — 工程與預印本
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse as up
from urllib.request import Request, urlopen

UA = "MedtechDevAssistant/1.0 (university incubation; research)"

# 長庚體系機構（用於「老師是否自己發表過」的自我碰撞檢測）
# ROR 為 OpenAlex 機構篩選的可靠識別碼
CGU_INSTITUTIONS = {
    "長庚大學": "https://ror.org/00d80zx46",
    "長庚紀念醫院": "https://ror.org/02verss31",
    "林口長庚": "https://ror.org/02dnn6q67",
    "基隆長庚": "https://ror.org/020dg9f27",
    "長庚兒童醫院": "https://ror.org/054e9ag92",
}


def _get_json(url: str, timeout: int = 25, retries: int = 3):
    """帶重試的 JSON 取用。

    為什麼需要重試：Europe PMC 偶爾回只含 {"version": "6.9"} 的空回應
    （實測：連 6 次中 1 次，間隔 2 秒，屬伺服器端限流而非請求錯誤）。
    若不重試會**靜默漏掉整個來源** —— 比報錯更危險。
    """
    ctx = ssl.create_default_context()
    for attempt in range(retries):
        req = Request(url, headers={"User-Agent": UA,
                                    "Accept": "application/json"})
        try:
            with urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read().decode("utf-8", "ignore")
            d = json.loads(raw)
            # 空回應偵測：Europe PMC 限流時只回 version 欄位
            if isinstance(d, dict) and set(d.keys()) <= {"version"}:
                time.sleep(1.5 * (attempt + 1))
                continue
            return d
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(1.0 * (attempt + 1))
    return None


# ------------------------------------------------------------- 各來源查詢

def _europepmc(query: str, limit: int = 15) -> list[dict]:
    """Europe PMC — 生醫核心，含預印本標記。"""
    # 不指定 sort → 使用相關度排序。
    # 為什麼不用 "CITED desc"：先前技術要的是「最接近的技術揭露」，
    # 不是「最有名的論文」。實測同一查詢，依引用數排序會撈到
    # 泛論型高引用回顧（Acinetobacter、AmpC beta-lactamases），
    # 依相關度排序才會撈到感測器與偵測技術的文獻 —— 後者才可能
    # 真正構成新穎性障礙。
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
           + up.urlencode({"query": query, "format": "json",
                           "pageSize": limit, "resultType": "core"}))
    d = _get_json(url)
    if not d:
        return []
    out = []
    for r in (d.get("resultList", {}).get("result") or []):
        src = str(r.get("source") or "")
        is_ppr = src == "PPR" or "preprint" in str(
            r.get("pubTypeList", {}).get("pubType") or "").lower()
        out.append({
            "title": (r.get("title") or "").strip().rstrip("."),
            "authors": r.get("authorString") or "",
            "venue": (r.get("journalInfo", {}).get("journal", {})
                      or {}).get("title") or "",
            "year": r.get("pubYear") or "",
            "date": r.get("firstPublicationDate") or "",
            "doi": r.get("doi") or "",
            "pmid": r.get("pmid") or "",
            "cites": r.get("citedByCount") or 0,
            "type": "preprint" if is_ppr else "article",
            "source": "Europe PMC",
            "oa": r.get("isOpenAccess") == "Y",
        })
    return out


def _openalex(query: str, limit: int = 15,
              institution_ror: str | None = None) -> list[dict]:
    """OpenAlex — 跨領域；institution_ror 供自我碰撞檢測。

    注意：OpenAlex 的 select 參數可減少回傳量，加快速度。
    """
    params = {
        "search": query,
        "per-page": limit,
        "select": ("id,title,publication_date,publication_year,doi,type,"
                   "cited_by_count,is_retracted,authorships,primary_location"),
    }
    if institution_ror:
        params["filter"] = f"authorships.institutions.ror:{institution_ror}"
    d = _get_json("https://api.openalex.org/works?" + up.urlencode(params))
    if not d:
        return []
    out = []
    for w in (d.get("results") or []):
        auths = []
        for a in (w.get("authorships") or [])[:3]:
            nm = (a.get("author") or {}).get("display_name")
            if nm:
                auths.append(nm)
        venue = ((w.get("primary_location") or {}).get("source") or {}).get(
            "display_name") or ""
        out.append({
            "title": (w.get("title") or "").strip(),
            "authors": ", ".join(auths),
            "venue": venue,
            "year": w.get("publication_year") or "",
            "date": w.get("publication_date") or "",
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "pmid": "",
            "cites": w.get("cited_by_count") or 0,
            "type": w.get("type") or "article",
            "source": "OpenAlex",
            "oa": bool((w.get("primary_location") or {}).get("is_oa")),
            "retracted": w.get("is_retracted") or False,
        })
    return out


def _crossref(query: str, limit: int = 15) -> list[dict]:
    """Crossref — DOI 後設資料最完整，含學位論文與會議論文。"""
    d = _get_json("https://api.crossref.org/works?" + up.urlencode({
        "query.bibliographic": query, "rows": limit,
        "select": "DOI,title,author,issued,container-title,type,is-referenced-by-count",
    }))
    if not d:
        return []
    out = []
    for it in (d.get("message", {}).get("items") or []):
        auths = []
        for a in (it.get("author") or [])[:3]:
            nm = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if nm:
                auths.append(nm)
        dp = (it.get("issued", {}).get("date-parts") or [[None]])[0]
        # 排除 component（圖表附件）與 reference-entry（百科條目）
        if it.get("type") in ("component", "reference-entry", "peer-review"):
            continue
        out.append({
            "title": ((it.get("title") or [""])[0] or "").strip(),
            "authors": ", ".join(auths),
            "venue": ((it.get("container-title") or [""])[0] or ""),
            "year": dp[0] or "",
            "date": "-".join(str(x) for x in dp if x),
            "doi": it.get("DOI") or "",
            "pmid": "",
            "cites": it.get("is-referenced-by-count") or 0,
            "type": it.get("type") or "",
            "source": "Crossref",
            "oa": False,
        })
    return out


def _arxiv(query: str, limit: int = 8) -> list[dict]:
    """arXiv — 工程與預印本。預印本日期即公開日，是優惠期計算的關鍵。"""
    url = ("http://export.arxiv.org/api/query?"
           + up.urlencode({"search_query": f'all:"{query}"',
                           "max_results": limit}))
    ctx = ssl.create_default_context()
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=25, context=ctx) as r:
            raw = r.read().decode("utf-8", "ignore")
    except Exception:
        return []

    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", raw, re.S):
        def grab(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.S)
            return " ".join(m.group(1).split()) if m else ""
        title = grab("title")
        if not title:
            continue
        authors = ", ".join(
            " ".join(m.split()) for m in
            re.findall(r"<name>(.*?)</name>", entry, re.S)[:3])
        out.append({
            "title": title,
            "authors": authors,
            "venue": "arXiv (preprint)",
            "year": grab("published")[:4],
            "date": grab("published")[:10],
            "doi": grab("doi"),
            "pmid": "",
            "cites": 0,
            "type": "preprint",
            "source": "arXiv",
            "oa": True,
        })
    return out


# ------------------------------------------------------------- 合併與去重

def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())[:70]


_TYPE_LABEL = {
    "article": "期刊論文",
    "review": "回顧論文",
    "review-article": "回顧論文",
    "preprint": "**預印本**",
    "proceedings-article": "會議論文",
    "proceedings": "會議論文",
    "dissertation": "學位論文",
    "posted-content": "預印本",
    "book-chapter": "專書章節",
    "book": "專書",
    "report": "技術報告",
    "dataset": "資料集",
}


def merge_papers(groups: list[list[dict]]) -> list[dict]:
    """多來源合併去重。

    優先去重鍵：DOI（正規化）→ 標題正規化。
    保留資訊較完整者（有引用數、有 DOI 者優先）。
    """
    by_key: dict[str, dict] = {}
    for g in groups:
        for p in g:
            doi = (p.get("doi") or "").lower().strip()
            key = doi if doi else "t:" + _norm_title(p.get("title", ""))
            if not key.strip("t:"):
                continue
            cur = by_key.get(key)
            if cur is None:
                by_key[key] = p
                continue
            # 合併：型別以「預印本」為優先（風險更高，寧可誤報）
            if p.get("type") in ("preprint", "posted-content"):
                cur["type"] = "preprint"
            if not cur.get("doi") and p.get("doi"):
                cur["doi"] = p["doi"]
            if not cur.get("date") and p.get("date"):
                cur["date"] = p["date"]
            if (p.get("cites") or 0) > (cur.get("cites") or 0):
                cur["cites"] = p["cites"]
            # 記錄多來源命中
            srcs = cur.setdefault("_sources", [cur.get("source", "")])
            if p.get("source") not in srcs:
                srcs.append(p.get("source", ""))
    out = list(by_key.values())
    for p in out:
        p.setdefault("_sources", [p.get("source", "")])
        p["sources"] = [s for s in p["_sources"] if s]
        p.pop("_sources", None)
        p["type_label"] = _TYPE_LABEL.get(p.get("type", ""), p.get("type", ""))
    # 排序：預印本優先（公開日更早、風險更高），其餘維持來源回傳的
    # 相關度順序。不依引用數排序 —— 先前技術看的是技術相關性，
    # 高引用數往往代表泛論型回顧，反而不是最接近的先前技術。
    out.sort(key=lambda x: (x.get("type") not in ("preprint",
                                                  "posted-content"),))
    return out


def search_papers(query: str, limit: int = 15,
                  include_arxiv: bool = True) -> dict:
    """跨來源論文檢索。

    參數
    ----
    query : 英文檢索詞（與專利檢索共用 `derive_device_query`）。
            建議 3-5 個詞的具體片語，不要用泛用詞。
    limit : 每個來源的回傳上限
    include_arxiv : 工程類團隊建議開啟（預印本風險高）

    回傳 {"query":..., "papers":[...], "counts":{...}, "errors":[...]}
    """
    groups, counts, errors = [], {}, []

    for label, fn in (("Europe PMC", _europepmc),
                      ("OpenAlex", _openalex),
                      ("Crossref", _crossref)):
        try:
            res = fn(query, limit)
            groups.append(res)
            counts[label] = len(res)
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}")
        time.sleep(0.3)

    if include_arxiv:
        try:
            res = _arxiv(query, max(4, limit // 3))
            groups.append(res)
            counts["arXiv"] = len(res)
        except Exception as exc:
            errors.append(f"arXiv: {type(exc).__name__}")

    papers = merge_papers(groups)
    return {"query": query, "papers": papers, "counts": counts,
            "errors": errors}


def search_papers_all(query: str, limit: int = 12) -> dict:
    """論文檢索 + 機構自我碰撞，一次到位（供模組呼叫）。

    回傳 {"search": {...}, "self_collision": [...]}
    """
    res = search_papers(query, limit=limit)
    try:
        res["self_collision"] = self_collision_check(query, limit=6)
    except Exception:
        res["self_collision"] = []
    return res


def self_collision_check(query: str, limit: int = 8) -> list[dict]:
    """機構自我碰撞檢測 —— 查長庚體系是否已發表同主題論文。

    這是校園育成團隊最常見的專利風險：老師自己的論文。
    用 OpenAlex 的 ROR 機構篩選，比關鍵字搜尋可靠得多。
    """
    found: list[dict] = []
    seen: set[str] = set()
    for name, ror in CGU_INSTITUTIONS.items():
        try:
            res = _openalex(query, limit=limit, institution_ror=ror)
        except Exception:
            continue
        for p in res:
            key = (p.get("doi") or _norm_title(p.get("title", "")))
            if key and key not in seen:
                seen.add(key)
                p = dict(p)
                p["institution"] = name
                p["type_label"] = _TYPE_LABEL.get(
                    p.get("type", ""), p.get("type", ""))
                found.append(p)
        time.sleep(0.3)
    found.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    return found


# ------------------------------------------------------------- 優惠期矩陣

def grace_period_matrix() -> list[dict]:
    """各國「優惠期是否涵蓋學術發表」對照表。

    這張表是本模組最有價值的產出之一：各國差異極大，
    且**學術發表是否適用**是完全不同的問題。

    法源已逐一查證（2026-09）：
      台灣 — 專利法第 22 條第 3 項（2022 年修正，6 個月放寬為 12 個月）
      美國 — 35 U.S.C. 102(b)（AIA）
      中國 — 專利法第 24 條
      日本 — 特許法第 30 條
      韓國 — 特許法第 30 條
      歐洲 — EPC Art. 55
    """
    return [
        {
            "region": "台灣",
            "months": 12,
            "covers_academic": True,
            "note": "專利法第 22 條第 3 項：出於申請人本意或非本意之公開，"
                    "12 個月內申請者不喪失新穎性。**2022 年修法由 6 個月"
                    "放寬為 12 個月，且不限公開態樣**，學術發表適用。",
            "risk": "低",
        },
        {
            "region": "美國",
            "months": 12,
            "covers_academic": True,
            "note": "35 U.S.C. 102(b)：發明人自己於申請前 12 個月內的"
                    "公開不構成先前技術。學術發表適用。",
            "risk": "低",
        },
        {
            "region": "日本",
            "months": 12,
            "covers_academic": True,
            "note": "特許法第 30 條。適用學術發表，但**須在申請時提出"
                    "程序聲明**（30 日內補提證明），程序漏做即失效。",
            "risk": "中",
        },
        {
            "region": "韓國",
            "months": 12,
            "covers_academic": True,
            "note": "特許法第 30 條。適用學術發表，同樣有程序要求。",
            "risk": "中",
        },
        {
            "region": "中國",
            "months": 6,
            "covers_academic": "部分",
            "note": "專利法第 24 條：**僅限「規定的學術會議或技術會議」"
                    "首次發表**、政府承認之國際展覽、緊急狀態公益公開。"
                    "**一般期刊論文發表不在適用範圍**，會直接破壞新穎性。",
            "risk": "高",
        },
        {
            "region": "歐洲 (EPO)",
            "months": 6,
            "covers_academic": False,
            "note": "EPC Art. 55：僅限「明顯濫用（evident abuse）」與"
                    "「官方或官方承認之國際展覽」。"
                    "**學術發表完全不在適用範圍**。"
                    "論文一旦公開，歐洲新穎性即喪失且無補救。",
            "risk": "極高",
        },
    ]


def submission_order_note() -> str:
    """論文發表與專利申請的時序建議（不做個案判斷）。"""
    return (
        "**論文發表與專利申請的時序原則**\n\n"
        "1. **申請在先、發表在後**是唯一在各國都安全的順序。\n"
        "2. 台灣與美國有 12 個月緩衝，但仍應盡早申請"
        "（他人獨立研發或搶先申請的風險不因優惠期而消失）。\n"
        "3. **歐洲是最大風險區**：一旦論文公開，EPO 即無優惠期可主張。"
        "若歐洲是目標市場，論文必須等專利申請日之後才投出。\n"
        "4. 中國一般期刊論文**不算**優惠期適用情形"
        "（僅限指定學術會議）。\n"
        "5. **預印本（arXiv、bioRxiv、SSRN）的公開日即為公開日**，"
        "不是正式刊登日。優惠期要從預印本上線日起算。\n"
        "6. 若已發生發表，時序比對與優惠期主張屬專業判斷，"
        "請洽專利師。本工具只提供日期與法源，不代為決定。"
    )
