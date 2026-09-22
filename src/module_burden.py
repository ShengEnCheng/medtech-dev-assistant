"""M4 疾病負擔與市場推估（Biodesign Stage 2.1 / 2.2 / 2.4）。

對應 Biodesign 需求篩選的核心問題：
  2.1 疾病狀態基礎 — 這病有多嚴重？（發生率、盛行率、經濟負擔）
  2.2 治療選項     — 現在的照護方式是什麼？哪裡不夠好？
  2.4 市場分析     — 多少人需要？現在花多少錢？

為什麼這個模組才是 Biodesign 的核心：
前三個模組（法規／專利／競品）回答「市面上有什麼」，
但 Biodesign 的需求陳述（need statement）建立在「疾病負擔」上。
Stanford 官方需求簡報的格式是固定的：

  背景 → 觀察 → 問題 → 市場

以官方 2010 年範例為例：
  心房顫動 → 「美國約 250 萬病人；導管消融僅 70-80% 有效」
  心衰竭   → 「每年 350 萬次住院；約 100 億美元醫療支出」
  壓瘡     → 「每年超過 100 萬例；每例治療 4 千至 4 萬美元」

注意這些數字的性質：**疾病發生率、現有療法失效比例、每人花費**。
全都是臨床與流行病學數字，不是貿易統計。

實測來源（2026-09-22）：
- PubMed E-utilities：疾病負擔文獻（英文），可撈出含數字的句子
- WHO GHO：全球疾病指標（**台灣無資料**，非會員國）
- World Bank：各國人口與醫療支出（**台灣無資料**）
- 衛福部統計處：台灣死因、長照統計（dl- 連結為真實 PDF）
- 國健署：國民健康訪問調查
- 健保署：特材給付（全站 403，需經 r.jina.ai 代理）

重要限制：
- PubMed 給的是**文獻摘要**，非結構化數字。摘取的數字需人工判讀。
- 由上而下市場推估需要四個輸入，任一無來源就只是數字堆疊。
"""

from __future__ import annotations

from . import source_burden as sb
from .query_terms import derive_condition
from .schema import BurdenResult


def run(description: str, condition_override: str | None = None,
        include_reimbursement: bool = True,
        market_inputs: dict | None = None) -> BurdenResult:
    """執行疾病負擔與市場分析。

    description：產品說明（中文），用來抽取疾病檢索詞
    condition_override：手動指定疾病檢索詞（英文），覆蓋自動抽取
    market_inputs：由上而下市場推估的輸入（可選）
        {"population": ..., "prevalence_pct": ...,
         "seek_treatment_pct": ..., "annual_spend_usd": ...}
    """
    out = BurdenResult()
    condition = derive_condition(description, condition_override)
    out.condition = condition

    if not condition:
        out.note = ("未能從產品說明識別疾病／臨床狀態，無法查詢疾病負擔。"
                    "請於進階設定手動指定（英文，例：urinary retention）。")
        return out

    # --- 疾病負擔四類數字 ---
    try:
        rep = sb.burden_report(condition, limit=3)
        out.sections = rep.get("sections", {})
        out.errors = rep.get("errors", [])
    except Exception as exc:
        out.errors.append({"source": "PubMed", "error": str(exc)[:150]})

    # --- 健保給付 ---
    if include_reimbursement:
        try:
            out.reimbursement = sb.reimbursement_check()
        except Exception as exc:
            out.errors.append({"source": "健保署", "error": str(exc)[:150]})

    # --- 由上而下市場推估（Biodesign 官方方法）---
    if market_inputs:
        try:
            need = ["population", "prevalence_pct",
                    "seek_treatment_pct", "annual_spend_usd"]
            if all(k in market_inputs for k in need):
                out.market_calc = sb.top_down_market(
                    float(market_inputs["population"]),
                    float(market_inputs["prevalence_pct"]),
                    float(market_inputs["seek_treatment_pct"]),
                    float(market_inputs["annual_spend_usd"]),
                )
        except Exception as exc:
            out.errors.append({"source": "市場推估", "error": str(exc)[:150]})

    out.note = (
        "疾病負擔數字取自 PubMed 文獻摘要，為**原文引用**非本工具推算，"
        "惟摘要擷取可能混入不相關文獻，需人工核對。"
        "健保給付為台灣商品化關鍵門檻，須核對最新支付標準。"
        "市場規模請採由上而下與由下而上雙向計算，"
        "兩者落差本身就是重要資訊（Biodesign 官方教材）。"
    )
    return out
