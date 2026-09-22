# 資料來源與 API 實測紀錄

所有端點皆於 2026-09-22 實測。本檔記錄可用的端點、正確參數、
踩到的坑，以及已確認不可用的來源（避免日後重複嘗試）。

---

## 一、可用來源

### 1. TFDA 醫材許可證資料集（台灣）

- 端點：`https://data.fda.gov.tw/opendata/exportDataList.do?method=ExportData&InfoId=68&logType=2`
- 授權：政府資料開放授權條款第 1 版
- 規模：104,680 筆（2026-09 下載，約 16 MB ZIP）
- 儲存：本地 SQLite（`data/tfda_68.db`，精簡版 37 MB）

**踩到的坑**

1. 沒有 REST 查詢端點，只能下載整份資料集做本地索引。
2. 品名為**中文**（「維立」氣管內管），用英文檢索詞永遠查不到。
   必須另外抽一組中文檢索詞。
3. 短詞 LIKE 比對會嚴重灌水：
   `endotracheal tube holder` 若逐詞 OR，會撈到「真空採血管」
   「X 光球管套組件」等含 "tube" 的無關品項，件數從 59 暴增到 472。
4. 解法：具體詞門檻 + 泛用詞停用表 + 漸進式檢索（見下節）。

### 2. openFDA UDI（全球已上市器材）

- 端點：`https://api.fda.gov/device/udi.json`
- 正確欄位：`device_description`、`brand_name`、`company_name`
- 規模：`device_description:"catheter"` → 59,480 筆
- 價值：全球已上市器材清單，含品牌商與 GMDN 分類

**踩到的坑**

- 欄位名不是 `device_name`（那是 classification 端的欄位）。
  用錯欄位會回 HTTP 404，容易誤判為端點失效。

### 3. openFDA 製造廠登記（全球製造國分布）

- 端點：`https://api.fda.gov/device/registrationlisting.json`
- 正確欄位：`proprietary_name`
- 規模：335,224 筆
- 關鍵結構：`registration.iso_country_code` 直接給製造國
- 實測 `proprietary_name:"catheter"` 取 1,000 筆：
  US 597 / MX 78 / CN 76 / CR 31 / GB 31 / NL 18 …共 41 國

**價值**：這是回答「這產品全球誰在做」最有力的資料。

### 4. openFDA PMA（美國高風險器材）

- 端點：`https://api.fda.gov/device/pma.json`
- 正確欄位：`trade_name`
- 規模：4,252 筆
- 價值：有 PMA 紀錄 = 美國 Class III 最高風險，需臨床證據。
  團隊若看到同類產品有 PMA，就知道法規門檻與成本量級。

### 5. openFDA 召回紀錄（競品品質風險）

- 端點：`https://api.fda.gov/device/enforcement.json`
- 正確欄位：`product_description`
- 規模：2,617 筆（`product_description:"catheter"`）
- 價值：競品分析的隱藏金礦。同類產品若常有召回，
  代表技術難度高或品管門檻高 —— 既是風險，也是切入機會。
- 實測 catheter 召回前段廠商：ICU Medical 67 件、
  Medtronic Vascular 45 件、Boston Scientific 34 件

### 6. UN Comtrade（全球醫材市場規模）

- 端點：`https://comtradeapi.un.org/public/v1/preview/C/A/HS`
- 關鍵參數：`reporterCode`、`period`、`cmdCode`、`flowCode`、`partnerCode=0`
- HS 碼：9018（醫療儀器及用具）、901839（導管插管）等

**踩到的坑**

1. `reporterDesc` / `reporterISO` 欄位常回 `None`，
   必須自備國家代碼對照表。
2. **台灣代碼是 490**（Other Asia, nes），不是 158。用 158 回 0 筆。
3. `reporterCode=0`（世界總計）回 0 筆 —— 不能用世界總計，
   要逐國查再自行加總。
4. 公開端點有嚴格速率限制，連續查詢會回 **HTTP 429**，
   需 8 秒以上退避。
5. 多國一次查（逗號分隔）會因 500 筆上限而截斷，
   實測一次只回 5 國。**逐國查詢才穩定。**

**實測結果（2022, HS 9018, 進口）**
中國 12.08 / 德國 6.63 / 比利時 3.98 / 加拿大 3.69 /
澳洲 2.76 / 巴西 1.84 十億美元

### 7. World Bank（各國醫療支出）

- 端點：`https://api.worldbank.org/v2/country/{codes}/indicator/SH.XPD.CHEX.GD.ZS`
- 免費、無 key、穩定
- 實測 2021：US 17.4% / DE 12.7% / FR 12.2% / JP 12.1% / KR 8.3% / CN 5.3%

### 8. FreePatentsOnline（全球專利，五大局）

