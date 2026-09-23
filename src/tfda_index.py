"""TFDA 醫療器材許可證資料集的本地索引。

為什麼要本地索引：
TFDA 沒有公開的即時查詢 REST 端點（實測 data.fda.gov.tw/rest/opendata 回 404），
唯一可靠途徑是下載官方資料集。資料集約 16 MB / 15 萬筆，
規模小，用 SQLite + FTS5 建索引後查詢延遲可壓在毫秒級。

資料集：政府資料開放平臺「醫療器材許可證資料集」
  https://data.gov.tw/dataset/9576
API 下載端點：https://data.fda.gov.tw/opendata/exportDataList.do
  ?method=ExportData&InfoId=68&logType=2

授權：政府資料開放授權條款第 1 版。使用時應標註資料來源與更新日期。
"""

from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from pathlib import Path

from .http_client import get_text

TFDA_INFO_ID = 68
TFDA_ZIP_URL = (
    "https://data.fda.gov.tw/opendata/exportDataList.do"
    f"?method=ExportData&InfoId={TFDA_INFO_ID}&logType=2"
)

# 資料集欄位 → 資料庫欄位
_COLUMNS = [
    ("許可證字號", "license_no"),
    ("醫療器材級數", "class_level"),
    ("中文品名", "name_zh"),
    ("英文品名", "name_en"),
    ("效能", "efficacy"),
    ("醫器主類別一", "category"),
    ("醫器主類別二", "category2"),
    ("申請商名稱", "applicant"),
    ("申請商地址", "applicant_addr"),
    ("製造商名稱", "maker"),
    ("製造廠國別", "maker_country"),
    ("有效日期", "valid_date"),
    ("發證日期", "issue_date"),
    ("註銷狀態", "cancelled"),
]


