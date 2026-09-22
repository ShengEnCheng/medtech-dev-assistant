"""Evidence 回饋引擎。

把三個模組的外部證據，回寫成 Biodesign Identify 階段篩選評分的調整建議。

設計理由：
  三模組不是三個獨立的查詢工具，而是把 Stage 2 篩選評分裡既有的三個維度
  （Technical Feasibility、Regulatory / Reimbursement Risk、Market Potential）
  從「AI 文字推論」升級成「外部資料佐證」。

重要：這只是「調整建議」。最終分數由團隊與 mentor 確認，
      系統不能代替人做 Go / No-Go 決策。
"""

from __future__ import annotations

from .schema import CompetitorResult, PatentResult, RegulatoryResult

_DIMENSION_LABELS = {
    "technical_feasibility": "Technical Feasibility",
    "regulatory_risk": "Regulatory / Reimbursement Risk",
    "market_potential": "Market Potential",
}


def _technical_feasibility(patent: PatentResult) -> tuple[int, str]:
    """專利密度高 → 需設計迴避 → 可行性略降。

    密度判定只用「高相關專利」（標題命中檢索詞者），
    不用全文檢索命中數 —— 後者動輒數千至上萬筆，不是相關件數。
    """
    if patent.degraded:
        return 0, "專利檢索未取得結果，維持 AI 初判分數（需人工補檢索）"

    high = len(patent.high_risk or [])

    if high >= 15:
        return -1, (f"專利高度密集（標題高相關 {high} 件），需設計迴避")
    if high >= 5:
        return 0, f"專利有一定密度（高相關 {high} 件），仍可實施但需確認 FTO"
    if high > 0:
        return 0, f"專利密度低（高相關 {high} 件），技術空間相對開放"
    return 0, "未檢出高相關專利，建議擴大檢索詞再確認"


def _regulatory_risk(regulatory: RegulatoryResult) -> tuple[int, str]:
    """分類越高 → 法規風險越高（此為負向指標，分數越高越不利）。"""
    classes = {c.device_class for c in regulatory.classification if c.device_class}
    if not classes:
        return 0, "未取得分類資料，維持 AI 初判（建議手動指定檢索詞）"
    if classes == {"3"}:
        return +2, "Class III／需 PMA，法規風險最高"
    if "3" in classes:
        return +1, f"部分適應症落 Class III（{sorted(classes)}），需釐清範圍"
    if classes == {"2"}:
        return 0, "Class II／需 510(k)，路徑明確、風險中等"
    if classes == {"1"}:
        return -1, "Class I，法規負擔低"
    return 0, f"分類橫跨 {sorted(classes)}，需逐項確認適應症差異"


def _market_potential(competitor: CompetitorResult) -> tuple[int, str]:
    """台灣取證家數：代表市場已被驗證，同時也代表競爭。"""
    n = competitor.taiwan_total
    if n >= 50:
        return 0, f"台灣已取證 {n} 件、多家廠商 — 市場存在但競爭明顯"
    if n >= 10:
        return +1, f"台灣已取證 {n} 件 — 市場已驗證，競爭有限"
    if n > 0:
        return 0, f"台灣僅 {n} 件取證 — 需釐清是市場小或需求未被滿足"
    return 0, "未取得對應資料，建議改用醫器主類別重新檢索"


def evaluate(regulatory: RegulatoryResult, competitor: CompetitorResult,
             patent: PatentResult) -> dict:
    """產出三維分數調整建議。"""
    adjustments = {
        "technical_feasibility": _technical_feasibility(patent),
        "regulatory_risk": _regulatory_risk(regulatory),
        "market_potential": _market_potential(competitor),
    }
    return {
        "adjustments": {
            k: {"delta": v[0], "reason": v[1], "label": _DIMENSION_LABELS[k]}
            for k, v in adjustments.items()
        },
        "note": "此為證據佐證的調整建議，最終分數由團隊與 mentor 確認。",
    }
