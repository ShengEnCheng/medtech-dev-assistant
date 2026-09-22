"""M3 競品與市場地景（全球版）。

對應 Biodesign 教科書 Stage 2.4 Market Analysis 與 Stage 4.4 Business Models。

做什麼：
  1. 台灣競品 — TFDA 許可證資料集（本地索引）
  2. 全球已上市器材 — openFDA UDI（26.7 萬筆，含品牌商）
  3. 全球製造廠分布 — openFDA registrationlisting（33.5 萬筆、41 國）
  4. 高風險器材 — openFDA PMA（4,252 筆 Class III）
  5. 競品召回紀錄 — openFDA enforcement（品質風險訊號）
  6. 全球市場規模 — UN Comtrade 各國醫材進口額（官方貿易統計）
  7. 各國醫療支出 — World Bank（市場購買力參考）

為什麼要分成這麼多層：
初版只接了 openFDA 510(k)，國際資料停留在 1980-90 年代的美國前導裝置，
完全沒有全球視野。實測發現 openFDA 還有四個未使用的端點，
加上 UN Comtrade 與 World Bank，才能撐起真正的全球分析。

明確不做：
  不估算 TAM / SAM / SOM，不產出市占率預測。
  市場規模只呈現官方貿易統計與各國支出，不代團隊做市場推論。
"""

from __future__ import annotations

from pathlib import Path

from . import sources_global as g
from . import tfda_index
from .schema import (
    CompetitorResult,
    InternationalCompetitor,
    LicenseeGroup,
    RecallEntry,
    TODAY,
)


def run(device_query: str, tfda_db: Path | None = None,
        tfda_limit: int = 200, fda_limit: int = 50,
        tfda_query: str | None = None,
        include_market: bool = True) -> CompetitorResult:
    """執行競品與市場地景分析（全球）。

    tfda_query 為中文檢索詞（TFDA 品名是中文，英文檢索詞查不到）。
    include_market 控制是否查 UN Comtrade / World Bank（較慢，約 10-30 秒）。
    """
    out = CompetitorResult(query=device_query, query_date=TODAY)

    # 1. 台灣已取證廠商（TFDA 本地索引）
    tq = tfda_query or device_query
    out.tfda_query = tq
    if tfda_db and tfda_db.exists():
        try:
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

    # 2. 全球已上市器材（openFDA UDI）— 取代舊的 510(k) 作為國際競品主來源
    try:
        udi = g.fda_udi(device_query, limit=200)
        out.udi_total = udi.get("meta", {}).get("results", {}).get("total")
        out.udi_matched_term = udi.get("_matched_term")
        out.global_companies = g.to_company_groups(udi.get("results", []), top=20)
        out.international = [
            InternationalCompetitor(
                applicant=c["company"],
                clearance_count=c["count"],
                latest_clearance=c.get("latest") or "",
                samples=c.get("samples") or [],
            )
            for c in out.global_companies
        ]
    except Exception as exc:
        out.errors.append({"source": "openFDA UDI", "error": str(exc)[:150]})

    # 3. 全球製造廠國別分布（openFDA registrationlisting）
    try:
        reg = g.fda_registration(device_query, limit=1000)
        out.manufacturer_total = reg.get("meta", {}).get("results", {}).get("total")
        out.manufacturer_countries = g.to_country_groups(
            reg.get("results", []), top=25)
    except Exception as exc:
        out.errors.append(
            {"source": "openFDA registrationlisting", "error": str(exc)[:150]})

    # 4. 高風險器材（openFDA PMA）
    try:
        pma = g.fda_pma(device_query, limit=30)
        out.pma_total = pma.get("meta", {}).get("results", {}).get("total")
        out.pma_entries = [
            {
                "pma_number": x.get("pma_number") or "",
                "applicant": x.get("applicant") or "",
                "trade_name": (x.get("trade_name") or "")[:80],
                "date": x.get("decision_date") or "",
            }
            for x in (pma.get("results") or [])[:10]
        ]
    except Exception as exc:
        out.errors.append({"source": "openFDA PMA", "error": str(exc)[:150]})

    # 5. 召回紀錄（品質風險訊號）
    try:
        rc = g.fda_recall(device_query, limit=200)
        out.recall_total = rc.get("meta", {}).get("results", {}).get("total")
        firm_count: dict[str, int] = {}
        entries: list[RecallEntry] = []
        for it in rc.get("results") or []:
            f = (it.get("recalling_firm") or "").strip()
            if f:
                firm_count[f] = firm_count.get(f, 0) + 1
            if len(entries) < 12:
                entries.append(RecallEntry(
                    firm=f,
                    product=(it.get("product_description") or "")[:100],
                    classification=it.get("classification") or "",
                    reason=(it.get("reason_for_recall") or "")[:120],
                    date=(it.get("report_date") or ""),
                    country=it.get("country") or "",
                ))
        out.recall_firms = [
            {"firm": k, "count": v}
            for k, v in sorted(firm_count.items(), key=lambda x: -x[1])[:12]
        ]
        out.recall_entries = entries
    except Exception as exc:
        out.errors.append(
            {"source": "openFDA enforcement", "error": str(exc)[:150]})

    # 6. 全球市場規模（UN Comtrade，較慢）
    if include_market:
        try:
            cm = g.comtrade_imports("9018", 2022)
            out.market_rows = cm.get("rows", [])
            out.market_hs = cm.get("hs_code", "9018")
            out.market_year = cm.get("year", 2022)
            out.market_error = cm.get("error")
            out.market_total_usd = g.comtrade_total(out.market_rows)
        except Exception as exc:
            out.errors.append({"source": "UN Comtrade", "error": str(exc)[:150]})

        # 7. 各國醫療支出（World Bank）
        try:
            out.health_spending = g.worldbank_health_spending(
                ["US", "DE", "JP", "TW", "KR", "CN", "GB", "FR"], 2021)
        except Exception as exc:
            out.errors.append({"source": "World Bank", "error": str(exc)[:150]})

    # 8. 競爭密度判定（台灣 + 全球雙軌）
    n = out.taiwan_total
    if out.taiwan_relaxed:
        tw_dens = (f"台灣資料不足 — 中文檢索詞「{out.tfda_query}」嚴格檢索無結果，"
                   "已放寬比對（可能含不相關品項）")
    elif n >= 50:
        tw_dens = f"台灣密集（{n} 件）— 多家廠商已取證，需明確差異化"
    elif n >= 10:
        tw_dens = f"台灣中等（{n} 件）— 有既有廠商但選擇有限"
    elif n > 0:
        tw_dens = f"台灣稀薄（{n} 件）— 取證廠商少，需確認是市場小或需求未滿足"
    else:
        tw_dens = f"台灣無對應資料（中文檢索詞「{out.tfda_query}」）"

    gd = out.manufacturer_total or 0
    if gd >= 2000:
        gl_dens = f"全球密集（{gd:,} 家登記製造廠）"
    elif gd >= 300:
        gl_dens = f"全球中等（{gd:,} 家登記製造廠）"
    elif gd > 0:
        gl_dens = f"全球稀薄（{gd:,} 家登記製造廠）"
    else:
        gl_dens = "全球資料未取得"

    out.density = f"{tw_dens}　|　{gl_dens}"
    return out
