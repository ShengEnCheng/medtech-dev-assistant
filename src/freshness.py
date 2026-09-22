"""資料新鮮度檢查與自動更新。

為什麼需要這個模組：
本工具的資料來源有兩種截然不同的更新行為，若不區分，團隊會誤用過期資料：

  A. **即時查詢型**（每次都用最新）
     openFDA 四個端點、PubMed、FreePatentsOnline、健保署
     → 每次執行都打官方 API，官方資料庫更新就反映

  B. **快取下載型**（不會自動更新，需重建）
     TFDA 醫材許可證資料集、UN Comtrade
     → 本地索引／年度資料，需手動或排程更新

實測（2026-09-22）各來源的實際資料日期：

| 來源 | 類型 | 實測資料日期 | 落後 |
|------|------|------------|------|
| openFDA UDI | 即時 | 2026-09-02 | 20 天 |
| openFDA 510(k) | 即時 | 2026-09-14 | 8 天 |
| openFDA PMA | 即時 | 2026-09-14 | 8 天 |
| openFDA 召回 | 即時 | 2026-09-16 | 6 天 |
| TFDA 許可證 | 快取 | 2026-09-17（來源檔內日期） | 5 天 |
| UN Comtrade | 快取 | 2022 年 | 約 3-4 年 |
| PubMed / FPO | 即時 | — | 即時 |

注意：即使是「即時查詢型」，官方本身也有更新週期
（開放資料多為每月或每週），所以看到 6-20 天的落差是正常的，
不是工具的問題。

TFDA 的更新偵測方式：
官方 ZIP 檔內含的檔案帶有時間戳（實測 68_2.csv 為 2026-09-17 15:22:32），
下載 ZIP 後讀取該時間戳，與本地索引記錄的來源日期比對，
即可判斷是否需要重建。這比比對檔案大小或雜湊更可靠。
"""

from __future__ import annotations

import datetime
import io
import json
import ssl
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UA = "MedtechDevAssistant/1.0 (university incubation; research)"

# 即時查詢型的來源（每次執行都用最新，不需更新動作）
LIVE_SOURCES = {
    "openFDA UDI": "全球已上市器材",
    "openFDA 510(k)": "前導裝置",
    "openFDA PMA": "美國高風險器材",
    "openFDA 召回": "競品品質風險",
    "openFDA 製造廠登記": "全球製造地",
    "PubMed": "疾病負擔文獻",
    "FreePatentsOnline": "專利檢索",
}

# 快取型的來源（需更新）
CACHED_SOURCES = {
    "TFDA 許可證資料集": "台灣已取證廠商",
    "UN Comtrade": "各國醫材貿易額",
    "WHO GHO": "全球疾病指標",
    "World Bank": "各國醫療支出",
}

SOURCE_LINKS = {
    "TFDA 醫材許可證資料集": "https://data.gov.tw/dataset/9576",
    "openFDA": "https://open.fda.gov/apis/device/",
    "PubMed": "https://pubmed.ncbi.nlm.nih.gov/",
    "FreePatentsOnline": "https://www.freepatentsonline.com/",
    "UN Comtrade": "https://comtradeplus.un.org/",
    "健保署": "https://www.nhi.gov.tw/",
}


def _get_json(url: str, timeout: int = 30):
    ctx = ssl.create_default_context()
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


# ---------------------------------------------------------------- openFDA

OPENFDA_ENDPOINTS = {
    "openFDA UDI": "udi",
    "openFDA 510(k)": "510k",
    "openFDA PMA": "pma",
    "openFDA 召回": "enforcement",
    "openFDA 製造廠登記": "registrationlisting",
}


