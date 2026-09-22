"""疾病負擔與市場推估（Biodesign Stage 2.1 / 2.2 / 2.4 的資料苦工）。

為什麼需要這個模組：
原本的三模組（法規／專利／競品）只回答「市面上有什麼」，
完全沒有回答 Biodesign 需求篩選最核心的問題：

  1. 這個疾病有多嚴重？（疾病負擔：發生率、盛行率、死亡率）
  2. 現在的照護方式是什麼？哪裡不夠好？（現行標準治療與其侷限）
  3. 有多少人需要？（目標人口 = market sizing 的分母）
  4. 現在花多少錢？（由下而上市場規模）

Biodesign 官方教材（Top-Down and Bottom-Up Market Sizing）要求雙向計算：
  - 由上而下：目標人口 × 每人每年花費
  - 由下而上：各現有解法 × 使用人數 × 單價
兩個數字通常差很多，那個落差本身就是重要資訊。

實測確認的來源（2026-09-22）：

| 來源 | 端點 | 台灣資料 | 用途 |
|------|------|---------|------|
| PubMed E-utilities | eutils.ncbi.nlm.nih.gov | ✅ | 盛行率／發生率／成本文獻 |
| 衛福部統計處 | dep.mohw.gov.tw | ✅ | 死因、長照、醫療統計 |
| 國健署 | hpa.gov.tw | ✅ | 國民健康訪問調查 |
| 健保署 | www.nhi.gov.tw | ✅（需 jina 代理） | 特材給付 |
| WHO GHO | ghoapi.azureedge.net | ❌ 非會員國 | 全球負擔比較 |
| World Bank | api.worldbank.org | ❌ 非會員國 | 各國人口／支出 |

重要限制（必須在報告中揭露）：
- WHO 與 World Bank **沒有台灣資料**（台灣非會員國）。台灣數字要走
  衛福部統計處與國健署。
- 健保署全站對程式化請求回 403，需經 r.jina.ai 代理。此為繞道，
  非官方 API，穩定性較差，失敗時優雅降級。
- PubMed 給的是**文獻摘要**，不是結構化數字。本模組會擷取摘要中
  帶數字的句子，但**仍需人工判讀**，不可直接當結論。
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .http_client import get_json

UA = {"User-Agent": "MedtechDevAssistant/1.0 (university incubation; research)",
      "Accept": "application/json"}
CTX = ssl.create_default_context()

PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
WHO_GHO = "https://ghoapi.azureedge.net/api"
WB = "https://api.worldbank.org/v2"
JINA = "https://r.jina.ai/"

# E-utilities 禮貌性延遲（NCBI 要求未帶 key 者每秒最多 3 次）
_LAST_CALL = {"t": 0.0}


def _throttle(min_gap: float = 0.4) -> None:
    gap = time.time() - _LAST_CALL["t"]
    if gap < min_gap:
        time.sleep(min_gap - gap)
    _LAST_CALL["t"] = time.time()


def _get(url: str, accept: str = "application/json", timeout: int = 35,
         retries: int = 2):
    """通用抓取（含退避重試）。"""
    for attempt in range(retries + 1):
        h = dict(UA)
        h["Accept"] = accept
        try:
            with urlopen(Request(url, headers=h), timeout=timeout,
                         context=CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries:
                raise RuntimeError(f"HTTP {e.code}") from e
        except Exception as exc:
            if attempt == retries:
                raise RuntimeError(str(exc)[:120]) from exc
        time.sleep(1.5 * (attempt + 1))
    return None


# ---------------------------------------------------------------- PubMed

# 疾病負擔相關的檢索後綴（Biodesign 要的四類數字）
BURDEN_SUFFIXES = {
    "burden": "prevalence OR incidence OR epidemiology",
    "cost": "cost OR economic burden OR healthcare expenditure",
    "treatment": "treatment OR standard of care OR management",
    "limitation": "limitations OR unmet need OR failure rate",
}


def pubmed_search(term: str, limit: int = 5) -> list[dict]:
    """檢索 PubMed，回傳 PMID 與標題。

    重要：query 用自然語言（不要加引號或 AND）。
    實測加引號會抑制 PubMed 的 automatic term mapping，反而變不準。
    """
    _throttle()
    q = urllib.parse.quote(term)
    url = (f"{PUBMED}/esearch.fcgi?db=pubmed&retmode=json"
           f"&retmax={limit}&sort=relevance&term={q}")
    raw = _get(url)
    if not raw:
        return []
    try:
        d = json.loads(raw)
    except Exception:
        return []
    ids = d.get("esearchresult", {}).get("idlist", [])
    total = d.get("esearchresult", {}).get("count")
    if not ids:
        return []
    _throttle()
    url2 = (f"{PUBMED}/esummary.fcgi?db=pubmed&retmode=json"
            f"&id={','.join(ids)}")
    raw2 = _get(url2)
    out: list[dict] = []
    try:
        d2 = json.loads(raw2 or "{}").get("result", {})
        for i in ids:
            rec = d2.get(i, {})
            out.append({
                "pmid": i,
                "title": rec.get("title", "")[:180],
                "journal": (rec.get("fulljournalname")
                            or rec.get("source") or "")[:60],
                "date": rec.get("pubdate", ""),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{i}/",
            })
    except Exception:
        pass
    if out and total:
        out[0]["_total_hits"] = total
    return out


# 從摘要中撈出含數字的句子（Biodesign 要的量化數字）
_NUM_PAT = re.compile(
    r"[^.]*?"
    r"(\d[\d,\.]*\s*(?:%|per\s+100|per\s+1,?000|million|billion|thousand|"
    r"USD|US\$|\$|cases|patients|deaths|days|years))"
    r"[^.]*\.",
    re.I)


def pubmed_abstract(pmid: str) -> str:
    """取 PubMed 摘要全文。"""
    _throttle()
    url = (f"{PUBMED}/efetch.fcgi?db=pubmed&retmode=text"
           f"&rettype=abstract&id={pmid}")
    return _get(url, accept="text/plain", timeout=40) or ""


def extract_numbers(text: str, max_sentences: int = 6) -> list[str]:
    """從摘要擷取帶量化數字的句子。

    用途：Biodesign 的疾病負擔要「具體可量測」的數字，
    不是「這個病很常見」這種描述。
    """
    clean = re.sub(r"\s+", " ", text or "")
    # 去掉期刊抬頭與作者段
    clean = re.sub(r"^(.*?Author information.*?)\n", "", clean, flags=re.S)
    found: list[str] = []
    for m in _NUM_PAT.finditer(clean):
        s = m.group(0).strip()
        if 40 < len(s) < 400:
            found.append(s)
        if len(found) >= max_sentences:
            break
    return found


def burden_report(condition: str, limit: int = 3) -> dict:
    """疾病負擔總覽：四類數字各檢索一次。

    condition 用英文（PubMed 是英文資料庫）。

    檢索策略（實測調校，2026-09，兩輪踩坑後的結論）：

    坑一：布林寫法反而變差
    原本用 `"term" AND (prevalence OR incidence)`，
    PubMed 的 automatic term mapping 被抑制，查 "urinary retention"
    竟回傳「多汗症盛行率」等不相關文獻。

    坑二：太多詞會回 0 篇
    實測詞數與命中率的關係：
      "urinary retention cost economic burden"                    → 有結果
      "urinary retention cost economic burden healthcare exp..."  → 0 篇
      "urinary retention unmet need limitations failure"          → 0 篇
    結論：**每個查詢控制在 3-4 個詞**，過多修飾詞會把結果濾成零。

    另：「未滿足需求」這類抽象概念在 PubMed 上檢索效益差，
    改以「現行治療的失敗率／併發症」切入更實際。
    """
    out: dict = {"condition": condition, "sections": {}, "errors": []}
    tasks = {
        "流行病學": f"{condition} prevalence incidence",
        "經濟負擔": f"{condition} cost economic burden",
        "現行治療": f"{condition} management current treatment",
        "治療侷限": f"{condition} complications failure rate",
    }
    for label, q in tasks.items():
        try:
            arts = pubmed_search(q, limit=limit)
            if not arts:
                out["sections"][label] = {"articles": [], "numbers": []}
                continue
            numbers: list[str] = []
            for a in arts:
                if len(numbers) >= 6:
                    break
                ab = pubmed_abstract(a["pmid"])
                numbers += extract_numbers(ab, max_sentences=3)
                a["numbers"] = extract_numbers(ab, max_sentences=2)
            # 去重
            seen: set[str] = set()
            uniq: list[str] = []
            for n in numbers:
                k = n[:60].lower()
                if k not in seen:
                    seen.add(k)
                    uniq.append(n)
            out["sections"][label] = {
                "articles": [{k: v for k, v in a.items()
                              if not k.startswith("_")} for a in arts],
                "numbers": uniq[:6],
            }
        except Exception as exc:
            out["errors"].append({"section": label, "error": str(exc)[:120]})
    return out


# ---------------------------------------------------------------- WHO GHO

# 常用指標（實測可用的疾病負擔指標）
WHO_INDICATORS = {
    "NCD_GLUC_04": "糖尿病盛行率",
    "NCDMORT3070": "NCD 早死機率",
    "TB_e_inc_tbhiv_100k": "結核病發生率",
    "MORT_100": "每 10 萬人死亡數",
    "WHS4_100": "年齡標準化死亡率",
}


def who_indicator(code: str, country: str = "TWN",
                  top: int = 3) -> list[dict]:
    """取 WHO GHO 指標資料。

    注意：實測**台灣無資料**（非 WHO 會員國）。
    此函式主要供全球／他國比較用。
    """
    q = urllib.parse.quote(f"SpatialDim eq '{country}'")
    url = f"{WHO_GHO}/{code}?$filter={q}&$top={top}"
    try:
        raw = _get(url)
        if not raw:
            return []
        return [{
            "country": x.get("SpatialDim"),
            "year": x.get("TimeDim"),
            "dim": x.get("Dim1", ""),
            "value": x.get("NumericValue"),
        } for x in json.loads(raw).get("value", [])]
    except Exception:
        return []


def who_search_indicator(keyword: str, top: int = 10) -> list[dict]:
    """搜尋 WHO 指標名稱（找對應的疾病指標）。"""
    q = urllib.parse.quote(f"contains(IndicatorName,'{keyword}')")
    url = f"{WHO_GHO}/Indicator?$filter={q}&$top={top}"
    try:
        raw = _get(url)
        if not raw:
            return []
        return [{"code": x.get("IndicatorCode"),
                 "name": x.get("IndicatorName", "")}
                for x in json.loads(raw).get("value", [])]
    except Exception:
        return []


# ---------------------------------------------------------------- World Bank

WB_INDICATORS = {
    "SP.POP.TOTL": "總人口",
    "SP.POP.65UP.TO.ZS": "65 歲以上人口占比",
    "SH.XPD.CHEX.GD.ZS": "醫療支出占 GDP 比例",
    "SH.XPD.CHEX.PC.CD": "每人醫療支出（美元）",
    "SH.MED.BEDS.ZS": "每千人病床數",
}


def worldbank(indicator: str, countries: list[str],
              years: str = "2020:2023") -> list[dict]:
    """取 World Bank 指標。台灣（TWN）無資料。"""
    cs = ";".join(countries)
    url = (f"{WB}/country/{cs}/indicator/{indicator}"
           f"?format=json&per_page=60&date={years}")
    try:
        raw = _get(url)
        if not raw:
            return []
        d = json.loads(raw)
        if not isinstance(d, list) or len(d) < 2 or not d[1]:
            return []
        return [{
            "country": x["country"]["value"],
            "iso": x.get("countryiso3code", ""),
            "year": x["date"],
            "value": x["value"],
            "indicator": WB_INDICATORS.get(indicator, indicator),
        } for x in d[1] if x.get("value") is not None]
    except Exception:
        return []


# ---------------------------------------------------------------- 台灣來源

def mohw_download_links(page_url: str) -> list[str]:
    """從衛福部統計處頁面抓 dl- 下載連結。

    實測：dl- 連結回傳的是真實 PDF（2.2 MB 實測成功）。
    """
    try:
        raw = _get(page_url, accept="text/html", timeout=40)
        if not raw:
            return []
        links = re.findall(r'href="(https://www\.mohw\.gov\.tw/dl-[^"]+)"', raw)
        return list(dict.fromkeys(links))[:20]
    except Exception:
        return []


def nhi_via_proxy(path: str) -> str:
    """經 jina 代理取健保署頁面。

    健保署（www.nhi.gov.tw）對所有程式化請求回 403。
    實測 r.jina.ai 代理可成功取得內容。
    這是繞道方案，非官方 API，不保證長期穩定。
    """
    if not path.startswith("http"):
        path = "https://www.nhi.gov.tw" + path
    try:
        raw = _get(JINA + path, accept="text/plain", timeout=55, retries=1)
        return raw or ""
    except Exception:
        return ""


NHI_KEY_PAGES = {
    "特材給付": "/ch/lp-2552-1.html",
    "健保統計": "/ch/lp-2599-1.html",
    "藥品特材及醫療服務": "/ch/np-2462-1.html",
}


def reimbursement_check(keyword: str = "") -> dict:
    """檢查健保給付現況（特材）。"""
    out: dict = {"pages": {}, "note": "", "error": None}
    for label, path in NHI_KEY_PAGES.items():
        txt = nhi_via_proxy(path)
        if not txt:
            out["error"] = "健保署連線失敗（需經代理，可能暫時不可用）"
            continue
        out["pages"][label] = {
            "url": "https://www.nhi.gov.tw" + path,
            "chars": len(txt),
            "excerpt": re.sub(r"\s+", " ", txt)[:400],
        }
        if keyword:
            hits = [s for s in re.split(r"[。\n]", txt)
                    if keyword in s and 10 < len(s) < 300][:5]
            out["pages"][label]["hits"] = hits
    out["note"] = ("健保給付為台灣醫材商品化的關鍵門檻。"
                   "特材是否納入給付、給付點數與自費差額，"
                   "直接決定醫院採購意願。需人工核對最新支付標準。")
    return out


# ---------------------------------------------------------------- 市場推估

def top_down_market(population: float, prevalence_pct: float,
                    seek_treatment_pct: float,
                    annual_spend_usd: float) -> dict:
    """由上而下市場推估（Biodesign 官方教材方法）。

    Biodesign 的計算鏈（Stanford《Top-Down and Bottom-Up
    Market Sizing Example》，xerostomia 案例）：

      目標人口 × 疾病盛行率 × 就醫比例 × 每人年花費

    每乘一個比例都會縮小範圍，這是刻意的保守做法。
    官方提醒：每個比例都要能追溯到來源，否則只是數字堆疊。
    """
    with_condition = population * (prevalence_pct / 100)
    seeking = with_condition * (seek_treatment_pct / 100)
    total = seeking * annual_spend_usd
    return {
        "population": population,
        "with_condition": round(with_condition),
        "seeking_treatment": round(seeking),
        "annual_spend_per_patient": annual_spend_usd,
        "total_usd": round(total),
        "steps": [
            f"目標人口 {population:,.0f}",
            f"× 盛行率 {prevalence_pct}% = {with_condition:,.0f} 人",
            f"× 就醫比例 {seek_treatment_pct}% = {seeking:,.0f} 人",
            f"× 每人年花費 US${annual_spend_usd:,.0f} = US${total:,.0f}",
        ],
        "caveat": ("每個比例都需可追溯來源。任一比例無來源，"
                   "此數字僅供討論、不可寫入計畫書。"),
    }


def status() -> dict:
    """各來源可用性檢查（供介面顯示）。"""
    results: dict = {}
    # PubMed
    try:
        r = pubmed_search("catheter infection", limit=1)
        results["PubMed"] = {"ok": bool(r), "note": "疾病負擔文獻（英文）"}
    except Exception as exc:
        results["PubMed"] = {"ok": False, "note": str(exc)[:80]}
    # WHO
    try:
        r = who_search_indicator("prevalence", top=1)
        results["WHO GHO"] = {"ok": bool(r), "note": "全球負擔（台灣無資料）"}
    except Exception as exc:
        results["WHO GHO"] = {"ok": False, "note": str(exc)[:80]}
    # 衛福部
    try:
        r = mohw_download_links(
            "https://dep.mohw.gov.tw/DOS/np-5068-113.html")
        results["衛福部統計處"] = {"ok": bool(r),
                                   "note": f"死因統計等下載檔（{len(r)} 個）"}
    except Exception as exc:
        results["衛福部統計處"] = {"ok": False, "note": str(exc)[:80]}
    # 健保署
    try:
        t = nhi_via_proxy("/ch/lp-2552-1.html")
        results["健保署（代理）"] = {"ok": bool(t),
                                     "note": "特材給付（需經代理）"}
    except Exception as exc:
        results["健保署（代理）"] = {"ok": False, "note": str(exc)[:80]}
    return results