- 端點：`https://www.freepatentsonline.com/result.html`
- **這是本專案最重要的發現**：FPO 不只美國專利，
  改參數就能涵蓋全球五大局。

| 參數 | 專利局 | 實測命中（`urinary catheter`） |
|------|--------|------------------------------|
| `uspat=on` | 美國核准專利 | 27,057 |
| `usapp=on` | 美國公開申請案 | — |
| `eupat=on` | 歐洲專利局 | 27,129 |
| `wopat=on` | PCT 國際專利 | 64,470 |
| `jp=on` | 日本 | 24,233 |
| `depat=on` | 德國 | 2,645 |
| 全部合併 | — | 81,064 |

**踩到的坑**

1. URL 必須用 `query_txt=...&submit=&<專利局參數>&p=N`，
   用 `result.html?p=N` 的簡寫會被擋。
2. 站上分頁連結格式是 `p=N` **在前**、`query_txt` 在後，
   與請求格式順序相反 —— 抓頁數的正規表示式不能假設 query_txt 在前。
3. 速率限制嚴格：連續請求會 `Connection reset by peer`，
   需 **10-12 秒**退避（原本用 4 秒不夠）。
4. 總命中數藏在 `Matches 1 - 50 out of 4059` 字串裡。
5. 單篇專利頁 URL 需要 **kind code**：
   `EP0263645` 無效，`EP0263645A3` 才有效。美國號碼同理。

**未涵蓋**：台灣（TIPO）、中國（CNIPA）、韓國（KIPRIS）。
FPO 無 `twpat` 參數，實測無效。

---

## 二、已確認不可用（勿再嘗試）

| 來源 | 實測結果 | 備註 |
|------|---------|------|
| Google Patents xhr | HTTP 503 | 非官方端點 |
| Google Patents HTML | HTTP 503 | **含首頁也 503**，確認非 IP 封鎖（Google 首頁與 Scholar 皆 200） |
| EPO OPS | HTTP 403 | 匿名不可用；需註冊免費 API key |
| Espacenet | HTTP 403 | 同上 |
| Lens.org | HTTP 401 / 403 | 需 API key（付費方案） |
| EUDAMED | 回 Angular SPA HTML | 前端路徑拿不到後端 API |
| WIPO PATENTSCOPE | 200 但需 JSF postback | 可取得結果數（96,689）但抓不到清單 |
| DEPATISnet | 有 TSPD 反爬保護 | 需 JS 執行環境 |
| AccessGUDID 搜尋 | HTTP 404 | 僅單筆 lookup 可用（v3） |
| CNIPA | HTTP 412 | 有反爬 |
| KIPRIS | DNS 解析失敗 | 端點已變更 |

---

## 三、檢索詞處理（跨來源通用）

實測發現三類坑，解法已寫入程式：

### 坑一：中英文必須分開

TFDA 品名是中文，FDA / 專利是英文。
用同一組檢索詞會有一邊完全查不到。

```python
derive_device_query(desc)  # → 英文，用於 FDA / 專利
derive_tfda_terms(desc)    # → 中文，用於 TFDA
```

### 坑二：泛用詞會灌水

`_TOO_GENERIC` 停用表 + `_candidate_phrases()` 把關。

實測案例：`bladder scanner ultrasound`
- 若退到 `bladder` → 全球品牌商抓到血壓計（Aneroids、Adcuff）
- 正確退到 `bladder scanner` → 抓到 Suzhou PeakSonic、Astrasono、
  DBMEDX 等真正的膀胱掃描儀廠商

### 坑三：中文詞組是「或」不是「且」

`尿布 看護墊 失禁` 查智慧尿布：
- 「尿布」在 TFDA 是 0 筆
- 「失禁」7 筆、「看護墊」1 筆都是正確命中
- 若要求命中多數詞，正確結果會被誤判為無資料

解法：`search_progressive()` —— 由最具體的詞開始累積，
達標（≥3 筆）就停止加入後續詞。

實測 `膀胱 餘尿 超音波` 查膀胱餘尿偵測儀：
- 全部 OR → 617 筆（「超音波」是 modality 不是品類，嚴重灌水）
- 漸進式 → 只用「膀胱」，47 筆，全部相關

---

## 四、維護提醒

- **TFDA 資料集**建議每季重新下載（`python -m src.build_tfda_index --force`）
- **Comtrade 年度**資料有 1-2 年落後，屬正常
- **FPO 速率限制**在多人同時使用時容易觸發，
  已設 10 秒退避 + 4 次重試；若仍失敗會降級並附檢索詞表
- **Google Patents** 若日後恢復，`include_google=True` 即可重新啟用
- **EPO OPS API key** 免費，申請後可補上歐洲專利的完整申請人分群
  （目前 FPO 只給號碼與標題，沒有 assignee）