def download_csv(dest_dir: Path) -> Path:
    """下載 TFDA 資料集 ZIP 並解出 CSV。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    req_url = TFDA_ZIP_URL
    # ZIP 是二進位，用 urlopen 直取
    import urllib.request
    from .http_client import DEFAULT_UA
    req = urllib.request.Request(req_url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    if len(raw) < 100_000:
        raise RuntimeError(
            f"TFDA 下載異常（僅 {len(raw)} bytes），請確認網路或端點是否變更")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        content = z.read(name)
    csv_path = dest_dir / f"tfda_{TFDA_INFO_ID}.csv"
    csv_path.write_bytes(content)
    return csv_path


def _db_source_date(db_path: Path) -> str:
    """讀取索引的官方資料日期（meta.source_date）。

    讀不到時回空字串（舊版索引、或檔案不存在）。
    不拋例外 —— 索引還原不該讓整個 app 起不來。
    """
    try:
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT value FROM meta WHERE key='source_date'").fetchone()
            return str(row[0]) if row and row[0] else ""
        finally:
            con.close()
    except Exception:
        return ""


def _is_newer_db(candidate: Path, current: Path) -> bool:
    """判斷 candidate 的官方資料是否比 current 新。

    判準優先序（語意由強到弱）：
      1. candidate 無日期 → 不換（無從比較，保守）
      2. current 無日期 → 不換（可能是使用者剛手動重建的完整索引，
         且舊版索引會由 ensure_meta() 一次性補上日期，屬過渡狀態）
      3. 雙方都有日期 → 比日期字串（ISO 格式可直接比大小）
      4. 都讀不到 → 比檔案大小，且要求「大小不同」才換

    為什麼不用 mtime 當主要判準：git checkout 會把 mtime 設為當下時間，
    跨環境複製後 mtime 完全不可信 —— 可能用舊資料覆蓋新資料。

    為什麼 current 無日期時不換：無日期的索引無法判斷新舊。部署容器內
    的副本來自 slim（必帶日期），因此不會卡在無日期狀態；若真的遇到，
    使用 --force 或介面上的重建按鈕即可。寧可漏更新一次，也不要蓋掉
    使用者剛建好的索引。
    """
    if not candidate.exists():
        return False
    if not current.exists():
        return True

    a, b = _db_source_date(candidate), _db_source_date(current)
    if a and b:
        return a > b
    # 任一方無官方日期 → 無從比較，維持現狀
    return False


def get_db_path(data_dir: Path) -> Path:
    """取得 TFDA 索引路徑，必要時自動從 repo 內的精簡副本還原。

    為什麼需要這一步：
    部署到 Streamlit Community Cloud 時，容器的檔案系統是臨時的，
    每次休眠喚醒後 data/ 目錄會消失。若每次都要等 2 分鐘重建索引，
    使用者體驗很差。

    解法：repo 內附一份精簡索引（tfda_slim.db，約 37 MB），
    啟動時若 data/ 沒有索引就複製一份過來（秒級完成）。
    精簡版已移除 FTS 表與檢索用不到的欄位（申請商地址、製造商、發證日期），
    檢索功能完全相同。
    """
    import shutil

    canonical = data_dir / f"tfda_{TFDA_INFO_ID}.db"
    bundled = data_dir / "tfda_slim.db"

    if canonical.exists():
        # 若 repo 內的精簡索引比容器內的副本新，需重新複製。
        #
        # 為什麼需要這一步（實測踩到）：
        # data/* 是 gitignore 的（僅 tfda_slim.db 例外），部署平台
        # 重新部署時不會刪掉執行期產生的 tfda_68.db。舊版邏輯只要
        # canonical 存在就直接回傳，導致更新 repo 內的精簡索引後
        # 線上永遠沿用舊副本 —— 實測線上顯示 37.2 MB、repo 已是 22 MB，
        # TFDA 官方每週更新永遠反映不到部署環境。
        #
        # 判準用「官方資料日期」（meta.source_date）而非檔案 mtime：
        # mtime 在不同環境複製時會走樣（git checkout 會把 mtime 設為
        # 當下時間），拿它比較可能反過來用舊資料覆蓋新資料。
        # 資料日期才是語意上正確的比較基準。
        if bundled.exists() and _is_newer_db(bundled, canonical):
            import shutil
            shutil.copy2(bundled, canonical)
            return canonical
        return canonical

    if bundled.exists():
        import shutil
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, canonical)
        return canonical

    return canonical


def build(csv_path: Path, db_path: Path) -> dict:
    """由 CSV 建立 SQLite + FTS5 索引。"""
    csv.field_size_limit(10**7)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    col_defs = ",\n            ".join(f"{c} TEXT" for _, c in _COLUMNS)
    con.executescript(f"""
        DROP TABLE IF EXISTS lic;
        DROP TABLE IF EXISTS lic_fts;
        CREATE TABLE lic (
            id INTEGER PRIMARY KEY,
            {col_defs}
        );
    """)

    n = 0
    batch: list[tuple] = []
    with open(csv_path, encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            batch.append(tuple(
                (row.get(zh) or "")[:2000] for zh, _ in _COLUMNS
            ))
            n += 1
            if len(batch) >= 5000:
                con.executemany(
                    f"INSERT INTO lic VALUES (NULL, {','.join('?' * len(_COLUMNS))})",
                    batch)
                batch = []
    if batch:
        con.executemany(
            f"INSERT INTO lic VALUES (NULL, {','.join('?' * len(_COLUMNS))})", batch)

    con.executescript("""
        CREATE VIRTUAL TABLE lic_fts USING fts5(
            name_zh, name_en, efficacy, applicant, category,
            content='lic', content_rowid='id', tokenize='unicode61'
        );
        INSERT INTO lic_fts(rowid, name_zh, name_en, efficacy, applicant, category)
            SELECT id, name_zh, name_en, efficacy, applicant,
                   COALESCE(category,'') || ' ' || COALESCE(category2,'')
            FROM lic;
        CREATE INDEX idx_applicant ON lic(applicant);
        CREATE INDEX idx_category ON lic(category);
    """)
    con.commit()
    con.close()
    return {"rows": n, "db": str(db_path), "csv": str(csv_path)}


# 泛用詞停用表：這些詞在醫材品名裡太常見，不能當作「具體詞」。
# 例：holder 只有 6 個字母，若單憑長度會被誤判為具體詞，
# 結果「持針器」（needle holder）會被當成氣管內管固定器的競品。
_GENERIC_TERMS = {
    "tube", "holder", "device", "system", "kit", "set", "line", "needle",
    "pad", "probe", "sensor", "monitor", "clip", "clamp", "mask", "glove",
    "syringe", "filter", "bag", "cover", "support", "catheter", "pad",
    "dressing", "patch", "strap", "belt", "band", "plate", "wire", "port",
    "valve", "pump", "screen", "test", "strip", "sheet", "film", "lens",
}


def _is_specific(term: str) -> bool:
    """判斷是否為「具體詞」（可作為相關性門檻）。

    規則：
    1. 非 ASCII（中文詞如「氣管內管」）→ 具體
    2. 在泛用詞停用表中 → 不具體（即使字母數夠長）
    3. 長度 >= 6 → 具體
    """
    t = term.lower()
    if not term.isascii():
        return True
    if t in _GENERIC_TERMS:
        return False
    return len(term) >= 6


def search_with_meta(db_path: Path, query: str, limit: int = 10,
                     active_only: bool = True, strict: bool = True) -> dict:
    """檢索並回傳是否放寬了嚴格模式（供報告標註可信度）。"""
    rows = _search_rows(db_path, query, active_only=active_only, strict=strict)
    relaxed = False
    if not rows and strict:
        rows = _search_rows(db_path, query, active_only=active_only, strict=False)
        relaxed = bool(rows)
    out: list[dict] = []
    for v in rows[:limit]:
        r = dict(v["row"])
        r.pop("efficacy", None)
        r["_relevance"] = round(v["score"], 2)
        r["_specific_hits"] = v["specific_hits"]
        out.append(r)
    return {"rows": out, "relaxed": relaxed, "total": len(rows)}


def search(db_path: Path, query: str, limit: int = 10,
           active_only: bool = True, strict: bool = True) -> list[dict]:
    """檢索並回傳結果清單（不含中介資訊）。"""
    return search_with_meta(db_path, query, limit=limit,
                            active_only=active_only, strict=strict)["rows"]


def _search_rows(db_path: Path, query: str, *, active_only: bool,
                 strict: bool) -> list[dict]:
    """內部：回傳依相關度排序的候選（含分數），供 search / count 共用。"""
    if not db_path.exists():
        return []
    raw_terms = [t for t in query.replace("-", " ").split() if len(t) > 1]
    # 中文檢索詞（如「氣管內管」）直接視為具體詞
    terms = raw_terms or [query]
    specific = [t for t in terms if _is_specific(t)]
    # 嚴格模式的門檻：命中「任一」具體詞即可（組內是「或」的關係）。
    # 不能用「命中一半」—— 中文關鍵詞組是「或」的關係不是「且」。
    # 實測「尿布 看護墊 失禁」查智慧尿布：「尿布」在 TFDA 是 0 筆，
    # 但「失禁」7 筆、「看護墊」1 筆都是正確命中的相關產品；
    # 若要求命中多數詞，正確結果會被誤判為無資料。
    needed = 1 if specific else 0

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    scored: dict[str, dict] = {}

    for t in terms:
        weight = min(len(t) / 5.0, 3.0)
        sql = """SELECT license_no, class_level, name_zh, name_en, category,
                        applicant, maker_country, valid_date, efficacy
                 FROM lic
                 WHERE (name_zh LIKE ? OR name_en LIKE ? OR efficacy LIKE ?)"""
        params: list = [f"%{t}%", f"%{t}%", f"%{t}%"]
        if active_only:
            sql += " AND (cancelled IS NULL OR cancelled = '' OR cancelled = '未註銷')"
        sql += " LIMIT 20000"

        for row in con.execute(sql, params).fetchall():
            r = dict(row)
            key = r.get("license_no") or ""
            if not key:
                continue
            rec = scored.setdefault(
                key, {"row": r, "score": 0.0, "name_hits": 0, "specific_hits": 0})
            in_name = t in (r.get("name_zh") or "") or t in (r.get("name_en") or "")
            if in_name:
                rec["score"] += 3.0 * weight
                rec["name_hits"] += 1
            else:
                rec["score"] += 1.0 * weight
            if _is_specific(t) and in_name:
                rec["specific_hits"] += 1
                rec["score"] += 2.0 * weight  # 具體詞命中額外加權
    con.close()

    out = list(scored.values())
    if strict and needed:
        out = [v for v in out if v["specific_hits"] >= needed]
    else:
        out = [v for v in out if v["name_hits"] > 0]
    out.sort(key=lambda v: (-v["specific_hits"], -v["name_hits"], -v["score"]))
    return out


def count(db_path: Path, query: str, active_only: bool = True,
          strict: bool = True) -> int:
    """計算符合檢索詞的許可證真實總件數。

    為什麼需要獨立函式：
    1. 最初直接用 search() 回傳的 list 長度當件數，
       但 search() 有 limit（預設 200），所以「200 件」其實是上限截斷值。
    2. 更嚴重的是「tube」這類短詞會把「X 光球管」「餵食管」全算進來，
       總件數被灌水到 472 件。
    因此改用與 search() 相同的相關度規則（含具體詞門檻）統計。
    """
    rows = _search_rows(db_path, query, active_only=active_only, strict=strict)
    if not rows and strict:
        rows = _search_rows(db_path, query, active_only=active_only, strict=False)
    return len(rows)


def search_progressive(db_path: Path, terms: list[str], limit: int = 200,
                       min_results: int = 3, active_only: bool = True) -> dict:
    """漸進式檢索：由最具體的詞開始累積，達到 min_results 就停。

    為什麼需要這個：
    中文關鍵詞組的具體程度差異很大，全部 OR 起來會嚴重灌水。
    實測「膀胱 餘尿 超音波」查膀胱餘尿偵測儀：
      單獨「膀胱」→ 47 筆（含「超音波膀胱掃描儀」，正確）
      全部 OR   → 617 筆（「超音波」是 modality 不是品類，
                 把超音波探頭、超音波掃描儀全撈進來）

    規則：依序累積詞組，一旦命中數 >= min_results 就停止加入後續詞。
    如此「膀胱」單獨就達標 → 只用「膀胱」，不會被「超音波」汙染。
    若最具體的詞完全沒命中（如「尿布」在 TFDA 是 0 筆），
    才會退到下一個詞（「失禁」7 筆）。

    回傳 {"rows", "used_terms", "total", "relaxed"}。
    """
    if not db_path.exists():
        return {"rows": [], "used_terms": [], "total": 0, "relaxed": False}

    used: list[str] = []
    rows: list[dict] = []
    for t in terms:
        if not t:
            continue
        used.append(t)
        rows = _search_rows(db_path, " ".join(used), active_only=active_only,
                            strict=True)
        if len(rows) >= min_results:
            break

    relaxed = False
    if not rows:
        rows = _search_rows(db_path, " ".join(used) or "", active_only=active_only,
                            strict=False)
        relaxed = bool(rows)

    out: list[dict] = []
    for v in rows[:limit]:
        r = dict(v["row"])
        r.pop("efficacy", None)
        r["_relevance"] = round(v["score"], 2)
        r["_specific_hits"] = v["specific_hits"]
        out.append(r)
    return {"rows": out, "used_terms": used, "total": len(rows),
            "relaxed": relaxed}


def search_by_category(db_path: Path, category: str, limit: int = 20) -> list[dict]:
    """依醫器主類別檢索（當關鍵詞檢索失焦時的退路）。

    例如「D 麻醉科學」可撈出該類別全部已取證產品，
    適合使用者只知道領域、不知道品名的情況。
    """
    if not db_path.exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT license_no, class_level, name_zh, name_en, category,
                  applicant, maker_country, valid_date
           FROM lic
           WHERE category LIKE ?
             AND (cancelled IS NULL OR cancelled = '' OR cancelled = '未註銷')
           LIMIT ?""",
        (f"%{category}%", limit),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def categories(db_path: Path) -> list[tuple[str, int]]:
    """回傳所有醫器主類別與件數（供介面下拉選單）。"""
    if not db_path.exists():
        return []
    con = sqlite3.connect(db_path)
    rows = con.execute(
        """SELECT category, COUNT(*) c FROM lic
           WHERE category IS NOT NULL AND category != ''
           GROUP BY category ORDER BY c DESC"""
    ).fetchall()
    con.close()
    return [(r[0], r[1]) for r in rows]


def stats(db_path: Path) -> dict:
    """索引基本資訊（筆數、建立時間）。"""
    if not db_path.exists():
        return {"exists": False}
    con = sqlite3.connect(db_path)
    n = con.execute("SELECT COUNT(*) FROM lic").fetchone()[0]
    con.close()
    mtime = db_path.stat().st_mtime
    import datetime
    return {
        "exists": True,
        "rows": n,
        "db": str(db_path),
        "size_mb": round(db_path.stat().st_size / 1024 / 1024, 1),
        "built_at": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
    }
