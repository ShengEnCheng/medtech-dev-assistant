"""主流程：輸入產品說明 + 勾選模組 → 產出報告。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import (feedback, module_burden, module_competitor, module_paper,
               module_patent, module_regulatory, report as report_mod)
from .query_terms import derive_device_query, derive_tfda_query, is_generic
from .schema import (
    MODULE_LABELS,
    MODULE_ORDER,
    BurdenResult,
    CompetitorResult,
    PaperResult,
    PatentResult,
    RegulatoryResult,
    Report,
)

ALL_MODULES = MODULE_ORDER


def analyze(product_description: str,
            modules: list[str] | None = None,
            device_query: str | None = None,
            tfda_query: str | None = None,
            condition_query: str | None = None,
            need_statement: str | None = None,
            tfda_db: Path | None = None,
            progress=None) -> Report:
    """執行指定模組並回傳 Report。

    progress: 可選回呼 progress(step: int, total: int, message: str)，
              供 UI 顯示進度。
    """
    modules = [m for m in (modules or ALL_MODULES) if m in MODULE_LABELS]
    if not modules:
        modules = list(ALL_MODULES)

    query = derive_device_query(product_description, device_query)
    tfda_q = (tfda_query or derive_tfda_query(product_description) or query)
    rep = Report(
        generated=date.today().isoformat(),
        product_description=product_description,
        need_statement=need_statement or product_description,
        device_query=query,
        tfda_query=tfda_q,
        modules_run=modules,
    )

    # 進度總數需含最後的彙整步驟（回饋評估不屬於模組，但會 emit）
    total = len(modules) + 1
    step = 0

    def emit(msg: str) -> None:
        if progress:
            progress(step, total, msg)

    if "burden" in modules:
        step += 1
        emit("疾病負擔與市場推估（約需 20-40 秒）…")
        rep.burden = module_burden.run(
            product_description, condition_override=condition_query)
    else:
        rep.burden = BurdenResult()

    if "regulatory" in modules:
        step += 1
        emit("法規路徑判定…")
        rep.regulatory = module_regulatory.run(query, tfda_db, tfda_query=tfda_q)
    else:
        rep.regulatory = RegulatoryResult(query=query)

    if "competitor" in modules:
        step += 1
        emit("競品與市場地景…")
        rep.competitor = module_competitor.run(query, tfda_db, tfda_query=tfda_q)
    else:
        rep.competitor = CompetitorResult(query=query)

    if "patent" in modules:
        step += 1
        emit("專利與 FTO 初篩（約需 20-40 秒）…")
        rep.patent = module_patent.run(query)
    else:
        rep.patent = PatentResult(query=query)

    if "paper" in modules:
        step += 1
        emit("論文先前技術檢索（約需 20-40 秒）…")
        rep.paper = module_paper.run(product_description, query=query)
    else:
        rep.paper = PaperResult(query=query)

    step += 1
    emit("彙整篩選分數調整建議…")
    rep.screening_feedback = feedback.evaluate(
        rep.regulatory, rep.competitor, rep.patent)

    # 資料來源標註
    sources = ["TFDA 醫材許可證資料集（食藥署開放資料）"]
    if "burden" in modules:
        sources += ["PubMed（疾病負擔文獻）", "健保署（特材給付）"]
    if "regulatory" in modules:
        sources += ["openFDA 510(k)", "openFDA Classification"]
    if "competitor" in modules:
        sources += ["openFDA UDI", "openFDA 製造廠登記", "openFDA PMA",
                    "openFDA 召回"]
        if rep.competitor.market_rows:
            sources.append(f"UN Comtrade {rep.competitor.market_year}")
        if rep.competitor.health_spending:
            sources.append("World Bank")
    if "patent" in modules:
        sources += ["FreePatentsOnline（US/EP/WO/JP/DE）"]
    if "paper" in modules:
        sources += ["論文先前技術：Europe PMC／OpenAlex／Crossref／arXiv"
                    "（免 API key）"]
    rep.sources = sources

    return rep


def save(rep: Report, out_dir: Path) -> dict[str, Path]:
    """輸出 JSON 與 Markdown 報告。"""
    import json
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = rep.generated.replace("-", "")
    slug = rep.device_query.replace(" ", "-").replace("/", "-")[:40]
    jp = out_dir / f"{stamp}_{slug}.json"
    mp = out_dir / f"{stamp}_{slug}.md"
    jp.write_text(
        json.dumps(rep.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(report_mod.to_markdown(rep), encoding="utf-8")
    return {"json": jp, "markdown": mp}


def warnings(rep: Report) -> list[str]:
    """回傳需要提醒使用者的注意事項。"""
    out: list[str] = []
    if is_generic(rep.device_query):
        out.append(
            f"FDA／專利檢索詞「{rep.device_query}」為泛用詞，查詢結果可能失焦。"
            "建議在介面上手動指定更精確的檢索詞。")
    if "regulatory" in rep.modules_run and not rep.regulatory.classification:
        out.append("法規模組未取得 FDA 分類資料，建議手動指定檢索詞後重跑。")
    if "regulatory" in rep.modules_run and rep.regulatory.tfda_relaxed:
        out.append(
            f"TFDA 中文檢索詞「{rep.regulatory.tfda_query}」嚴格比對無結果，"
            "台灣同類清單已放寬比對，可能含不相關品項。")
    if "competitor" in rep.modules_run and rep.competitor.taiwan_relaxed:
        out.append(
            f"TFDA 中文檢索詞「{rep.competitor.tfda_query}」嚴格比對無結果，"
            "台灣競品清單已放寬比對，可能含不相關品項。")
    if "patent" in rep.modules_run and rep.patent.degraded:
        out.append("專利模組兩個來源皆未取得結果，已產出檢索詞表供人工檢索。")
    return out
