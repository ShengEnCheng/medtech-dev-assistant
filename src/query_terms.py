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

# 技術詞：問的是「這個技術在學術上的正式名稱」（文獻／專利檢索用）。
#
# 為什麼要跟裝置品名分開：
#   裝置品名問「市面上有什麼產品」（FDA 分類、TFDA 取證）；
#   技術詞問「這個技術被研究過什麼」（PubMed、專利）。
#   實測心震圖產品的教訓：用 "wearable device" 查論文撈回
#   「穿戴裝置與飲食習慣」「藍牙穿戴裝置通訊」等完全無關的文獻；
#   改用 "ballistocardiography" 立刻撈到枕頭式 BCG 的 J 波偵測論文
#   —— 那才是真正可能構成新穎性障礙的先前技術。
#
# 這張表刻意保持精簡：只收「無歧義且在文獻中穩定使用」的技術名詞。
# 其餘一律靠 extract_bilingual_pairs() 從團隊自己的文件抓，
# 或由 LLM 建議 + 人工確認。表越大越容易誤判。
_TECH_HINTS: dict[str, str] = {
    "心震圖": "ballistocardiography",
    "彈道心震圖": "ballistocardiography",
    "心音圖": "phonocardiography",
    "光容積": "photoplethysmography",
    "容積脈波": "photoplethysmography",
    "生物阻抗": "bioimpedance",
    "阻抗量測": "bioimpedance",
    "肌電圖": "electromyography",
    "腦電圖": "electroencephalography",
    "都卜勒": "doppler ultrasound",
    "熱像": "thermal imaging",
    "氣體感測": "gas sensor",
    "揮發性有機": "volatile organic compounds",
    "加速度計": "accelerometer",
    "慣性測量": "inertial measurement unit",
    "無線感測": "wireless sensor network",
    "機器學習": "machine learning",
    "深度學習": "deep learning",
    "訊號解耦": "signal decomposition",
    "心率變異": "heart rate variability",
    "睡眠分期": "sleep staging",
    "連續監測": "continuous monitoring",
    "非侵入": "non-invasive",
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

# 學術文件的中英對照格式：「中文 (English, ABBR)」或「中文（English）」
# 台灣計畫書與論文慣用此格式，等於團隊自己標好了正確的英文專有名詞。
# 實測心衰竭團隊的計畫書：心衰竭 → Heart Failure (HF)、
# 彈道心震圖 → Ballistocardiography (BCG)，這是品質最高的檢索詞來源。
_BILINGUAL_PATTERNS = [
    # 中文 (English, ABBR)
    re.compile(
        r"([\u4e00-\u9fff][\u4e00-\u9fff\w]{0,14})"
        r"\s*[（(]\s*"
        r"([A-Za-z][A-Za-z0-9\s\-''/]{3,60}?)"
        r"\s*[,，]\s*([A-Za-z][A-Za-z0-9\-]{1,12})"
        r"\s*[)）]"),
    # 中文 (English)
    re.compile(
        r"([\u4e00-\u9fff][\u4e00-\u9fff\w]{0,14})"
        r"\s*[（(]\s*"
        r"([A-Za-z][A-Za-z0-9\s\-''/]{3,60}?)"
        r"\s*[)）]"),
]


def extract_bilingual_pairs(text: str) -> list[dict]:
    """抽取文件中的「中文 (English, 縮寫)」對照。

    為什麼這是最有價值的檢索詞來源：
    團隊寫計畫書時會自己標上英文專有名詞，這個英文名比任何對照表
    或 LLM 猜測都準確 —— 那是他們領域的正式術語。

    實測心衰竭團隊：
      心衰竭 → Heart Failure (HF)
      彈道心震圖 → Ballistocardiography (BCG)

    回傳 [{"zh":..., "en":..., "abbr":..., "en_clean":...}, ...]
    並為長詞額外註冊子詞（「彈道心震圖」→ 也登記「心震圖」），
    讓專案名稱（居家被動式心震圖…）也能對上。
    """
    text = text or ""
    out: list[dict] = []
    seen: set[str] = set()

    for pat in _BILINGUAL_PATTERNS:
        for m in pat.finditer(text):
            zh, en = m.group(1).strip(), m.group(2).strip()
            abbr = (m.group(3).strip() if m.lastindex and m.lastindex >= 3
                    else "")
            # 修正常見的抽取雜訊
            en = re.sub(r"\s+", " ", en).strip(" ,;")

            # 中文詞常連帶前面的修飾語被一起抓進來，例如
            # 「同步量測頸動脈傳導之彈道心震圖」。
            # 在連接詞後切斷，只留核心名詞（「彈道心震圖」）。
            for sep in ("之", "的", "與", "及", "和", "或", "，", "、", "。"):
                if sep in zh:
                    zh = zh.split(sep)[-1]
            zh = zh.strip()
            if len(zh) < 2 or len(en) < 4:
                continue
            key = (zh, en.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "zh": zh,
                "en": en,
                "abbr": abbr,
                "en_clean": en.lower(),
                "derived": False,
            })
            # 為長中文詞註冊子詞：「彈道心震圖」→「心震圖」
            for cut in range(1, len(zh) - 1):
                sub = zh[cut:]
                if len(sub) >= 3 and (sub, en.lower()) not in seen:
                    seen.add((sub, en.lower()))
                    out.append({"zh": sub, "en": en, "abbr": abbr,
                                "en_clean": en.lower(), "derived": True})

    # 去重鍵用 (中文, 英文) 而非只用英文。
    #
    # 為什麼不能只用英文去重：衍生子詞（「彈道心震圖」→「心震圖」）
    # 與原詞英文相同，若按英文去重會被誤刪，導致專案名稱
    # （「居家被動式心震圖監測…」）對不上任何檢索詞。
    out.sort(key=lambda x: (-len(x["zh"]), x.get("derived", False)))
    uniq: list[dict] = []
    seen_pair: set[tuple[str, str]] = set()
    for it in out:
        key = (it["zh"], it["en_clean"])
        if key in seen_pair:
            continue
        seen_pair.add(key)
        uniq.append(it)
    return uniq


