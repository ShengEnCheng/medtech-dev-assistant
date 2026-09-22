"""作者與機構層級的先前技術檢測（延伸 source_papers）。

R C 的需求：
  ① 輸入作者姓名 → 查他個人發表過的論文
     （團隊成員可能來自主其他學校或業界，不只在長庚）
  ② 其他學校的論文也要查（不只長庚）

為什麼需要「讓人挑選」而不是自動判定
------------------------------------
台灣姓名英譯重複率極高。實測 OpenAlex 搜尋 "Yung-Chun Chang"，
回傳的 5 筆中含 Yee-Chun Chen、C.M. Wang、Yenchun Jim Wu 等
非同一人。若工具自行挑一個，會產出錯誤的「己方先前技術」清單。

因此本模組的設計原則是：
  **列出候選人（附機構、著作數、ORCID）→ 由人挑選 → 再查其著作。**
寧可多一步人工確認，不要給出自信但錯誤的答案。

三種查法（互補，覆蓋不同情境）
----------------------------
  A. 依機構查：`papers_by_institution()` — 適合「查某校在主題上的成果」
  B. 依作者查：`find_authors()` + `author_works()` — 適合「查某人發表過什麼」
  C. 依 ORCID：最精確，但需使用者自行提供（OpenAlex 以 ORCID 聚合
     偶有錯誤合併，仍需檢視）

台灣機構清單
-----------
OpenAlex 可用 `filter=country_code:tw` 反查全部台灣機構（實測 520 個），
含 ROR 碼與著作數。快取到本地以加速下拉選單。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.parse as up
from pathlib import Path
from urllib.request import Request, urlopen

from .source_papers import _get_json, _norm_title, _TYPE_LABEL, CGU_INSTITUTIONS

# 台灣主要大學／醫學中心（ROR 已逐一查證，僅取台灣境內機構）
# 為什麼要寫死一份：OpenAlex 反查需網路且較慢，這份作為穩定的基礎清單，
# 動態清單再疊加其上。
TAIWAN_INSTITUTIONS = {
    # 綜合大學
    "國立臺灣大學": "https://ror.org/05bqach95",
    "國立陽明交通大學": "https://ror.org/00se2k293",
    "國立成功大學": "https://ror.org/01b8kcc49",
    "國立清華大學": "https://ror.org/00zdnkx70",
    "國立中央大學": "https://ror.org/00944ve71",
    "國立中興大學": "https://ror.org/05vn3ca78",
    "國立中山大學": "https://ror.org/00mjawt10",
    "國立臺灣科技大學": "https://ror.org/00q09pe49",
    "國立臺北科技大學": "https://ror.org/00cn92c09",
    "國立高雄科技大學": "https://ror.org/00hfj7g70",
    "國立虎尾科技大學": "https://ror.org/00q523p52",
    "國立高雄大學": "https://ror.org/013zjb662",
    "國立臺灣師範大學": "https://ror.org/05z1v2e72",
    "國立臺灣海洋大學": "https://ror.org/02d7r7e25",
    # 醫學校院
    "長庚大學": "https://ror.org/00d80zx46",
    "長庚科技大學": "https://ror.org/009knm296",
    "長庚紀念醫院": "https://ror.org/02verss31",
    "林口長庚": "https://ror.org/02dnn6q67",
    "基隆長庚": "https://ror.org/020dg9f27",
    "長庚兒童醫院": "https://ror.org/054e9ag92",
    "臺北醫學大學": "https://ror.org/05031qk94",
    "中國醫藥大學": "https://ror.org/00v408z34",
    "中國醫藥大學附設醫院": "https://ror.org/0368s4g32",
    "高雄醫學大學": "https://ror.org/03gk81f96",
    "國防醫學院": "https://ror.org/02bn97g32",
    "慈濟大學": "https://ror.org/04ss1bw11",
    "馬偕醫學院": "https://ror.org/00t89kj24",
    "國立臺灣大學醫學院附設醫院": "https://ror.org/03nteze27",
    "臺北榮民總醫院": "https://ror.org/03ymy8z76",
    "中央研究院": "https://ror.org/05bxb3784",
    # 其他
    "元智大學": "https://ror.org/01fv1ds98",
    "中原大學": "https://ror.org/02w8ws377",
    "輔仁大學": "https://ror.org/04je98850",
    "義守大學": "https://ror.org/04d7e4m76",
    "弘光科技大學": "https://ror.org/02f2vsx71",
    "中臺科技大學": "https://ror.org/03d4d3711",
    "南臺科技大學": "https://ror.org/0029n1t76",
    "大同大學": "https://ror.org/02djppw35",
}

# 快取檔（避免每次開介面都打 API）
_CACHE = Path(__file__).resolve().parent.parent / "data" / "tw_institutions.json"


def _institution_cache_path() -> Path:
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    return _CACHE


def taiwan_institutions(dynamic: bool = False,
                       min_works: int = 3000) -> dict[str, str]:
    """台灣機構清單（ROR 碼）。

    參數
    ----
    dynamic : True 時向 OpenAlex 反查全部台灣機構（520 個，實測），
              並快取到 data/。False 只用內建清單（快、穩定）。
    min_works : 動態查詢時的著作數門檻，過濾小型機構。

    回傳 {顯示名稱: ROR}
    """
    out = dict(TAIWAN_INSTITUTIONS)

    if not dynamic:
        return out

    cache = _institution_cache_path()
    data = None
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            data = None

    if not data:
        d = _get_json("https://api.openalex.org/institutions?" + up.urlencode({
            "filter": "country_code:tw",
            "per-page": 200,
            "sort": "works_count:desc",
            "select": "id,display_name,works_count,ror",
        }))
        if not d:
            return out
        data = []
        for it in (d.get("results") or []):
            if (it.get("works_count") or 0) < min_works:
                continue
            ror = it.get("ror")
            if ror:
                data.append({"name": it.get("display_name"),
                             "ror": ror,
                             "works": it.get("works_count")})
        try:
            cache.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        except Exception:
            pass

    for it in (data or []):
        nm = (it.get("name") or "").strip()
        ror = it.get("ror") or ""
        if nm and ror and nm not in out:
            out[nm] = ror
    return out


def papers_by_institution(query: str, ror: str, limit: int = 15) -> list[dict]:
    """查某機構在特定主題上的論文（支援任何學校，不只長庚）。"""
    d = _get_json("https://api.openalex.org/works?" + up.urlencode({
        "search": query,
        "filter": f"authorships.institutions.ror:{ror}",
        "per-page": limit,
        "select": ("id,title,publication_date,publication_year,doi,type,"
                   "cited_by_count,authorships,primary_location"),
    }))
    if not d:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for w in (d.get("results") or []):
        _t = (w.get("title") or "").strip()
        _k = ((w.get("doi") or "").replace("https://doi.org/", "")
              or _norm_title(_t))
        if not _k or _k in seen:
            continue
        seen.add(_k)
        auths = []
        for a in (w.get("authorships") or [])[:4]:
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
            "cites": w.get("cited_by_count") or 0,
            "type": w.get("type") or "article",
            "type_label": _TYPE_LABEL.get(w.get("type", ""), w.get("type", "")),
            "source": "OpenAlex",
        })
    return out


def find_authors(name: str, limit: int = 10,
                 institution_ror: str | None = None) -> list[dict]:
    """依姓名找作者候選人（**供人工挑選，不自動認定同一人**）。

    為什麼要回傳候選清單而非單一答案：
    台灣姓名英譯重複率極高，實測搜尋 "Yung-Chun Chang" 會混入
    其他作者。自動挑一個會產出錯誤結果，故一律列出候選並附
    機構與著作數，由使用者判斷。

    institution_ror 可縮小範圍（只知道所屬學校時強烈建議填）。
    """
    params = {
        "search": name,
        "per-page": limit,
        "select": ("id,display_name,works_count,cited_by_count,"
                   "last_known_institutions,orcid,summary_stats"),
    }
    if institution_ror:
        params["filter"] = f"last_known_institutions.ror:{institution_ror}"

    d = _get_json("https://api.openalex.org/authors?" + up.urlencode(params))
    if not d:
        return []

    out = []
    for a in (d.get("results") or []):
        insts = [(i.get("display_name") or "")
                 for i in (a.get("last_known_institutions") or [])]
        stats = a.get("summary_stats") or {}
        out.append({
            "id": (a.get("id") or "").split("/")[-1],
            "name": a.get("display_name") or "",
            "works": a.get("works_count") or 0,
            "cites": a.get("cited_by_count") or 0,
            "h_index": stats.get("h_index") or 0,
            "institutions": insts[:3],
            "orcid": (a.get("orcid") or "").replace(
                "https://orcid.org/", ""),
            "openalex_url": a.get("id") or "",
        })
    return out


def author_works(author_id: str, topic: str | None = None,
                 limit: int = 20) -> list[dict]:
    """查某作者的著作。

    topic 有值時同時以主題限縮 —— 這是實務上最有用的一步：
    先確認「這個人是誰」，再看「他在這個主題上發表過什麼」。
    """
    # 主題條件放進 filter（用 title_and_abstract.search），
    # 比外層 search 參數精確 —— 作者+主題的查詢要的是精準度。
    filt = f"authorships.author.id:{author_id}"
    if topic:
        filt += f",title_and_abstract.search:{topic}"

    params = {
        "filter": filt,
        "per-page": limit,
        "sort": "publication_date:desc",
        "select": ("id,title,publication_date,publication_year,doi,type,"
                   "cited_by_count,authorships,primary_location"),
    }

    d = _get_json("https://api.openalex.org/works?" + up.urlencode(params))
    if not d:
        return []

    # OpenAlex 會把同一篇論文的不同版本（arXiv 預印本、Zenodo、
    # 期刊正式版）各自算一筆，DOI 也不同。實測一位作者的近作中，
    # 同一篇「Mind the Gap」出現 3 次。
    #
    # 去重主鍵必須用**標題**而非 DOI —— 版本間 DOI 不同。
    # 合併時保留**最早的公開日期**：對專利先前技術而言，
    # 最早的公開日才是關鍵（優惠期從該日起算）。
    merged: dict[str, dict] = {}
    for w in (d.get("results") or []):
        title = (w.get("title") or "").strip()
        key = _norm_title(title)
        if not key:
            continue
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        date = w.get("publication_date") or ""
        cur = merged.get(key)
        if cur is None:
            venue = ((w.get("primary_location") or {}).get("source")
                     or {}).get("display_name") or ""
            merged[key] = {
                "title": title,
                "venue": venue,
                "year": w.get("publication_year") or "",
                "date": date,
                "doi": doi,
                "all_dois": [doi] if doi else [],
                "versions": 1,
                "cites": w.get("cited_by_count") or 0,
                "type": w.get("type") or "article",
                "type_label": _TYPE_LABEL.get(w.get("type", ""),
                                              w.get("type", "")),
                "source": "OpenAlex",
            }
            continue
        cur["versions"] += 1
        if doi and doi not in cur["all_dois"]:
            cur["all_dois"].append(doi)
        # 取最早日期（優惠期從最早公開日起算）
        if date and (not cur["date"] or date < cur["date"]):
            cur["date"] = date
            cur["year"] = w.get("publication_year") or cur["year"]
        if (w.get("cited_by_count") or 0) > (cur["cites"] or 0):
            cur["cites"] = w["cited_by_count"]

    out = list(merged.values())
    out.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    return out


def multi_institution_check(query: str, rors: list[str],
                            per_inst: int = 8) -> list[dict]:
    """一次查多個機構在主題上的論文（供「其他學校也要查」使用）。

    rors 為 ROR 碼清單；每筆結果會帶上機構名稱以便分組顯示。
    """
    name_of = {v: k for k, v in taiwan_institutions().items()}
    found: list[dict] = []
    seen: set[str] = set()
    for ror in rors:
        try:
            res = papers_by_institution(query, ror, limit=per_inst)
        except Exception:
            continue
        label = name_of.get(ror, ror)
        for p in res:
            key = p.get("doi") or _norm_title(p.get("title", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            p = dict(p)
            p["institution"] = label
            found.append(p)
        time.sleep(0.25)
    found.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    return found
