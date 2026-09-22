"""M3 競品與市場地景。

對應 Biodesign 教科書 Stage 2.4 Market Analysis 與 Stage 4.4 Business Models。

做什麼：
  1. 台灣競品名單 — 從 TFDA 許可證資料集撈同類產品的申請商
  2. 國際競品 — openFDA 510(k) 的申請人分群
  3. 競爭密度判定 — 依台灣取證件數分級

為什麼台灣名單最有價值：
  在台灣有取證的廠商，就是團隊實際上會遇到的競爭者。
  這份資料是官方、可查證、且別人的工具拿不到（多數競品工具只看國際市場）。

明確不做：
  不估算 TAM / SAM / SOM，不產出市占率。
  這些需要真實市場報告，用公開取證資料推估會誤導團隊。
"""

from __future__ import annotations

from pathlib import Path

from .http_client import fda_search_term, get_json
from .schema import (
    CompetitorResult,
    InternationalCompetitor,
    LicenseeGroup,
    TODAY,
)
from . import tfda_index
from .module_regulatory import _fda_query

FDA_510K_URL = "https://api.fda.gov/device/510k.json"


def run(device_query: str, tfda_db: Path | None = None,
        tfda_limit: int = 200, fda_limit: int = 50,
        tfda_query: str | None = None) -> CompetitorResult:
    """tfda_query 為中文檢索詞（TFDA 品名是中文，英文檢索詞查不到）。"""
    out = CompetitorResult(query=device_query, query_date=TODAY)

    # 1. 台灣已取證廠商（最有價值）
    tq = tfda_query or device_query
    out.tfda_query = tq
    if tfda_db and tfda_db.exists():
        try:
            # 漸進式檢索：由最具體的詞開始，達標就停（避免泛用詞灌水）
            terms = tq.replace("-", " ").split() or [tq]
            meta = tfda_index.search_progressive(tfda_db, terms, limit=tfda_limit)
            rows = meta["rows"]
            out.taiwan_relaxed = meta["relaxed"]
            out.taiwan_total = meta["total"]
            out.tfda_used_terms = meta["used_terms"]
            out.taiwan_licensees_sample = len(rows)
            agg: dict[str, dict] = {}
            for r in rows:
                name = (r.get("applicant") or "（未載明）").strip() or "（未載明）"
                a = agg.setdefault(name, {
                    "applicant": name, "count": 0, "categories": set(), "samples": [],
                })
                a["count"] += 1
                if r.get("category"):
                    a["categories"].add(r["category"])
                if len(a["samples"]) < 2 and r.get("name_zh"):
                    a["samples"].append(r["name_zh"])
            out.taiwan_licensees = [
                LicenseeGroup(
                    applicant=v["applicant"],
                    license_count=v["count"],
                    categories=sorted(v["categories"]),
                    samples=v["samples"],
                )
                for v in sorted(agg.values(), key=lambda x: -x["count"])
            ][:15]
        except Exception as exc:
            out.errors.append({"source": "TFDA", "error": str(exc)})

    # 2. 國際競品（openFDA 510(k) 申請人分群）
    try:
        data = _fda_query(FDA_510K_URL, device_query, limit=fda_limit)
        agg: dict[str, dict] = {}
        for item in data.get("results", []):
            ap = (item.get("applicant") or "").strip()
            if not ap:
                continue
            a = agg.setdefault(ap, {"applicant": ap, "count": 0, "latest": ""})
            a["count"] += 1
            d = item.get("decision_date") or ""
            if d > a["latest"]:
                a["latest"] = d
        out.international = [
            InternationalCompetitor(
                applicant=v["applicant"],
                clearance_count=v["count"],
                latest_clearance=v["latest"],
            )
            for v in sorted(agg.values(), key=lambda x: -x["count"])
        ][:15]
    except Exception as exc:
        out.errors.append({"source": "openFDA 510k", "error": str(exc)})

    # 3. 競爭密度判定（僅依件數級距，不做市占推論）
    n = out.taiwan_total
    if out.taiwan_relaxed:
        out.density = (
            f"資料不足 — 中文檢索詞「{out.tfda_query}」在 TFDA 嚴格檢索無結果，"
            "已放寬比對（結果可能含不相關品項），建議改用品類檢索或人工確認"
        )
    elif n >= 50:
        out.density = "密集 — 台灣已有多家取證廠商，需明確差異化才能切入"
    elif n >= 10:
        out.density = "中等 — 有既有廠商但選擇有限，差異化空間存在"
    elif n > 0:
        out.density = "稀薄 — 取證廠商少，需確認是市場小或需求未被滿足"
    else:
        out.density = (
            f"無對應資料（中文檢索詞「{out.tfda_query}」）— "
            "建議改用醫器主類別檢索"
        )
    return out
