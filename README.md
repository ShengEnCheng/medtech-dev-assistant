# 醫療產品開發助手（MedTech Dev Assistant）

> 輸入一段產品說明 → 依勾選的模組（疾病負擔／專利／法規／競品）
> 產出商品化評估報告。供育成中心、Biodesign 課程、SPARK 團隊使用。

---

## 這個專案要解決什麼

Biodesign 與 SPARK 在台灣都以醫療器材為主要標的，兩個計畫的共通終點都是**商品化**。
而商品化最常被團隊跳過、也最容易造成後續白做的三件事，對應 Biodesign 教科書的三個章節：

| 教科書章節 | 團隊要回答的問題 | 本專案模組 |
|-----------|---------------|-----------|
| Stage 2.1 疾病狀態 ＋ 2.2 治療選項 | 這病有多嚴重？現在怎麼治？哪裡不夠好？ | **M0 疾病負擔與市場推估** |
| Stage 4.1 Intellectual Property Basics | 這東西被申請過嗎？我能不能自由實施？ | **M1 專利與 FTO 初篩** |
| Stage 4.2 Regulatory Basics | 走 510(k) 還是 PMA？TFDA 第幾級？ | **M2 法規路徑判定** |
| Stage 2.4 Market Analysis ＋ 4.4 Business Models | 市場上有誰？做到什麼程度？ | **M3 競品與市場地景** |

> **M0 為什麼排第一**：Stanford 官方需求簡報的格式是固定的
> 「背景 → 觀察 → 問題 → 市場」，全部建立在疾病負擔上。
> 只做專利／法規／競品，會在最關鍵的需求陳述與市場規模處交白卷。

這些模組不是外加功能，而是把 Biodesign Stage 2 需求篩選評分裡**已經存在的三個維度**
（Technical Feasibility、Regulatory / Reimbursement Risk、Market Potential）
從「AI 文字推論」升級成「外部資料佐證」，再回寫修正分數。

報告最後一節「**待團隊補齊的資訊**」是刻意設計的：明確標出風險矩陣
（紅／黃／綠）、需求標準（must-have）、利害關係人分析等 Biodesign
要求但工具無法代勞的部分。**報告產出不等於評估完成。**

---

## 快速開始

### 1. 建立環境

```bash
cd /Volumes/MACRAID/projects/medtech-dev-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

需求：Python 3.10+（開發時使用 3.11）。相依套件只有 streamlit 與 pandas，
核心的三模組引擎只用標準函式庫，**沒有外部 API key 需求**。

### 2. 建立 TFDA 本地索引（首次執行一次，約 2 分鐘）

```bash
python -m src.build_tfda_index
```

會下載衛福部食藥署「醫療器材許可證資料集」（約 16 MB、約 15 萬筆），
建立 SQLite + FTS5 全文索引。之後所有台灣競品查詢都是毫秒級、離線可查。

### 3. 啟動介面

```bash
streamlit run app.py
```

瀏覽器開 http://localhost:8501

### 4. 也可以純命令列使用

```bash
python -m src.cli \
  --product "一款智慧型氣管內管固定裝置，用於加護病房插管病人翻身時偵測管子是否滑脫" \
  --modules regulatory,competitor,patent
```

---

## 專案結構

```
medtech-dev-assistant/
├── app.py                    # Streamlit 介面（主要入口）
├── requirements.txt
├── README.md
├── src/
│   ├── __init__.py
│   ├── schema.py             # 資料結構（dataclass）：三個模組的結果物件
│   ├── http_client.py        # 共用 HTTP（含 openFDA 404 處理、退避重試）
│   ├── tfda_index.py         # TFDA 資料集下載、SQLite FTS5 索引、查詢
│   ├── build_tfda_index.py   # 建索引 CLI
│   ├── query_terms.py        # 產品說明 → 檢索詞抽取（中英映射、泛用詞防護）
│   ├── module_regulatory.py  # M2 法規路徑判定
│   ├── module_competitor.py  # M3 競品與市場地景
│   ├── module_patent.py      # M1 專利與 FTO 初篩
│   ├── feedback.py           # Evidence 回饋引擎 → 三維分數調整
│   ├── report.py             # Markdown 報告產生
│   └── cli.py                # 命令列入口
├── data/                     # 索引與快取（不進版控）
├── reports/                  # 產出報告（不進版控）
└── docs/
    ├── SOURCES.md            # 資料來源與 API 實測紀錄
    ├── ROADMAP.md            # 開發里程碑
    └── DISCLAIMER.md         # 責任界線與免責聲明