def is_condition_zh(zh: str) -> bool:
    """判斷中文詞是否為疾病／臨床狀態（應歸到疾病檢索，不是裝置檢索）。"""
    return any(k in zh or zh in k for k in _CONDITION_HINTS)


def lookup_bilingual(text: str, zh_key: str) -> str:
    """用中文詞查文件中的英文對應（供專案名稱對照內文使用）。"""
    for it in extract_bilingual_pairs(text):
        if it["zh"] == zh_key:
            return it["en"]
    return ""


def _score_term(term: str) -> int:
    """檢索詞品質分數（越高越好）。供選擇主要裝置檢索詞用。

    為什麼需要評分而不是「取最長」：
    舊作法取最長的匹配詞，但長度不等於品質。
    "incontinence-associated dermatitis" 很長卻很精準；
    "wearable device" 不長但極泛用。
    這裡同時考量：具體度（詞數）、是否泛用、是否含技術詞。
    """
    if not term:
        return -1
    score = 0
    words = [w for w in term.split() if len(w) > 2]
    score += min(len(words), 4) * 2          # 詞數多 = 較具體
    if is_generic(term):
        score -= 8                            # 泛用詞重罰
    if any(g in term.lower() for g in ("device", "system", "apparatus")
           ) and len(words) <= 2:
        score -= 3                            # 泛用結尾詞
    if len(term) > 60:
        score -= 4                            # 過長可能是整句
    if not term.isascii():
        score -= 20                           # 中文不該出現在英文檢索詞
    return score


