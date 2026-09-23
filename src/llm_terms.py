"""LLM 檢索詞抽取（第一關：長文 → 結構化檢索詞）。

## 為什麼需要

規則式對照表收不到創新技術詞。實測心衰竭團隊：
  輸入整串中文專案名稱 → 抽出 'wearable device'（或原封不動送中文）
  正確的詞 'ballistocardiography' 完全沒被產出

而 LLM 從同一段文字抽出了：
  device_en: Ballistocardiograph、Breathing frequency monitor
  tech_en:   Ballistocardiography、Heart Rate Variability、
             Respiratory Acoustic Analysis、Sleep Posture Classification
  tfda_zh:   心臟血管監測器、呼吸監測器、睡眠生理監測器

## 關鍵設計原則：LLM 提候選，實測挑最佳

**不可照單全收。** 實測同一批候選拿去檢索，結果天差地遠：

    LLM device  'Ballistocardiograph'          → ✅ 相關性 71%
    LLM device  'Breathing frequency monitor'  → ❌ 相關性  0%
    LLM tech    'Heart Rate Variability'       → ❌ 相關性  0%（過泛）
    LLM tfda    '尿液分析儀'                    → ✅ TFDA 99 筆
    LLM tfda    '失禁警報器'                    → ❌ TFDA  0 筆

LLM 產出的是「可能的詞」，不是「正確的詞」。因此本模組只負責
**提出候選**，實際採用由 relevance.search_verified()（文獻）
與 tfda_index 實測命中數（台灣）決定。

## 紅線（與三條紅線一致）

本模組只做「文字 → 檢索詞」的轉換，不產生任何診斷或治療建議。
抽取失敗時回傳空 dict，**絕不編造**（prompt 明確要求無法判斷就給空陣列）。

## 降級設計

未設定金鑰、API 失敗、超時、回傳格式錯誤 → 一律回傳空 dict，
由上層退回規則式。**不讓 LLM 故障導致整份報告開天窗。**
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request

# 預設服務（可用環境變數或 Streamlit secrets 覆蓋）
DEFAULT_BASE = "https://ollama.com/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"

# 抽取結果的快取（同一段產品說明不重複呼叫，省時省錢）
_CACHE: dict[str, dict] = {}
_CACHE_MAX = 32

SYSTEM_PROMPT = """你是醫療器材法規與專利檢索的術語專家。
從產品說明萃取「拿去資料庫檢索」用的關鍵詞。

只輸出 JSON，不要 markdown 圍欄、不要任何說明文字：
{"device_en":["..."],"condition_en":["..."],"tfda_zh":["..."],"tech_en":["..."]}

欄位定義：
- device_en：FDA 資料庫用的英文裝置品名，1-3 個
- condition_en：PubMed 用的英文疾病或臨床狀態詞，1-3 個
- tfda_zh：台灣 TFDA 醫材許可證慣用的繁體中文品名，1-3 個
- tech_en：這個技術在學術文獻中的正式英文名稱，1-5 個

規則（務必遵守）：
1. device_en 要具體。禁止 wearable device、sensor、monitoring system、
   medical device、health monitoring 這類泛用詞 —— 它們會撈回無關文獻。
2. tech_en 用學術正式名稱（例：ballistocardiography、
   photoplethysmography），不是行銷詞或產品名。
3. tfda_zh 用台灣許可證資料庫實際會出現的品名慣用語
   （例：心臟血管監測器、超音波膀胱掃描儀），不要用產品行銷名稱。
4. **無法從說明判斷的欄位，給空陣列。絕對不要編造或猜測。**
5. 全部使用英文（除 tfda_zh 用繁體中文）。"""


def _config() -> tuple[str, str, str]:
    """回傳 (base_url, api_key, model)。金鑰永不寫入日誌。"""
    key = os.environ.get("OLLAMA_API_KEY", "")
    base = os.environ.get("LLM_BASE_URL", DEFAULT_BASE)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)

    if not key:
        # 嘗試 Streamlit secrets（部署環境）
        try:
            import streamlit as st
            key = st.secrets.get("OLLAMA_API_KEY", "") or key
            base = st.secrets.get("LLM_BASE_URL", base) or base
            model = st.secrets.get("LLM_MODEL", model) or model
        except Exception:
            pass

    if not key:
        # 嘗試讀 ~/.hermes/.env（本機開發）
        try:
            from pathlib import Path
            envp = Path.home() / ".hermes" / ".env"
            if envp.exists():
                for line in envp.read_text(encoding="utf-8").splitlines():
                    if line.startswith("OLLAMA_API_KEY"):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except Exception:
            pass
    return base, key, model


def available() -> bool:
    """是否可用（有金鑰）。供介面顯示狀態。"""
    return bool(_config()[1])


def _cache_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()[:32]


def _post(base: str, key: str, model: str, product: str,
          timeout: int) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": product[:6000]}],
        "temperature": 0.1,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def _parse(raw: str) -> dict:
    """解析 LLM 回應。嚴格驗證欄位型別，不符則回空 dict。"""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}

    out: dict = {}
    for k in ("device_en", "condition_en", "tfda_zh", "tech_en"):
        v = d.get(k)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            v = []
        # 清理：去空白、去空字串、去重、長度限制
        seen: set[str] = set()
        clean: list[str] = []
        for x in v:
            if not isinstance(x, str):
                continue
            x = re.sub(r"\s+", " ", x).strip(" ,;.")
            if len(x) < 3 or len(x) > 80:
                continue
            low = x.lower()
            if low in seen:
                continue
            seen.add(low)
            clean.append(x)
        out[k] = clean[:6]
    return out


def extract_terms(product: str, timeout: int = 90,
                  use_cache: bool = True) -> dict:
    """從產品說明抽取結構化檢索詞。

    回傳 {"device_en":[...], "condition_en":[...],
          "tfda_zh":[...], "tech_en":[...], "_model":..., "_ms":...}
    失敗時回傳 {}（呼叫端據此退回規則式）。

    **呼叫端必須驗證候選詞**，不可直接採用 ——
    實測 LLM 候選的檢索品質落差極大（71% vs 0%）。
    """
    text = (product or "").strip()
    if len(text) < 10:
        return {}

    base, key, model = _config()
    if not key:
        return {}

    ck = _cache_key(text, model)
    if use_cache and ck in _CACHE:
        cached = dict(_CACHE[ck])
        cached["_cached"] = True
        return cached

    t0 = time.time()
    try:
        raw = _post(base, key, model, text, timeout)
    except Exception:
        return {}

    parsed = _parse(raw)
    if not parsed:
        return {}

    parsed["_model"] = model
    parsed["_ms"] = int((time.time() - t0) * 1000)
    parsed["_cached"] = False

    if use_cache:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[ck] = dict(parsed)
    return parsed


def summarize(terms: dict) -> str:
    """人類可讀的一行摘要（供介面顯示）。"""
    if not terms:
        return "未取得 LLM 建議"
    parts = []
    for k, label in (("device_en", "裝置"), ("tech_en", "技術"),
                     ("condition_en", "疾病"), ("tfda_zh", "中文品名")):
        v = terms.get(k) or []
        if v:
            parts.append(f"{label}：{'、'.join(v[:3])}")
    return "；".join(parts) if parts else "未取得 LLM 建議"