```

---

## 資料來源

### 台灣
| 來源 | 用途 | 筆數 |
|------|------|------|
| TFDA 醫材許可證資料集 | 台灣已取證廠商與品名（**本地索引**） | 104,680 |
| PubMed | 疾病負擔文獻（**M0 核心**） | 依檢索詞 |
| 衛福部統計處 | 死因、長照統計（dl- 連結為真實檔） | 依主題 |
| 國健署 | 國民健康訪問調查 | 依主題 |
| 健保署 | 特材給付現況（**需經代理繞道**） | 依頁面 |

### 全球
| 來源 | 用途 | 筆數 |
|------|------|------|
| openFDA UDI | 全球已上市器材、品牌商 | 267,842 |
| openFDA 製造廠登記 | 全球製造廠分布（41 國） | 335,224 |
| openFDA PMA | 美國高風險器材（Class III） | 4,252 |
| openFDA 510(k) | 前導裝置、法規比對 | 14 萬+ |
| openFDA 召回 | 競品品質風險訊號 | 2,617 |
| openFDA 分類 | 法規分類與路徑 | 6 千+ |
| UN Comtrade | 各國醫材**貿易額**（非產品市場規模） | HS 9018，2022 |
| World Bank | 各國醫療支出佔 GDP | 各國年度 |
| WHO GHO | 全球疾病指標（**台灣無資料**） | 依指標 |
| FreePatentsOnline | 專利檢索（**US/EP/WO/JP/DE 五大局**） | 依檢索詞 |
| EPO OPS | 專利申請人分析（需免費 API key） | 依檢索詞 |

### 已知無法使用（勿再嘗試）
| 來源 | 狀況 |
|------|------|
| Google Patents | 2026-09 實測全面 503（含 HTML 首頁），已停用 |
| EPO OPS | HTTP 403，需註冊 API key（免費但需申請） |
| Lens.org | HTTP 401/403，需 API key |
| EUDAMED | Angular SPA，前端路徑無法取得後端 API |
| WIPO PATENTSCOPE | JSF 表單，可取得結果數但無法抓取清單 |
| DEPATISnet | 有 TSPD 反爬保護，需 JS 執行環境 |

詳細端點、實測數據與已知限制見 `docs/SOURCES.md`。

---

## 資料更新機制（重要）

各來源的更新行為**不同**，實測後分兩類（2026-09-22）：

### 即時查詢型 — 每次都用最新，不需任何動作

| 來源 | 官方資料日期（實測） | 落後 |
|------|-------------------|------|
| openFDA UDI | 2026-09-02 | 20 天 |
| openFDA 510(k) | 2026-09-14 | 8 天 |
| openFDA PMA | 2026-09-14 | 8 天 |
| openFDA 召回 | 2026-09-16 | 6 天 |
| openFDA 製造廠登記 | 2026-09-14 | 8 天 |
| PubMed | 即時 | 0 |
| FreePatentsOnline | 即時 | 0 |
| 健保署 | 即時 | 0 |

> 落後 6-20 天是**官方開放資料本身的更新週期**（多為每月或每週），
> 不是工具延遲。工具每次執行都直接打官方 API。

### 快取下載型 — 不會自動更新，需重建

| 來源 | 資料日期 | 更新方式 |
|------|---------|---------|
| TFDA 許可證資料集 | 官方檔案 2026-09-17 | `python -m src.build_tfda_index --force` |
| UN Comtrade | 2022 年 | 年度資料，落後 3-4 年屬正常 |
| WHO / World Bank | 年度 | 台灣非會員國無資料 |

### TFDA 索引更新偵測

TFDA 是唯一「會過期且影響台灣競品準確度」的來源，已建自動偵測：

```bash
# 檢查官方是否已更新（不下載重建）
python -m src.build_tfda_index --check-update
```

原理：官方 ZIP 不提供 Last-Modified 標頭，因此下載後讀取
**ZIP 內檔案的建立時間戳**，與本地索引記錄的 `source_date` 比對。

索引內建 `meta` 表記錄官方來源日期。設計要點：**不可拿「建立時間」
當「來源日期」** —— 建立時間永遠晚於官方日期，會使本地看起來較新
而漏掉更新。

介面側邊欄的「資料新鮮度」面板可直接檢視，並有按鈕即時檢查。

**建議維護頻率**：TFDA 索引每季重建一次（或發現台灣競品結果
與實際取證狀況不符時）。

---

## 免責與使用界線

本系統是**早期探索與資料彙整輔助**，不是專業意見的替代品。

- 專利：非正式 FTO 檢索，正式檢索請委由專利師
- 法規：分類初判，最終以 TFDA / FDA 正式判定為準
- 競品：基於公開資料，不構成市占率或市場規模結論
- 疾病負擔：數字取自 PubMed 文獻摘要的原文引用，**非本工具推算**，
  惟摘要擷取可能混入不相關文獻，需人工核對原始文獻
- 給付：健保特材給付須核對最新支付標準
- **UN Comtrade 的數字是全部醫療器材的貿易額，不是本產品市場規模。**
  實測膀胱掃描儀案例：HS 9018 全類別加總 21.4 十億美元，
  該產品實際市場僅 8,274 萬美元，落差達 259 倍。
  本產品市場請用報告中的「由上而下市場推估」

每一筆外部資料都會標註查詢日期與來源。詳見 `docs/DISCLAIMER.md`。

---

## 與舊原型的關係

本專案是把 `/Volumes/MACRAID/專案/multiagent/` 裡已驗證可行的商品化三模組
抽出、重構為獨立專案。舊 repo 保留作為 Biodesign Identify 多代理人系統與研究紀錄。

抽出的原因：舊 repo 同時包含研究原型、多代理人實驗、報告產物與 venv，
不利於交給同仁使用與後續維護。本專案只保留可交付的部分。