def derive_query_plan(description: str,
                      override_device: str | None = None,
                      override_tfda: str | None = None,
                      override_condition: str | None = None) -> dict:
    """完整的檢索詞計畫（多管道分離）。

    為什麼要一次產出「計畫」而不是單一檢索詞：
    一個產品需要三種不同性質的檢索詞，餵給三種不同的資料庫：

      device    裝置品名     → FDA 分類、TFDA 取證
      condition 疾病狀態     → PubMed 疾病負擔
      tech      技術關鍵詞   → PubMed／專利（先前技術的核心）

    實測心震圖產品的教訓：只給一個裝置檢索詞時，抓成
    "wearable device"，論文模組撈回「穿戴裝置與飲食習慣」
    「藍牙穿戴裝置通訊」等完全無關的文獻。正確的技術詞
    "ballistocardiography" 卻完全沒被產出 —— 而它才是
    真正能撈到枕頭式 BCG 先前技術的詞。

    優先序（信心由高到低）：
      A. 文件中的中英對照（團隊自己標的，最準）
      B. 手動指定
      C. 對照表（含技術詞表）
      D. fallback（**只產出中文，不當英文送出**）

    回傳 dict，內含 device/tfda/condition/tech 四組與來源標註。
    """
    text = description or ""
    plan: dict = {
        "device": "", "tfda": "", "condition": "", "tech": [],
        "source": {}, "warnings": [], "fallback_used": False,
        "bilingual": [],
    }

    pairs = extract_bilingual_pairs(text)
    plan["bilingual"] = [{"zh": p["zh"], "en": p["en"], "abbr": p["abbr"]}
                         for p in pairs if not p.get("derived")]

    # ---------- 疾病檢索詞 ----------
    if override_condition:
        plan["condition"] = override_condition.strip()
        plan["source"]["condition"] = "手動指定"
    else:
        cond_hit = ""
        for key in sorted(_CONDITION_HINTS, key=len, reverse=True):
            if key in text:
                cond_hit = _CONDITION_HINTS[key]
                break
        # 文件中的中英對照優先（團隊標的更準確）
        for p in pairs:
            if is_condition_zh(p["zh"]):
                cond_hit = p["en"]
                plan["source"]["condition"] = "文件中英對照"
                break
        if cond_hit:
            plan["condition"] = cond_hit
            plan["source"].setdefault("condition", "對照表")

    # ---------- 技術檢索詞 ----------
    tech: list[str] = []
    for key in sorted(_TECH_HINTS, key=len, reverse=True):
        if key in text:
            tech.append(_TECH_HINTS[key])
    # 文件中的中英對照：非疾病者視為技術詞
    for p in pairs:
        if not is_condition_zh(p["zh"]) and p["en"]:
            tech.insert(0, p["en"])
            plan["source"]["tech"] = "文件中英對照"
    # 去重保序
    seen_t: set[str] = set()
    plan["tech"] = [t for t in tech
                    if t and not (t.lower() in seen_t
                                  or seen_t.add(t.lower()))][:6]

    # ---------- TFDA 中文檢索詞 ----------
    if override_tfda:
        plan["tfda"] = override_tfda.strip()
        plan["source"]["tfda"] = "手動指定"
    else:
        plan["tfda"] = derive_tfda_query(text)
        if plan["tfda"]:
            plan["source"]["tfda"] = "對照表"

    # ---------- 裝置檢索詞（英文）----------
    if override_device:
        plan["device"] = override_device.strip()
        plan["source"]["device"] = "手動指定"
    else:
        cands: list[tuple[str, str]] = []
        for k, v in _SPECIFIC_HINTS.items():
            if k in text:
                cands.append((v, "對照表（具體）"))
        for p in pairs:
            if not is_condition_zh(p["zh"]) and p["en"]:
                # 技術詞可兼作裝置詞，但優先度低於具體品名
                cands.append((p["en"], "文件中英對照"))
        for k, v in _GENERIC_HINTS.items():
            if k in text:
                cands.append((v, "對照表（泛用）"))

        if cands:
            best = max(cands, key=lambda x: _score_term(x[0]))
            plan["device"] = best[0]
            plan["source"]["device"] = best[1]
            if is_generic(best[0]):
                plan["warnings"].append(
                    f"裝置檢索詞「{best[0]}」為泛用詞，結果可能不精確，"
                    "建議手動指定 FDA 官方品名")
        else:
            # fallback：不再把中文原文當英文送出。
            # 實測教訓：中文原文送進英文資料庫會撈回
            # 「心力衰竭患者居家护理模式」這種無關結果，
            # 且報告看起來正常，錯誤被掩蓋。寧可留空讓呼叫端提示。
            plan["fallback_used"] = True
            plan["device"] = ""
            plan["warnings"].append(
                "未能自動判定英文裝置檢索詞，請手動指定 FDA 官方品名"
                "（此步驟會影響專利與競品結果的正確性）")

    # 中文裝置詞但無對照時，若 tech 有值可作為英文檢索退路
    if not plan["device"] and plan["tech"]:
        plan["device"] = plan["tech"][0]
        plan["source"]["device"] = "技術詞（退路）"
        plan["warnings"].append(
            f"以技術詞「{plan['tech'][0]}」作為裝置檢索詞，"
            "若需 FDA 官方品名請手動指定")

    return plan


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
