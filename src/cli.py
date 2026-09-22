"""命令列介面。

用法：
    python -m src.cli --product "產品說明" --modules regulatory,competitor,patent
    python -m src.cli --product "產品說明" --device "manual override query"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import analyze
from .schema import MODULE_LABELS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="醫療產品開發助手 — 商品化三模組評估")
    ap.add_argument("--product", required=True, help="產品說明（中文描述即可）")
    ap.add_argument("--modules", default="regulatory,competitor,patent",
                    help="要執行的模組，逗號分隔：regulatory,competitor,patent")
    ap.add_argument("--device", default=None,
                    help="手動指定 FDA／專利檢索詞（英文，覆寫自動抽取）")
    ap.add_argument("--tfda-query", default=None,
                    help="手動指定 TFDA 檢索詞（中文，覆寫自動抽取）")
    ap.add_argument("--need", default=None, help="Need Statement（選填）")
    ap.add_argument("--tfda-db", default="./data/tfda_68.db",
                    help="TFDA 本地索引路徑")
    ap.add_argument("--out", default="./reports", help="報告輸出目錄")
    args = ap.parse_args(argv)

    modules = [m.strip() for m in args.modules.split(",") if m.strip()]
    bad = [m for m in modules if m not in MODULE_LABELS]
    if bad:
        print(f"未知模組：{bad}；可用：{list(MODULE_LABELS)}", file=sys.stderr)
        return 2

    tfda_db = Path(args.tfda_db)
    if not tfda_db.exists():
        print(f"注意：TFDA 索引不存在（{tfda_db}），"
              "台灣競品與法規模組將無資料。\n"
              "  請先執行：python -m src.build_tfda_index\n", file=sys.stderr)

    def progress(step: int, total: int, msg: str) -> None:
        print(f"[{step}/{total}] {msg}")

    rep = analyze.analyze(
        args.product, modules=modules, device_query=args.device,
        tfda_query=args.tfda_query,
        need_statement=args.need, tfda_db=tfda_db, progress=progress,
    )

    for w in analyze.warnings(rep):
        print(f"⚠ {w}")
    if rep.regulatory.errors or rep.competitor.errors or rep.patent.errors:
        print("ℹ 部分來源未取得資料，詳見報告末節。")

    paths = analyze.save(rep, Path(args.out))
    print(f"\n檢索詞：{rep.device_query}")
    print(f"Markdown：{paths['markdown']}")
    print(f"JSON：{paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
