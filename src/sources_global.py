"""全球醫材資料來源整合。

為什麼需要這個模組：
初版只接了 openFDA 510(k)，導致國際資料停留在 1980-90 年代的美國前導裝置，
完全沒有全球視野。實測後發現 openFDA 還有四個未使用的端點，
加上 UN Comtrade 與 World Bank，才能撐起真正的全球分析。

實測確認的資料源（2026-09）：

| 來源 | 筆數 | 用途 | 關鍵欄位 |
|------|------|------|---------|
| openFDA UDI | 267,842 | 全球已上市器材 | brand_name, company_name, gmdn_terms |
| openFDA registrationlisting | 335,224 | 全球製造廠（41 國） | registration.iso_country_code |
| openFDA PMA | 4,252 | 高風險器材（Class III） | applicant, trade_name |
| openFDA 510(k) | 14 萬+ | 前導裝置、國際競品 | applicant, decision_date |
| openFDA enforcement | 2,617 | 召回紀錄（風險訊號） | recalling_firm, country |
| openFDA classification | 6 千+ | 法規分類 | device_class, regulation_number |
| UN Comtrade | 各國年度 | 醫材進出口額（市場規模） | primaryValue |
| World Bank | 各國年度 | 醫療支出佔 GDP | SH.XPD.CHEX.GD.ZS |

實測不可用（勿再嘗試）：
- Google Patents xhr → 持續 503
- EPO OPS → 403，需註冊 API key（免費但需申請）
- Lens.org → 401/403，需 key
- EUDAMED → Angular SPA，前端路徑拿不到後端 API
- WIPO PATENTSCOPE → JSF 表單，能取結果數但抓不到清單
- AccessGUDID 搜尋端點 → 404（僅單筆 lookup 可用）

所有端點皆無需 API key。
"""

from __future__ import annotations

import time
from collections import Counter
from urllib.error import HTTPError

from .http_client import fda_search_term, get_json

FDA_BASE = "https://api.fda.gov/device"
COMTRADE_BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"

# UN Comtrade 國家代碼（M49）— 主要醫材市場
# 注意：台灣在 Comtrade 的代碼是 158（Other Asia, nes），不是 490
COMTRADE_COUNTRIES: dict[int, str] = {
    840: "美國", 276: "德國", 392: "日本", 156: "中國", 250: "法國",
    826: "英國", 380: "義大利", 528: "荷蘭", 124: "加拿大", 410: "韓國",
    158: "台灣", 36: "澳洲", 699: "印度", 76: "巴西", 756: "瑞士",
    724: "西班牙", 56: "比利時", 484: "墨西哥", 702: "新加坡",
}

# HS 醫材相關碼
HS_CODES: dict[str, str] = {
    "9018": "醫療、外科、牙科或獸醫用儀器及用具",
    "901831": "注射器（含針頭或不含）",
    "901832": "金屬針頭、縫合針",
    "901839": "其他導管、插管、套管",
    "901819": "其他電氣診斷設備",
    "901890": "其他醫療或外科用儀器",
    "9021": "矯形用具、義肢、助聽器",
    "3005": "繃帶、敷料",
}

# 用於判斷製造廠國別的代碼 → 中文名
ISO_COUNTRY_NAMES: dict[str, str] = {
    "US": "美國", "CN": "中國", "MX": "墨西哥", "DE": "德國", "JP": "日本",
    "CR": "哥斯大黎加", "GB": "英國", "NL": "荷蘭", "CA": "加拿大",
    "CZ": "捷克", "FR": "法國", "IE": "愛爾蘭", "BE": "比利時",
    "AU": "澳洲", "TR": "土耳其", "IN": "印度", "HK": "香港",
    "KR": "韓國", "PK": "巴基斯坦", "EG": "埃及", "IT": "義大利",
    "ES": "西班牙", "SE": "瑞典", "DK": "丹麥", "CH": "瑞士",
    "AT": "奧地利", "PL": "波蘭", "IL": "以色列", "SG": "新加坡",
    "MY": "馬來西亞", "TH": "泰國", "TW": "台灣", "BR": "巴西",
    "PT": "葡萄牙", "NO": "挪威", "FI": "芬蘭", "GR": "希臘",
    "HU": "匈牙利", "SK": "斯洛伐克", "SI": "斯洛維尼亞", "LV": "拉脫維亞",
    "LT": "立陶宛", "EE": "愛沙尼亞", "RO": "羅馬尼亞", "BG": "保加利亞",
    "VN": "越南", "PH": "菲律賓", "ID": "印尼", "NZ": "紐西蘭",
    "ZA": "南非", "AR": "阿根廷", "CL": "智利", "CO": "哥倫比亞",
    "PE": "秘魯", "SA": "沙烏地阿拉伯", "AE": "阿聯", "JO": "約旦",
    "UA": "烏克蘭", "RU": "俄羅斯", "RS": "塞爾維亞", "HR": "克羅埃西亞",
}


