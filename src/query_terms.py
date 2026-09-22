"""產品說明 → 裝置檢索詞抽取。

為什麼需要這一步：
使用者寫的是中文產品說明（「一款智慧型氣管內管固定裝置…」），
但外部資料庫查的是英文官方品名（Endotracheal Tube Holder）。
中間需要一層對映，且必須防止抽出過度泛用的詞。

實測教訓：
最初把「失禁性皮膚炎監測貼片」抽成 "sensor"，
結果 TFDA 回 104 件取證、法規分類橫跨 Class 1~3，完全沒有參考價值。
"""

from __future__ import annotations

import re

# 具體詞優先（key 越長越具體）。這些詞有明確對應的器材官方品名。
_SPECIFIC_HINTS: dict[str, str] = {
    "失禁性皮膚炎": "incontinence-associated dermatitis",
    "氣管內管": "endotracheal tube holder",
    "膀胱餘尿": "bladder scanner ultrasound",
    "糖尿病足": "diabetic foot ulcer",
    "糖尿病視網膜": "diabetic retinopathy",
    "餘尿": "bladder scanner ultrasound",
    "導尿管": "urinary catheter",
    "壓瘡": "pressure ulcer prevention",
    "褥瘡": "pressure ulcer prevention",
    "骨密度": "bone densitometer",
    "心電圖": "electrocardiograph",
    "呼吸器": "ventilator",
    "內視鏡": "endoscope",
    "超音波": "ultrasound probe",
    "洗腎": "hemodialysis",
    "透析": "dialysis",
    "腦波": "electroencephalograph",
    "血氧": "pulse oximeter",
    "血糖": "blood glucose meter",
    "輸液": "infusion pump",
    "注射": "infusion pump",
    "縫合": "suture",
    "傷口": "wound dressing",
    "支架": "stent",
    "尿管": "urinary catheter",
    "導管": "catheter",
    "尿布": "incontinence pad",
    "復健": "rehabilitation device",
    "輔具": "assistive device",
    "手術": "surgical instrument",
    "翻身": "patient repositioning",
}

# 泛用詞：只有在沒有具體詞匹配時才用，且報告需提示人工確認。
_GENERIC_HINTS: dict[str, str] = {
    "感測": "sensor",
    "穿戴": "wearable device",
    "貼片": "patch",
}

# TFDA 用中文檢索詞（TFDA 資料集品名為中文，英文檢索詞永遠對不上）
_TFDA_HINTS: dict[str, list[str]] = {
    "失禁性皮膚炎": ["失禁", "看護墊", "皮膚炎"],
    "氣管內管": ["氣管內管", "支氣管內管"],
    "膀胱餘尿": ["膀胱", "餘尿", "超音波"],
    "糖尿病足": ["糖尿病", "足", "傷口"],
    "導尿管": ["導尿管", "尿管", "導尿"],
    "尿管": ["導尿管", "尿管"],
    "壓瘡": ["壓瘡", "褥瘡", "減壓"],
    "褥瘡": ["褥瘡", "壓瘡"],
    "尿布": ["失禁", "看護墊"],
    "翻身": ["翻身", "翻身床", "減壓"],
    "血氧": ["血氧", "血氧濃度"],
    "血糖": ["血糖", "葡萄糖"],
    "輸液": ["輸液", "點滴", "注射幫浦"],
    "注射": ["注射", "輸液"],
    "導管": ["導管", "catheter"],
    "傷口": ["傷口", "敷料"],
    "支架": ["支架", "血管支架"],
    "內視鏡": ["內視鏡", "內視鏡"],
    "超音波": ["超音波", "超音波掃描"],
    "心電圖": ["心電圖"],
    "呼吸器": ["呼吸器", "呼吸機"],
    "洗腎": ["洗腎", "血液透析"],
    "透析": ["透析", "血液透析"],
    "縫合": ["縫合"],
    "復健": ["復健", "訓練器"],
    "手術": ["手術", "器械"],
    "感測": [],
    "穿戴": [],
    "貼片": ["貼片", "貼布"],
}

_EN_PATTERN = re.compile(r"[A-Za-z][A-Za-z\- ]{4,40}")


def derive_device_query(description: str, override: str | None = None) -> str:
    """從產品說明抽取「英文」裝置檢索詞（用於 FDA / 專利檢索）。

    策略（依序）：
    1. 若使用者手動指定，直接採用
    2. 具體詞優先，越長（越具體）優先
    3. 只有泛用詞時才用泛用詞（報告會標示需人工確認）
    4. 都沒有時，抓英文片段或截斷原文
    """
    if override:
        return override.strip()

    text = description or ""
    specific = [v for k, v in _SPECIFIC_HINTS.items() if k in text]
    if specific:
        return max(specific, key=len)

    generic = [v for k, v in _GENERIC_HINTS.items() if k in text]
    if generic:
        return max(generic, key=len)

    en = _EN_PATTERN.findall(text)
    if en:
        return en[0].strip()

    return text[:40]


