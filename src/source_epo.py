"""EPO Open Patent Services（OPS）整合。

為什麼要接這個：
FreePatentsOnline 只能給「專利號 + 標題」，**沒有申請人（assignee）**。
這使得「誰在這個領域佈局最密」這個關鍵問題無法回答。
EPO OPS 補上這塊，並額外提供：

- 申請人／發明人（assignee）→ 專利佈局分析
- 法律狀態（INPADOC legal events）→ 專利是否仍有效、何時到期
- 專利家族（DOCDB family）→ 同一技術在多少國家佈局（判斷保護強度）
- 引用資料 → 技術影響力

實測（2026-09-22）：
  認證端點 https://ops.epo.org/3.2/auth/accesstoken 運作中
  （無效憑證回 401 "ClientId is Invalid"）
  未帶 token 查詢回 403，確認需認證

申請方式（免費）：
  1. 到 https://developers.epo.org/ 註冊帳號
  2. 登入後到 APIs → 建立 App，取得 Consumer Key / Consumer Secret
  3. 設定環境變數 EPO_OPS_KEY / EPO_OPS_SECRET
     或在 Streamlit secrets 設定同名項目

配額：非付費使用者每週 4 GB。本工具的查詢量遠低於此上限。

重要：未設定憑證時，模組會優雅降級（回傳空結果並註明），
不會讓整個報告失敗。
"""

from __future__ import annotations

import base64
import os
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

OPS_AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
OPS_BASE = "https://ops.epo.org/3.2/rest-services"

UA = "MedtechDevAssistant/1.0 (university incubation center; research)"

# token 快取（EPO token 有效期 20 分鐘，這裡保守用 15 分鐘）
_TOKEN_CACHE: dict[str, object] = {"token": None, "expires": 0.0}


def _credentials() -> tuple[str, str] | None:
    """取得 EPO OPS 憑證。依序嘗試 Streamlit secrets → 環境變數。"""
    key = secret = None
    try:
        import streamlit as st
        key = st.secrets.get("EPO_OPS_KEY")
        secret = st.secrets.get("EPO_OPS_SECRET")
    except Exception:
        pass
    key = key or os.environ.get("EPO_OPS_KEY")
    secret = secret or os.environ.get("EPO_OPS_SECRET")
    if key and secret:
        return str(key), str(secret)
    return None


def is_configured() -> bool:
    """是否已設定憑證（供介面顯示狀態）。"""
    return _credentials() is not None


def get_token(force: bool = False) -> str | None:
    """取得 OAuth access token（含快取）。

    EPO 用 OAuth2 client_credentials，需 Basic auth 帶 key:secret。
    token 有效期 20 分鐘，快取 15 分鐘避免頻繁認證。
    """
    now = time.time()
    if not force and _TOKEN_CACHE["token"] and now < float(_TOKEN_CACHE["expires"]):
        return str(_TOKEN_CACHE["token"])

    creds = _credentials()
    if not creds:
        return None

    key, secret = creds
    auth = base64.b64encode(f"{key}:{secret}".encode()).decode()
    body = b"grant_type=client_credentials"
    req = Request(
        OPS_AUTH_URL, data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": UA,
        })
    try:
        with urlopen(req, timeout=30) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8", "ignore"))
            token = data.get("access_token")
            if token:
                _TOKEN_CACHE["token"] = token
                _TOKEN_CACHE["expires"] = now + 900  # 15 分鐘
                return str(token)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"EPO 認證失敗 HTTP {exc.code}：{detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"EPO 認證失敗：{exc}") from exc
    return None


def _get(url: str, accept: str = "application/json") -> dict | str | None:
    """帶 token 呼叫 OPS 端點。"""
    token = get_token()
    if not token:
        return None
    req = Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "User-Agent": UA,
    })
    try:
        with urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            if "json" in accept:
                import json
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
            return raw
    except HTTPError as exc:
        if exc.code == 404:
            return None          # 無結果
        if exc.code in (401, 403):
            # token 可能過期，強制重取一次
            token = get_token(force=True)
            if not token:
                return None
            req2 = Request(url, headers={
                "Authorization": f"Bearer {token}",
                "Accept": accept, "User-Agent": UA})
            try:
                with urlopen(req2, timeout=45) as resp:
                    raw = resp.read().decode("utf-8", "ignore")
                    import json
                    try:
                        return json.loads(raw)
                    except Exception:
                        return raw
            except Exception:
                return None
        raise RuntimeError(f"OPS HTTP {exc.code}")


