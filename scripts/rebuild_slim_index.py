"""重建 TFDA 精簡索引（含 meta 表），供 repo 內附。

為什麼要精簡：
雲端容器檔案系統是臨時的，repo 內附一份精簡索引可在啟動時秒級還原。
原版 59 MB → 精簡後約 37 MB（移除 FTS 表與檢索用不到的欄位）。

為什麼這次要重建：
新增了 meta 表記錄官方來源日期（判斷是否需要更新的依據），
舊精簡版沒有此表，會導致每次都被判定需要更新。
"""

import sqlite3
from pathlib import Path

src = Path("data/tfda_68.db")
dst = Path("data/tfda_slim.db")

if dst.exists():
    dst.unlink()

con = sqlite3.connect(src)
meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
print("來源 meta:", meta)

con.executescript(f"""
ATTACH '{dst}' AS slim;
CREATE TABLE slim.lic AS SELECT
    id, license_no, class_level, name_zh, name_en, category, category2,
    applicant, maker_country, valid_date, cancelled
FROM lic;
CREATE TABLE slim.meta AS SELECT * FROM meta;
DETACH slim;
""")
con.close()

c2 = sqlite3.connect(dst)
n = c2.execute("SELECT COUNT(*) FROM lic").fetchone()[0]
m = dict(c2.execute("SELECT key, value FROM meta").fetchall())
c2.close()
print(f"精簡版：{n:,} 筆")
print(f"meta：{m}")
print(f"大小：{dst.stat().st_size / 1024 / 1024:.1f} MB")