def country_name(code: str) -> str:
    """ISO 國別代碼轉中文名（找不到時回原代碼）。"""
    if not code:
        return ""
    return ISO_COUNTRY_NAMES.get(code.upper(), code.upper())




# 泛用詞：這些詞單獨出現時不足以定位產品，不可作為檢索詞
_TOO_GENERIC = {
    "device", "system", "kit", "set", "line", "needle", "pad", "probe",
    "sensor", "monitor", "clip", "clamp", "mask", "glove", "syringe",
    "filter", "bag", "cover", "support", "catheter", "dressing", "patch",
    "strap", "belt", "band", "plate", "wire", "port", "valve", "pump",
    "screen", "test", "strip", "sheet", "film", "lens", "tube", "holder",
    "ultrasound", "scanner", "bladder", "urinary", "medical", "surgical",
    "hospital", "clinical", "patient", "care", "health", "therapy",
}


def _candidate_phrases(phrase: str) -> list[str]:
    """產生候選檢索片語，由具體到寬鬆，但不退到無意義的單一泛用詞。

    為什麼需要這層把關：
    初版用「逐漸縮短片語」的方式做 fallback，結果
    「bladder scanner ultrasound」退到「bladder」，
    全球品牌商清單抓回血壓計（Aneroids、Adcuff）等完全不相關的產品。

    規則：
    1. 完整片語優先
    2. 移除結尾修飾詞（ultrasound / device / system 等）
    3. 只保留前 2 個詞
    4. 若候選片語只剩單一泛用詞，直接排除
    """
    words = [w for w in phrase.replace("-", " ").split() if len(w) > 2]
    cands: list[str] = []
    if words:
        cands.append(" ".join(words))
        # 移除結尾的泛用修飾詞
        while len(words) > 1 and words[-1].lower() in _TOO_GENERIC:
            words = words[:-1]
            cands.append(" ".join(words))
        # 前兩詞
        if len(words) >= 2:
            cands.append(" ".join(words[:2]))

    out: list[str] = []
    seen: set[str] = set()
    for c in cands:
        cl = c.lower().strip()
        if not cl or cl in seen:
            continue
        ws = cl.split()
        # 排除單一泛用詞
        if len(ws) == 1 and ws[0] in _TOO_GENERIC:
            continue
        seen.add(cl)
        out.append(c)
    return out or [phrase]


# ------------------------------------------------------------------ openFDA


def fda_udi(device_query: str, limit: int = 100) -> dict:
    """openFDA UDI — 全球已上市器材清單（含品牌名、labeler）。

    27 萬筆規模，是「全球有哪些同類產品」最直接的資料。
    """
    for term in _candidate_phrases(device_query)[:3]:
        for field in ("device_description", "brand_name"):
            url = (f"{FDA_BASE}/udi.json?search="
                   f"{fda_search_term(field, term)}&limit={limit}")
            try:
                d = get_json(url, allow404=True)
            except Exception:
                continue
            if d.get("results"):
                d["_matched_term"] = term
                d["_matched_field"] = field
                return d
    return {"meta": {"results": {"total": 0}}, "results": [], "_matched_term": None}