def _cq(term: str) -> str:
    """組 OPS CQL 查詢（片語需引號）。"""
    return f'ti%3D%22{quote(term)}%22'


def search(term: str, limit: int = 25) -> dict:
    """檢索專利，回傳含申請人的結果。

    這是 FPO 做不到的部分 —— FPO 只給號碼與標題，沒有 assignee。
    """
    if not is_configured():
        return {"configured": False, "results": [], "total": None}

    url = (f"{OPS_BASE}/published-data/search?"
           f"q={_cq(term)}&Range=1-{limit}")
    try:
        data = _get(url)
    except Exception as exc:
        return {"configured": True, "results": [], "total": None,
                "error": str(exc)[:150]}

    if not data or not isinstance(data, dict):
        return {"configured": True, "results": [], "total": None}

    ops = data.get("ops:world-patent-data", {})
    meta = ops.get("ops:meta", {})
    total = meta.get("@total-results")
    docs = (ops.get("exchange-documents", {}) or {}).get("exchange-document", [])
    if isinstance(docs, dict):
        docs = [docs]

    out: list[dict] = []
    for d in docs[:limit]:
        bib = (d.get("bibliographic-data", {}) or {})
        # 標題（優先英文）
        title = ""
        ti = bib.get("invention-title", [])
        if isinstance(ti, dict):
            ti = [ti]
        for t in ti:
            if isinstance(t, dict) and t.get("@lang") == "en":
                title = (t.get("$") or "").strip()
                break
        if not title and ti:
            first = ti[0]
            title = ((first.get("$") if isinstance(first, dict) else "") or "").strip()

        # 申請人（assignee）— 這是接 EPO 的主要目的
        assignees: list[str] = []
        parties = bib.get("parties", {}) or {}
        apps = ((parties.get("applicants", {}) or {}).get("applicant", []))
        if isinstance(apps, dict):
            apps = [apps]
        for a in apps:
            if not isinstance(a, dict):
                continue
            nm = ((a.get("applicant-name", {}) or {}).get("name", {}))
            if isinstance(nm, dict):
                val = (nm.get("$") or "").strip()
                if val:
                    assignees.append(val)

        # 公開號與日期
        pub = ((bib.get("publication-reference", {}) or {})
               .get("document-id", []))
        if isinstance(pub, dict):
            pub = [pub]
        pub_num = pub_date = ""
        for p in pub:
            if isinstance(p, dict):
                pub_num = (p.get("doc-number", {}) or {}).get("$", "") or pub_num
                pub_date = (p.get("date", {}) or {}).get("$", "") or pub_date

        # 法律狀態（供判斷是否仍有效）
        legal = ((bib.get("legal-status") or {}))
        status = ""
        if isinstance(legal, dict):
            status = (legal.get("$") or "")

        out.append({
            "publication": pub_num or d.get("@doc-number", ""),
            "title": title[:110],
            "assignees": assignees[:3],
            "date": pub_date,
            "status": status,
        })

    return {"configured": True, "results": out,
            "total": int(total) if total else None}


def assignee_landscape(term: str, limit: int = 100) -> list[dict]:
    """專利佈局者排行 —— 誰在這個領域申請最多專利。

    這是 FPO 完全做不到的分析（FPO 無 assignee 欄位）。
    """
    r = search(term, limit=limit)
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for x in r.get("results") or []:
        for a in x.get("assignees") or []:
            key = a.strip()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            s = samples.setdefault(key, [])
            if len(s) < 2 and x.get("title"):
                s.append(x["title"][:70])
    return [
        {"assignee": k, "count": v, "samples": samples.get(k, [])}
        for k, v in sorted(counts.items(), key=lambda x: -x[1])
    ]


def status() -> dict:
    """回傳 EPO 連線狀態（供介面顯示）。"""
    if not is_configured():
        return {
            "configured": False,
            "message": "未設定 EPO 憑證（缺少 EPO_OPS_KEY / EPO_OPS_SECRET）",
        }
    try:
        get_token()
        return {"configured": True, "message": "EPO OPS 已連線"}
    except Exception as exc:
        return {"configured": False, "message": str(exc)[:150]}
