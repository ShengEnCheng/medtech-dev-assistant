"""建立 TFDA 醫材許可證本地索引。

用法：
    python -m src.build_tfda_index
    python -m src.build_tfda_index --data-dir ./data --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import tfda_index


def main() -> int:
    ap = argparse.ArgumentParser(description="建立 TFDA 醫材許可證本地索引")
    ap.add_argument("--data-dir", default="./data", help="索引與快取目錄")
    ap.add_argument("--force", action="store_true", help="即使索引已存在也重建")
    ap.add_argument("--check-update", action="store_true",
                    help="檢查官方是否已更新（不下載重建）")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    db_path = data_dir / f"tfda_{tfda_index.TFDA_INFO_ID}.db"

    if args.check_update:
        from . import freshness
        print("檢查 TFDA 官方資料集是否已更新…")
        rem = freshness.tfda_remote_date()
        st = freshness.tfda_needs_update(db_path, rem)
        print(f"  官方資料日期：{rem.get('date') or '取得失敗'}")
        print(f"  本地索引日期：{st.get('local') or '未記錄'}")
        print(f"  判定：{'**需要更新**' if st['needs_update'] else '已是最新'}"
              f"（{st['reason']}）")
        return 0

    if db_path.exists() and not args.force:
        info = tfda_index.stats(db_path)
        print(f"索引已存在：{info['rows']:,} 筆（建立於 {info['built_at']}）")
        print("如需重建請加 --force")
        return 0

    print("下載 TFDA 醫療器材許可證資料集（約 16 MB）…")
    try:
        csv_path = tfda_index.download_csv(data_dir)
    except Exception as exc:
        print(f"下載失敗：{exc}", file=sys.stderr)
        return 1
    print(f"  已下載：{csv_path}")

    print("建立 SQLite 索引…")
    result = tfda_index.build(csv_path, db_path)

    # 記錄來源日期：用官方 ZIP 內的檔案時間戳，供日後判斷是否需要更新
    # （官方 ZIP 不提供 Last-Modified 標頭，只能用內含時間戳）
    from . import freshness
    rem = freshness.tfda_remote_date()
    if rem.get("date"):
        freshness._write_meta(db_path, rem["date"])
        print(f"完成：{result['rows']:,} 筆 → {result['db']}")
        print(f"  官方資料日期：{rem['date']}")
    else:
        import datetime
        freshness._write_meta(
            db_path,
            datetime.datetime.fromtimestamp(
                csv_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"))
        print(f"完成：{result['rows']:,} 筆 → {result['db']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