def fda_registration(device_query: str, limit: int = 200) -> dict:
    """openFDA registrationlisting — 全球製造廠（含製造地國別）。

    這是回答「這產品全球誰在做」最有力的資料：
    33.5 萬筆登記，registration.iso_country_code 直接給製造國。
    """
    for term in _candidate_phrases(device_query)[:3]:
        url = (f"{FDA_BASE}/registrationlisting.json?search="
               f"{fda_search_term('proprietary_name', term)}&limit={limit}")
        try:
            d = get_json(url, allow404=True)
        except Exception:
            continue
        if d.get("results"):
            d["_matched_term"] = term
            return d
    return {"meta": {"results": {"total": 0}}, "results": [], "_matched_term": None}


def fda_pma(device_query: str, limit: int = 30) -> dict:
    """openFDA PMA — 高風險器材（Class III）核准清單。

    4,252 筆。PMA 是 Class III 專用路徑，
    有 PMA 紀錄代表這類產品在美國屬於最高風險等級，需臨床證據。
    """
    for term in _candidate_phrases(device_query)[:3]:
        url = (f"{FDA_BASE}/pma.json?search="
               f"{fda_search_term('trade_name', term)}&limit={limit}")
        try:
            d = get_json(url, allow404=True)
        except Exception:
            continue
        if d.get("results"):
            d["_matched_term"] = term
            return d
    return {"meta": {"results": {"total": 0}}, "results": [], "_matched_term": None}


def fda_recall(device_query: str, limit: int = 30) -> dict:
    """openFDA 召回紀錄 — 競品的品質風險訊號。

    召回資料是競品分析的隱藏金礦：
    同類產品若常有召回，代表技術難度高或品管門檻高，
    既是風險也是切入機會（可訴求更可靠的設計）。
    """
    for term in _candidate_phrases(device_query)[:3]:
        url = (f"{FDA_BASE}/enforcement.json?search="
               f"{fda_search_term('product_description', term)}&limit={limit}")
        try:
            d = get_json(url, allow404=True)
        except Exception:
            continue
        if d.get("results"):
            d["_matched_term"] = term
            return d
    return {"meta": {"results": {"total": 0}}, "results": [], "_matched_term": None}


# ------------------------------------------------------------------ Comtrade


def comtrade_imports(hs_code: str = "9018", year: int = 2022,
                     countries: list[int] | None = None,
                     retries: int = 3) -> dict:
    """UN Comtrade — 各國醫材進口額（市場規模的官方量化依據）。

    實測注意：
    1. 公開預覽端點有速率限制，連續查詢會回 429，需要退避重試。
    2. reporterCode=0（世界總計）回傳 0 筆 —— 要用各國加總，不能用世界總計。
    3. reporterISO 欄位常為 None，需自行用 reporterCode 對照。

    回傳 {"rows": [{"country","value_usd",...}], "year", "hs_code"}
    """
    if countries is None:
        countries = list(COMTRADE_COUNTRIES)

    codes = ",".join(str(c) for c in countries)
    url = (f"{COMTRADE_BASE}?reporterCode={codes}&period={year}"
           f"&cmdCode={hs_code}&flowCode=M&partnerCode=0")

    for attempt in range(retries):
        try:
            d = get_json(url, timeout=40.0, allow404=True)
            break
        except HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(8 * (attempt + 1))  # 公開端點速率限制，需明顯退避
                continue
            return {"rows": [], "year": year, "hs_code": hs_code,
                    "error": f"HTTP {exc.code}"}
        except Exception as exc:
            return {"rows": [], "year": year, "hs_code": hs_code,
                    "error": str(exc)[:120]}
    else:
        return {"rows": [], "year": year, "hs_code": hs_code,
                "error": "重試耗盡"}

    raw = d.get("data") or []
    # 同一國可能多筆（不同 partner/mot），取 partnerCode=0 的那筆為國別總額
    best: dict[int, dict] = {}
    for r in raw:
        rc = r.get("reporterCode")
        if rc is None or rc == 0:
            continue
        if r.get("partnerCode") not in (0, None):
            continue
        v = r.get("primaryValue") or 0
        if rc not in best or v > (best[rc].get("primaryValue") or 0):
            best[rc] = r

    rows = []
    for rc, r in best.items():
        rows.append({
            "country": COMTRADE_COUNTRIES.get(rc, str(rc)),
            "reporter_code": rc,
            "value_usd": r.get("primaryValue") or 0,
            "year": r.get("refYear") or year,
        })
    rows.sort(key=lambda x: -x["value_usd"])
    return {"rows": rows, "year": year, "hs_code": hs_code,
            "hs_desc": HS_CODES.get(hs_code, ""), "raw_count": len(raw)}