def openfda_freshness() -> list[dict]:
    """查 openFDA 各端點的官方資料更新日期。

    openFDA 在 meta.last_updated 直接給出資料日期，
    這是最可靠的判斷依據。
    """
    out: list[dict] = []
    for label, ep in OPENFDA_ENDPOINTS.items():
        d = _get_json(f"https://api.fda.gov/device/{ep}.json?limit=1")
        if not d:
            out.append({"name": label, "kind": "即時", "ok": False,
                        "date": None, "note": "查詢失敗"})
            continue
        meta = d.get("meta", {})
        out.append({
            "name": label,
            "kind": "即時",
            "ok": True,
            "date": meta.get("last_updated"),
            "total": meta.get("results", {}).get("total"),
            "note": "官方開放資料更新週期，非工具延遲",
        })
    return out


def _days_ago(date_str: str | None) -> int | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            d = datetime.datetime.strptime(str(date_str).strip()[:10], fmt).date()
            return (datetime.date.today() - d).days
        except Exception:
            continue
    return None


def attach_lag(rows: list[dict]) -> list[dict]:
    """為每筆加上「落後天數”。"""
    for r in rows:
        r["lag_days"] = _days_ago(r.get("date"))
    return rows


# ---------------------------------------------------------------- TFDA

TFDA_ZIP_URL = ("https://data.fda.gov.tw/opendata/exportDataList.do"
                "?method=ExportData&InfoId=68&logType=2")


