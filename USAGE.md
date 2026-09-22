# 醫療產品開發助手

輸入一段產品說明，產出專利／法規／競品三份商品化評估報告。

## 給同仁的使用方式

### 方法一：圖形介面（建議）

雙擊 `啟動介面.command`，瀏覽器會自動開啟。
第一次啟動需要約 30 秒安裝環境。

### 方法二：命令列

```bash
cd /Volumes/MACRAID/projects/medtech-dev-assistant
.venv/bin/python -m src.cli --product "你的產品說明" --modules regulatory,competitor,patent
```

## 三個模組

| 模組 | 代號 | 對應 Biodesign 章節 | 回答什麼問題 |
|------|------|-------------------|-------------|
| 法規路徑判定 | regulatory | Stage 4.2 | 走 510(k) 還是 PMA？TFDA 第幾級？ |
| 競品與市場地景 | competitor | Stage 2.4 / 4.4 | 台灣有誰取證了？國際有誰？ |
| 專利與 FTO 初篩 | patent | Stage 4.1 | 這東西被申請過嗎？能不能自由實施？ |

可以只跑其中一個，也可以三個都跑。

## 資料來源

- **openFDA**（美國 FDA 官方 API）— 法規分類、510(k) 前導裝置
- **TFDA 醫材許可證資料集**（食藥署開放資料，本地索引）— 台灣競品名單
- **FreePatentsOnline** — 專利檢索主來源
- **Google Patents** — 專利申請人分群（次要）

## 重要限制

本系統是**早期探索輔助**，不是專業意見的替代品。

- 專利：非正式 FTO 檢索，正式檢索請委由專利師
- 法規：分類初判，最終以 TFDA / FDA 正式判定為準
- 競品：基於公開取證資料，**不推算市占率或市場規模**

每一筆外部資料都標註檢索日期與來源。所有結論須經專業確認。

## 疑難排解

**介面開不起來** — 先確認 TFDA 索引已建立：
```bash
.venv/bin/python -m src.build_tfda_index
```

**專利模組沒有結果** — FreePatentsOnline 有速率限制，等幾分鐘再試。
若持續失敗，報告會附上檢索詞表，可到 patents.google.com 人工檢索。

**法規模組沒抓到分類** — 這是檢索詞與 FDA 官方品名不符。
在介面上「進階設定」手動指定檢索詞（用英文官方品名）再跑一次。