def derive_tfda_query(description: str) -> str:
    """從產品說明抽取「中文」檢索詞（用於 TFDA 本地索引）。

    為什麼必須分開：
    TFDA 許可證資料集的品名是中文（「維立」氣管內管），
    用英文檢索詞（endotracheal tube holder）永遠對不上，
    嚴格模式會篩成空集合。實測同一產品：
      英文檢索 → 命中 0 筆（只能靠寬鬆模式撈到不相關的 X 光球管）
      中文「氣管內管」→ 命中 59 筆，全部是氣管內管相關產品

    為什麼只取「最優先的一組」而不是全部匹配的關鍵詞：
    產品說明是完整句子，會命中多個不相干的關鍵詞。
    實測「智慧型氣管內管固定裝置…病人翻身時…」同時命中
    「氣管內管」與「翻身」，若全部帶入檢索，
    「漢翔動力式病人翻身床」會混進氣管內管的競品清單。
    因此按 key 長度降序（越具體的詞越長）取第一個匹配的關鍵詞組。

    回傳以空白分隔的中文關鍵詞；若抽不到，回傳空字串
    （呼叫端會改用英文檢索詞或醫器主類別檢索作為退路）。
    """
    return " ".join(derive_tfda_terms(description))


def derive_tfda_terms(description: str) -> list[str]:
    """回傳「有序」的中文檢索詞組（最具體優先），供漸進式檢索使用。

    順序即具體程度：先試最具體的詞，達標就停止累積後續詞。
    例：膀胱餘尿偵測儀 → ["膀胱", "餘尿", "超音波"]
        「膀胱」單獨 47 筆就達標，不會被「超音波」汙染成 617 筆。
    """
    text = description or ""
    for key in sorted(_TFDA_HINTS, key=len, reverse=True):
        if key in text:
            terms = [t for t in _TFDA_HINTS[key] if t]
            seen: set[str] = set()
            out: list[str] = []
            for t in terms:
                if t not in seen:
                    seen.add(t)
                    out.append(t)
            if out:
                return out
    return []


def is_generic(query: str) -> bool:
    """判斷檢索詞是否為泛用詞（報告需加提示）。"""
    return query in set(_GENERIC_HINTS.values())


# 疾病／臨床狀態對照（供 PubMed 疾病負擔檢索用）
# 為什麼需要獨立一組：裝置檢索詞（endotracheal tube holder）查的是「產品」，
# 疾病負擔查的是「臨床狀態」（urinary retention）。兩者是完全不同的概念，
# 用裝置詞去 PubMed 查盛行率會撈到不相關文獻。
_CONDITION_HINTS: dict[str, str] = {
    "失禁性皮膚炎": "incontinence-associated dermatitis",
    "膀胱餘尿": "urinary retention",
    "餘尿": "urinary retention",
    "泌尿道感染": "catheter-associated urinary tract infection",
    "尿路感染": "catheter-associated urinary tract infection",
    "導尿管": "catheter-associated urinary tract infection",
    "尿管": "urinary catheterization",
    "壓瘡": "pressure ulcer",
    "褥瘡": "pressure ulcer",
    "糖尿病足": "diabetic foot ulcer",
    "糖尿病視網膜": "diabetic retinopathy",
    "失禁": "urinary incontinence",
    "尿布": "urinary incontinence",
    "氣管內管": "endotracheal intubation",
    "呼吸器": "mechanical ventilation",
    "洗腎": "hemodialysis",
    "透析": "dialysis",
    "心衰竭": "heart failure",
    "中風": "stroke",
    "骨折": "fracture",
    "失智": "dementia",
    "憂鬱": "depression",
    "糖尿病": "diabetes mellitus",
    "高血壓": "hypertension",
    "慢性腎": "chronic kidney disease",
    "癌症": "cancer",
    "腫瘤": "neoplasm",
    "感染": "infection",
    "傷口": "wound",
    "疼痛": "pain",
    "跌倒": "falls",
}


def derive_condition(description: str, override: str | None = None) -> str:
    """從產品說明抽取「疾病／臨床狀態」檢索詞（供 PubMed 疾病負擔用）。

    為什麼要跟裝置檢索詞分開：
    裝置詞問「這個產品市面上有什麼」（FDA/專利/TFDA），
    疾病詞問「這個病有多嚴重」（PubMed）。
    實測用裝置詞 "endotracheal tube holder" 去查盛行率會撈到無關文獻。

    優先取最具體的詞（key 越長越具體），避免「感染」這種泛用詞
    蓋掉「泌尿道感染」。
    """
    if override:
        return override.strip()
    text = description or ""
    for key in sorted(_CONDITION_HINTS, key=len, reverse=True):
        if key in text:
            return _CONDITION_HINTS[key]
    return ""


def expand_patent_terms(device_query: str) -> list[str]:
    """專利檢索詞擴充。

    依 BMJ Health Care Inform 2024 那篇 review 的做法：
    把技術描述展開成同義詞、上位詞，以提高檢索覆蓋率。
    """
    synonyms = {
        "securement": ["fixation", "holder", "stabilization", "retention"],
        "endotracheal": ["tracheal", "intubation", "ET tube"],
        "catheter": ["cannula", "line", "tube"],
        "monitor": ["sensing", "detection", "measurement"],
        "wearable": ["body-worn", "ambulatory", "portable"],
        "sensor": ["detector", "transducer", "probe"],
        "diagnostic": ["detection", "assessment", "screening"],
        "rehabilitation": ["rehab", "therapy", "training"],
        "urinary": ["bladder", "urogenital"],
        "holder": ["securement", "fixation", "retainer"],
        "ultrasound": ["sonography", "sonographic"],
        "dermatitis": ["skin breakdown", "skin damage"],
    }
    base = [t for t in device_query.replace("-", " ").split() if len(t) > 2]
    terms: list[str] = [device_query] + base
    for t in base:
        low = t.lower()
        for key, syns in synonyms.items():
            if key in low or low in key:
                terms += syns[:3]

    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        tl = t.lower().strip()
        if tl and tl not in seen:
            seen.add(tl)
            out.append(t)
    return out[:12]