def tfda_remote_date(timeout: int = 300) -> dict:
    """取得 TFDA 官方資料集的來源日期（ZIP 內檔案時間戳）。

    為什麼要用 ZIP 內的時間戳：
    官方 ZIP 不提供 Last-Modified 標頭（實測為 None），
    但 ZIP 內的 CSV 帶有可靠的建立時間戳。
    下載完讀取該時間戳，是判斷「官方是否已更新」最直接的方法。

    回傳 {"date": "YYYY-MM-DD HH:MM", "raw": (y,m,d,h,mi,s), "size_mb": ...}
    """
    ctx = ssl.create_default_context()
    req = Request(TFDA_ZIP_URL, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read()
    except Exception as exc:
        return {"date": None, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = [(i.filename, i.date_time) for i in z.infolist()]
    except Exception as exc:
        return {"date": None, "error": f"ZIP 解析失敗：{exc}"}

    if not infos:
        return {"date": None, "error": "ZIP 內無檔案"}
    fn, dt = infos[0]
    return {
        "date": datetime.datetime(*dt).strftime("%Y-%m-%d %H:%M"),
        "raw": dt,
        "file": fn,
        "size_mb": round(len(raw) / 1024 / 1024, 2),
    }


def tfda_local_date(db_path: Path) -> str | None:
    """讀取本地 TFDA 索引記錄的來源日期。"""
    import sqlite3
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT value FROM meta WHERE key='source_date'").fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def tfda_needs_update(db_path: Path, remote: dict | None = None) -> dict:
    """判斷 TFDA 索引是否需要更新。

    回傳 {"needs_update": bool, "local": ..., "remote": ..., "reason": ...}
    """
    local = tfda_local_date(db_path)
    if remote is None:
        remote = tfda_remote_date()
    rdate = remote.get("date")

    if not db_path.exists():
        return {"needs_update": True, "local": None, "remote": rdate,
                "reason": "本地索引不存在"}
    if not rdate:
        return {"needs_update": False, "local": local, "remote": None,
                "reason": f"無法取得官方日期（{remote.get('error','')}），"
                          "保守起見不自動更新"}
    if not local:
        return {"needs_update": True, "local": None, "remote": rdate,
                "reason": "本地索引未記錄官方來源日期（舊版建立），"
                          "建議重建一次以帶入正確日期"}
    if rdate > local:
        return {"needs_update": True, "local": local, "remote": rdate,
                "reason": f"官方已更新（{rdate} 晚於本地 {local}）"}
    return {"needs_update": False, "local": local, "remote": rdate,
            "reason": "本地索引已是最新"}


# ---------------------------------------------------------------- 綜合報告

def freshness_report(tfda_db: Path | None = None,
                     check_tfda_remote: bool = False) -> dict:
    """綜合新鮮度報告（供介面顯示）。

    check_tfda_remote=True 會下載 16 MB ZIP 取得官方日期（較慢）。
    預設 False，只比對本地記錄。
    """
    rows: list[dict] = []

    # 即時型
    rows += openfda_freshness()
    rows.append({"name": "PubMed", "kind": "即時", "ok": True,
                 "date": None, "note": "每次查詢即時檢索"})
    rows.append({"name": "FreePatentsOnline", "kind": "即時", "ok": True,
                 "date": None, "note": "每次查詢即時檢索"})

    # 快取型
    if tfda_db is not None:
        local = tfda_local_date(tfda_db)
        info = {"name": "TFDA 許可證資料集", "kind": "快取",
                "ok": bool(local), "date": local,
                "note": "需更新才會反映最新取證"}
        if check_tfda_remote:
            rem = tfda_remote_date()
            info["remote_date"] = rem.get("date")
            info["needs_update"] = tfda_needs_update(tfda_db, rem).get(
                "needs_update")
        rows.append(info)

    rows.append({"name": "UN Comtrade", "kind": "快取", "ok": True,
                 "date": "2022", "note": "年度資料，官方落後 3-4 年屬正常"})
    rows.append({"name": "WHO / World Bank", "kind": "快取", "ok": True,
                 "date": None, "note": "年度資料；台灣非會員國無資料"})

    return attach_lag(rows)


def update_tfda(db_path: Path, data_dir: Path | None = None) -> dict:
    """下載並重建 TFDA 索引，把來源日期寫入 meta 表。

    這是「不會自動更新」的那一半的解法：
    TFDA 索引是本地 SQLite，官方資料集更新後需重建才會反映。
    """
    from . import build_tfda_index as bti
    from . import tfda_index

    data_dir = data_dir or db_path.parent
    rem = tfda_remote_date()
    if not rem.get("date"):
        return {"ok": False, "error": rem.get("error", "無法取得官方日期")}

    try:
        csv_path = tfda_index.download_csv(data_dir)
        result = tfda_index.build(csv_path, db_path)
        # 寫入來源日期
        _write_meta(db_path, rem["date"])
        return {"ok": True, "rows": result["rows"],
                "source_date": rem["date"],
                "size_mb": round(db_path.stat().st_size / 1024 / 1024, 1)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}


def _write_meta(db_path: Path, source_date: str) -> None:
    """把來源日期寫進索引的 meta 表。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    con.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('source_date', ?)",
        (source_date,))
    con.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)",
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),))
    con.commit()
    con.close()


def ensure_meta(db_path: Path) -> None:
    """為舊版索引補上 meta 表（一次性）。

    只寫 built_at（建立時間），**不寫 source_date**。

    為什麼不能拿建立時間當來源日期：
    建立時間永遠晚於官方資料日期（下載後才建），若拿它當來源日期，
    本地索引會看起來比實際新，反而漏掉官方更新。
    例：官方資料 9/17、本地 9/22 建立 → 若存 9/22，
    官方 9/24 更新時才會被偵測到，9/18-9/23 之間的更新會漏掉。

    因此 source_date 只在真正取得官方日期時寫入（見 _write_meta 呼叫端）。
    舊索引因缺 source_date，會被判定「建議重建一次」，
    重建後即帶入正確的官方日期。
    """
    import sqlite3
    if not db_path.exists():
        return
    try:
        con = sqlite3.connect(db_path)
        con.executescript(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
        mtime = datetime.datetime.fromtimestamp(db_path.stat().st_mtime)
        con.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)",
            (mtime.strftime("%Y-%m-%d %H:%M"),))
        con.commit()
        con.close()
    except Exception:
        pass


def tfda_built_at(db_path: Path) -> str | None:
    """讀取本地索引的建立時間（供介面顯示）。"""
    import sqlite3
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT value FROM meta WHERE key='built_at'").fetchone()
        con.close()
        if row:
            return row[0]
    except Exception:
        pass
    return datetime.datetime.fromtimestamp(
        db_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def write_meta(db_path: Path, source_date: str) -> None:
    """寫入官方來源日期與建立時間（公開介面）。"""
    _write_meta(db_path, source_date)