def comtrade_total(rows: list[dict]) -> float:
    """由各國進口額加總估算全球市場規模。

    注意：這是「主要市場進口額加總」，不是全球市場總值。
    未涵蓋的國家與在地製造銷售不計入，屬保守下限。
    """
    return sum(r.get("value_usd") or 0 for r in rows)


# --------------------------------------------------------------- World Bank


def worldbank_health_spending(countries: list[str] | None = None,
                              year: int = 2021) -> list[dict]:
    """World Bank — 各國醫療支出佔 GDP 比例（市場購買力參考）。

    指標 SH.XPD.CHEX.GD.ZS。用來判斷某國醫療市場的相對規模與投入程度。
    """
    codes = ";".join(countries) if countries else "all"
    url = (f"{WORLD_BANK_BASE}/country/{codes}/indicator/"
           f"SH.XPD.CHEX.GD.ZS?format=json&per_page=400&date={year}")
    try:
        d = get_json(url, timeout=30.0)
    except Exception as exc:
        return []
    if not isinstance(d, list) or len(d) < 2:
        return []
    out = []
    for item in d[1] or []:
        v = item.get("value")
        if v is None:
            continue
        out.append({
            "country": (item.get("country") or {}).get("value") or "",
            "iso3": (item.get("countryiso3code") or ""),
            "value_pct": round(v, 1),
            "year": item.get("date"),
        })
    out.sort(key=lambda x: -x["value_pct"])
    return out


# ------------------------------------------------------------ 彙整用工具


def aggregate_countries(registrations: list[dict]) -> list[dict]:
    """由 registrationlisting 結果統計製造廠國別分布。"""
    c = Counter()
    for r in registrations:
        reg = r.get("registration") or {}
        if isinstance(reg, dict):
            code = (reg.get("iso_country_code") or "").strip()
            if code:
                c[code] += 1
    return [
        {"iso": k, "country": country_name(k), "count": v}
        for k, v in c.most_common()
    ]


def aggregate_companies(udi_results: list[dict], top: int = 20) -> list[dict]:
    """由 UDI 結果統計 labeler（品牌商）分布。"""
    c = Counter()
    for r in udi_results:
        name = (r.get("company_name") or "").strip()
        if name:
            c[name] += 1
    return [{"company": k, "count": v} for k, v in c.most_common(top)]


def to_company_groups(udi_results: list[dict], top: int = 20) -> list[dict]:
    """由 UDI 結果彙整公司群組（含樣本品牌名）。"""
    agg: dict[str, dict] = {}
    for r in udi_results:
        name = (r.get("company_name") or "").strip()
        if not name:
            continue
        a = agg.setdefault(name, {"company": name, "count": 0, "samples": [],
                                  "latest": ""})
        a["count"] += 1
        bn = (r.get("brand_name") or "").strip()
        if bn and len(a["samples"]) < 2:
            a["samples"].append(bn[:70])
        d = (r.get("public_version_date") or "")[:10]
        if d > a["latest"]:
            a["latest"] = d
    return sorted(agg.values(), key=lambda x: -x["count"])[:top]


def to_country_groups(registrations: list[dict], top: int = 25) -> list[dict]:
    """由 registrationlisting 彙整製造國群組（含樣本廠商名）。"""
    agg: dict[str, dict] = {}
    for r in registrations:
        reg = r.get("registration") or {}
        if not isinstance(reg, dict):
            continue
        code = (reg.get("iso_country_code") or "").strip().upper()
        if not code:
            continue
        a = agg.setdefault(code, {"iso": code, "country": country_name(code),
                                  "count": 0, "samples": []})
        a["count"] += 1
        nm = (reg.get("name") or "").strip()
        if nm and len(a["samples"]) < 3:
            a["samples"].append(nm[:60])
    return sorted(agg.values(), key=lambda x: -x["count"])[:top]
