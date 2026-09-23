"""主流程：輸入產品說明 + 勾選模組 → 產出報告。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import (feedback, module_burden, module_competitor, module_paper,
               module_patent, module_regulatory, report as report_mod)
from .query_terms import (derive_device_query, derive_query_plan,
                          derive_tfda_query, is_generic)
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
            use_llm: bool = False,
            progress=None) -> Report:
    """執行指定模組並回傳 Report。

    use_llm : 啟用語言模型協助抽取檢索詞。抽出的候選一律經實測驗證
              （文獻用相關性判定、中文品名用 TFDA 實際命中數），
              不因 LLM 說了就採用。失敗時自動退回規則式。
    progress: 可選回呼 progress(step: int, total: int, message: str)，
              供 UI 顯示進度。
    """
    modules = [m for m in (modules or ALL_MODULES) if m in MODULE_LABELS]
    if not modules:
        modules = list(ALL_MODULES)

    # 檢索詞計畫：一次產出裝置／疾病／技術三組詞
    # （單一檢索詞餵所有資料庫是舊設計，實測會撈到完全無關的結果）
    plan = derive_query_plan(
        product_description,
        override_device=device_query,
        override_tfda=tfda_query,
        override_condition=condition_query,
        use_llm=use_llm,
    )

    query = plan["device"]

    # ---------- TFDA 中文品名：LLM 候選須經實測挑選 ----------
    #
    # 為什麼要實測而非照單全收（實測數據）：
    #   尿布案  LLM 提「尿液分析儀」  → TFDA 99 筆 ✅
    #   尿布案  LLM 提「失禁警報器」  → TFDA  0 筆
    #   心衰竭  LLM 提「心臟血管監測器」→ TFDA  0 筆，但「生理監視器」39 筆 ✅
    # LLM 提的是「可能的詞」，實際命中數才是判準。
    # 若 LLM 候選全部查不到，保留規則式的結果。
    #
    # **必須在建立 Report 之前執行**：Report.tfda_query 是競品與法規
    # 模組實際使用的檢索詞。曾經因為順序錯誤（先建 Report 才挑詞），
    # 導致挑出的中文詞沒被使用，TFDA 檢索仍用英文詞而全部落空。
    if plan.get("llm_used") and tfda_db:
        try:
            from .query_terms import derive_tfda_terms, verify_tfda_candidates
            from . import tfda_realnames as trn

            # 候選池 = LLM 憑空生成的詞 + 規則式詞組
            _cands = list(plan.get("llm_tfda") or [])
            _base_terms = derive_tfda_terms(product_description)

            # 加上「真實品名回饋」：先用功能／部位詞撈出 TFDA 實際存在的
            # 品名，讓 LLM 從中挑選，再取去品牌的核心詞。
            #
            # 為什麼這一步最有效（實測）：
            #   LLM 憑空生成「心臟血管監測器」→ 0 筆
            #   從真實品名挑出的核心詞「生理監視器」→ 39 筆 ✅
            # LLM 不知道台灣 TFDA 的品名慣用語；給它真實清單挑選，
            # 它就不可能編造出不存在的品名，準確度大幅提升。
            # 種子詞策略：用「部位／功能」的通用中文詞撈出真實品名。
            #
            # 為什麼不能只用技術詞與規則式詞組當種子（實測 bug）：
            # 心衰竭案的規則式詞組是空的、技術詞是英文，
            # 撈不到任何品名 → 真實品名回饋完全沒觸發。
            # 需要額外補「產品說明中出現的中文部位／功能詞」。
            _seeds: list[str] = []
            for t in (plan.get("llm_tfda") or []):
                _seeds.append(t)
            for t in _base_terms:
                _seeds.append(t)
            # 從說明中抓中文名詞片段（2-4 字），作為部位／功能線索
            import re as _re
            _zh = _re.findall(r"[\u4e00-\u9fff]{2,4}", product_description or "")
            # 只取高頻且有鑑別力的（去掉純連接詞與贅字）
            _STOP_ZH = {"因此", "目前", "主要", "由於", "以及", "並且", "透過",
                        "使用", "提供", "進行", "開發", "整合", "建立", "分析",
                        "系統", "裝置", "設備", "訊號", "資訊", "數據", "結果",
                        "方式", "期間", "期間", "病人", "患者", "臨床", "應用",
                        "技術", "產品", "功能", "需求", "照護", "醫療", "團隊"}
            from collections import Counter as _C
            _cnt = _C(w for w in _zh if w not in _STOP_ZH and len(w) >= 2)
            _seeds += [w for w, _ in _cnt.most_common(8)]

            _real = trn.fetch_real_product_names(
                tfda_db, list(dict.fromkeys(_seeds))[:14], limit=40)
            if _real:
                _picked = trn.pick_from_real(product_description, _real)
                plan["tfda_realnames"] = {
                    "count": len(_real),
                    "picks": _picked.get("picks") or [],
                    "cores": _picked.get("cores") or [],
                    "reason": _picked.get("reason", ""),
                }
                _cands += (_picked.get("cores") or [])
                _cands += (_picked.get("picks") or [])

            _tfda_v = verify_tfda_candidates(tfda_db, _cands,
                                             baseline_terms=_base_terms)
            plan["tfda_verification"] = _tfda_v

            # 品類層檢索詞：用於查「這個品類有多少玩家」。
            # 取 LLM 候選、規則式詞組、與真實品名核心詞中長度適中者
            # （實測「膀胱」47 筆、「超音波膀胱掃描儀」1 筆 ——
            #  品類規模要看前者，最接近產品要看後者）。
            # 品類詞要排除「純模態詞」（超音波、X光…）。
            # 實測：膀胱案若把「超音波」納入品類層 → 348 件、
            # 含富士軟片與飛利浦等通用超音波影像廠商，並非膀胱品類。
            # 純模態詞是「用什麼技術做」，不是「做什麼產品」。
            from .query_terms import _tfda_specificity, _TFDA_GENERIC_ZH
            _cat: list[str] = []
            for _t in ((plan.get("llm_tfda") or []) + _base_terms
                       + ((plan.get("tfda_realnames") or {}).get("cores") or [])):
                _t = (_t or "").strip()
                if not (2 <= len(_t) <= 12) or _t in _cat:
                    continue
                if _t in _TFDA_GENERIC_ZH:
                    continue
                _cat.append(_t)
            # 依具體度排序，具體的先（品類詞優於技術手段詞）
            _cat.sort(key=lambda x: -_tfda_specificity(x))
            plan["tfda_category_terms"] = _cat[:6]
            _best_tfda = _tfda_v.get("chosen") or ""
            if _best_tfda:
                plan["tfda"] = _best_tfda
                plan["source"]["tfda"] = "實測挑選"
            elif not plan.get("tfda"):
                # 候選全數落空且規則式也沒抽到詞時，不要讓 TFDA 檢索詞
                # 變成空的（會讓整個台灣區塊無聲消失）。
                # 退回「醫材分類分級」檢索：用 device 的英文詞對不上中文
                # 品名，改用分類欄位（category）查詢。
                plan["tfda_empty_fallback"] = True
                plan["warnings"].append(
                    "TFDA 中文品名候選皆查無既有品名（創新產品常見）。"
                    "台灣區塊改以醫材分類查詢，或請於「進階設定」"
                    "手動輸入最接近的品名（例：生理監視器）。")
        except Exception:
            pass

    # TFDA 檢索詞：中文優先（TFDA 品名是中文，用英文永遠對不上）
    tfda_q = plan["tfda"] or query

    rep = Report(
        generated=date.today().isoformat(),
        product_description=product_description,
        need_statement=need_statement or product_description,
        device_query=query,
        tfda_query=tfda_q,
        modules_run=modules,
    )
    # 檢索詞計畫寫入報告（介面顯示與除錯用）
    rep.query_plan = plan
    # 論文模組用技術詞（文獻檢索的核心），沒有時退回裝置詞
    tech_query = plan["tech"][0] if plan.get("tech") else query

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
            product_description,
            condition_override=plan["condition"] or condition_query)
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
        rep.competitor = module_competitor.run(
            query, tfda_db, tfda_query=tfda_q,
            category_terms=plan.get("tfda_category_terms") or [])
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
        # 論文檢索用「技術詞」而非裝置品名 —— 文獻裡的正式名詞是
        # ballistocardiography，不是 wearable device。
        rep.paper = module_paper.run(product_description, query=tech_query,
                                     plan=plan)
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
