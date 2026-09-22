"""M2 法規路徑判定。

對應 Biodesign 教科書 Stage 4.2 Regulatory Basics。

做什麼：
  以 openFDA 裝置分類資料庫為主要依據（直接給 device_class 與法規編號），
  用 openFDA 510(k) 找前導裝置（predicate device），
  用 TFDA 本地索引找台灣同類已取證產品。

為什麼不是「問 AI」：
  class I / II / III 的差別是天價級的 —— 標準 PMA 送審費約 US$441,547，
  標準 510(k) 約 US$19,870，差 22 倍。這種判斷必須有官方資料佐證，
  不能靠語言模型推論。
"""

from __future__ import annotations

from pathlib import Path

from .http_client import fda_search_term, get_json
from .schema import (
    ClassificationEntry,
    PredicateDevice,
    RegulatoryResult,
    TfdaRecord,
    TODAY,
)
from . import tfda_index

FDA_CLASSIFICATION_URL = "https://api.fda.gov/device/classification.json"
FDA_510K_URL = "https://api.fda.gov/device/510k.json"

# 各分類對應的送審路徑與費用級距（2022 年度 FDA 公開數據，供研判參考）
_PATH_HINTS = {
    ("1",): ("Class I — 多數僅需登記與一般管制，法規負擔最低", None),
    ("2",): ("Class II — 需 510(k) 前導裝置比對；台灣通常對應第二等級", 19870),
    ("3",): ("Class III — 需 PMA，臨床證據要求最高、費用與時程最重", 441547),
}


def _fda_query(endpoint_url: str, phrase: str, limit: int) -> dict:
    """查 openFDA；完整片語無結果時逐次退掉修飾詞。

    實測：FDA 用官方品名。使用者說 "tracheal tube holder" 查不到，
    要退到 "tube holder" 才命中（官方名為 Endotracheal Tube Holder）。
    """
    words = phrase.replace("-", " ").split()
    candidates = [phrase]
    if len(words) > 1:
        candidates.append(" ".join(words[-2:]))
        candidates.append(words[-1])

    for cand in candidates:
        url = f"{endpoint_url}?search={fda_search_term('device_name', cand)}&limit={limit}"
        try:
            data = get_json(url, allow404=True)
        except Exception:
            continue
        if data.get("results"):
            data["_matched_term"] = cand
            return data
    return {"meta": {"results": {"total": 0}}, "results": [], "_matched_term": None}


def run(device_query: str, tfda_db: Path | None = None,
        tfda_limit: int = 8, tfda_query: str | None = None) -> RegulatoryResult:
    """tfda_query 為中文檢索詞（TFDA 品名是中文，英文檢索詞查不到）。"""
    out = RegulatoryResult(query=device_query, query_date=TODAY)

    # 1. openFDA 裝置分類（最強依據）
    try:
        data = _fda_query(FDA_CLASSIFICATION_URL, device_query, limit=5)
        if data.get("_matched_term"):
            out.matched_terms.append(data["_matched_term"])
        for item in data.get("results", []):
            out.classification.append(ClassificationEntry(
                device_name=item.get("device_name") or "",
                device_class=item.get("device_class") or "",
                regulation_number=item.get("regulation_number") or "",
                specialty=item.get("medical_specialty_description") or "",
                definition=(item.get("definition") or "")[:300],
            ))
    except Exception as exc:
        out.errors.append({"source": "openFDA classification", "error": str(exc)})

    # 2. openFDA 510(k) 前導裝置
    try:
        data = _fda_query(FDA_510K_URL, device_query, limit=8)
        if data.get("_matched_term") and data["_matched_term"] not in out.matched_terms:
            out.matched_terms.append(data["_matched_term"])
        out.predicates_total = data.get("meta", {}).get("results", {}).get("total")
        for item in data.get("results", []):
            out.predicates.append(PredicateDevice(
                k_number=item.get("k_number") or "",
                device_name=(item.get("device_name") or "")[:90],
                applicant=item.get("applicant") or "",
                decision_date=item.get("decision_date") or "",
            ))
    except Exception as exc:
        out.errors.append({"source": "openFDA 510k", "error": str(exc)})

    # 3. 台灣同類已取證（本地索引，用中文檢索詞）
    tq = tfda_query or device_query
    if tfda_db and tfda_db.exists():
        try:
            terms = tq.replace("-", " ").split() or [tq]
            meta = tfda_index.search_progressive(tfda_db, terms, limit=tfda_limit)
            out.tfda_query = tq
            out.tfda_used_terms = " ".join(meta["used_terms"])
            out.tfda_relaxed = meta["relaxed"]
            out.tfda_total = meta["total"]
            for r in meta["rows"]:
                out.tfda_same_class.append(TfdaRecord(
                    license_no=r.get("license_no") or "",
                    class_level=r.get("class_level") or "",
                    name_zh=r.get("name_zh") or "",
                    name_en=r.get("name_en") or "",
                    category=r.get("category") or "",
                    applicant=r.get("applicant") or "",
                    maker_country=r.get("maker_country") or "",
                    valid_date=r.get("valid_date") or "",
                ))
        except Exception as exc:
            out.errors.append({"source": "TFDA local index", "error": str(exc)})

    # 4. 路徑研判
    classes = {c.device_class for c in out.classification if c.device_class}
    key = tuple(sorted(classes))
    if key in _PATH_HINTS:
        hint, fee = _PATH_HINTS[key]
        out.path_hint = hint + (f"（標準審查費約 US${fee:,}，2022 年度）" if fee else "")
    elif classes:
        out.path_hint = (
            f"分類不一致（{sorted(classes)}）— 需逐項確認適應症差異，"
            "不同適應症可能落在不同等級"
        )
    else:
        out.path_hint = "未取得分類資料 — 檢索詞可能與 FDA 官方品名不符，建議手動指定檢索詞"

    out.to_confirm = [
        "以哪一個前導裝置作為實質等同（SE）比對標的",
        "適應症（indication for use）是否落在既有法規編號範圍內",
        "是否需要臨床試驗，或僅需效能測試",
        "TFDA 等級與美國分類是否一致（部分產品台灣更嚴或更寬）",
    ]
    return out
