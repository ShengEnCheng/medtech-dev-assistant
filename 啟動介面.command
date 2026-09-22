#!/bin/bash
# 醫療產品開發助手 — 啟動介面
# 雙擊即可使用。首次執行會自動建立環境（約 1-2 分鐘）。

cd "$(dirname "$0")" || exit 1

echo "=========================================="
echo "  醫療產品開發助手"
echo "=========================================="
echo ""

# 1. 檢查 Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "✗ 找不到 python3，請先安裝 Python 3.10 以上版本"
    echo "  https://www.python.org/downloads/"
    read -r -p "按 Enter 關閉…"
    exit 1
fi

# 2. 建立環境（不存在才建）
if [ ! -x ".venv/bin/python" ]; then
    echo "首次執行，正在建立環境…"
    python3 -m venv .venv || {
        echo "✗ 建立虛擬環境失敗"
        read -r -p "按 Enter 關閉…"
        exit 1
    }
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet -r requirements.txt || {
        echo "✗ 安裝套件失敗，請確認網路連線"
        read -r -p "按 Enter 關閉…"
        exit 1
    }
    echo "✓ 環境建立完成"
fi

# 3. 檢查 TFDA 索引
if [ ! -f "data/tfda_68.db" ]; then
    echo ""
    echo "尚未建立 TFDA 醫材許可證索引（台灣競品資料）"
    read -r -p "現在建立？（約 2 分鐘，需下載 16 MB）[Y/n] " answer
    if [ "$answer" != "n" ] && [ "$answer" != "N" ]; then
        .venv/bin/python -m src.build_tfda_index || echo "⚠ 索引建立失敗，台灣競品模組將無資料"
    fi
fi

# 4. 啟動
echo ""
echo "啟動介面中…瀏覽器會自動開啟（關閉此視窗即結束）"
echo ""
.venv/bin/streamlit run app.py --server.headless=false
