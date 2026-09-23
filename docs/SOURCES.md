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

### 9. PubMed E-utilities（疾病負擔文獻）

- 端點：`https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`
- 免 API key（未帶 key 者每秒最多 3 次，已設 0.4 秒節流）
- esearch → esummary → efetch 三段取得標題與摘要

**這是疾病負擔模組的核心來源。** Biodesign 要的數字
（發生率、現行療法失效比例、每人花費）多半只存在文獻裡。

**踩到的坑（兩輪）**

1. **布林寫法反而變差**：用
   `"urinary retention" AND (prevalence OR incidence)`
   會抑制 PubMed 的 automatic term mapping，
   查 urinary retention 竟回傳「多汗症盛行率」等不相關文獻。
   → 改用**自然語言片語**（不加引號、不加 AND）。

2. **詞數過多會回 0 篇**：實測
   `urinary retention cost economic burden` → 有結果
   `... + healthcare expenditure` → 0 篇
   `urinary retention unmet need limitations failure` → 0 篇
   → **每個查詢控制在 3-4 個詞**。

3. 「未滿足需求」這類抽象概念檢索效益差，
   改以「現行治療的併發症／失敗率」切入更實際。

### 10. WHO GHO / World Bank（全球比較）

- WHO：`https://ghoapi.azureedge.net/api/{IndicatorCode}`
  OData 語法需 URL 編碼，否則 `InvalidURL: control characters`
- World Bank：`https://api.worldbank.org/v2/country/{iso}/indicator/{code}`
- **兩者都沒有台灣資料**（台灣非會員國）。
  台灣數字必須走衛福部統計處與國健署。

### 11. 衛福部統計處（台灣）

- `https://dep.mohw.gov.tw/DOS/` 可用
- 死因統計頁 `np-5068-113.html` 內含 `dl-` 連結清單
- **`dl-` 連結回傳的是真實檔案**（實測某檔為 2.2 MB PDF），
  可直接下載

### 12. 健保署（台灣，需代理繞道）

- `https://www.nhi.gov.tw/` **全站對程式化請求回 403**
- 實測 `https://r.jina.ai/` 前綴可成功取得內容
- 這是繞道方案，非官方 API，穩定性較差 → 已做優雅降級
- 台灣商品化的關鍵門檻是**健保特材給付**，比 FDA 更直接影響
  醫院採購意願

### 13. 國健署（台灣）

- `https://www.hpa.gov.tw/` 可用
- 含國民健康訪問調查、健康促進統計年報

## 一之二、資料更新機制（實測 2026-09-22）

### 即時查詢型（每次都用最新）

| 來源 | 官方資料日期 | 判斷依據 |
|------|------------|---------|
| openFDA UDI | 2026-09-02 | `meta.last_updated` |
| openFDA 510(k) | 2026-09-14 | 同上 |
| openFDA PMA | 2026-09-14 | 同上 |
| openFDA 召回 | 2026-09-16 | 同上 |
| openFDA 製造廠登記 | 2026-09-14 | 同上 |
| PubMed | 即時 | 每日更新 |
| FreePatentsOnline | 即時 | 即時檢索 |
| 健保署 | 即時 | 每次經代理抓取 |

openFDA 在 `meta.last_updated` 直接給出資料日期，最可靠。
落後 6-20 天是官方更新週期，非工具問題。

### 快取下載型（需重建）

| 來源 | 資料日期 | 更新方式 |
|------|---------|---------|
| TFDA 許可證資料集 | 官方檔 2026-09-17 15:22 | `--force` 重建 |
| UN Comtrade | 2022 | 年度資料 |

### TFDA 更新偵測實作要點

1. **官方 ZIP 沒有 Last-Modified 標頭**（實測為 None），
   也無 ETag 可用。唯一可靠依據是 **ZIP 內檔案的建立時間戳**
   （實測 68_2.csv 為 2026-09-17 15:22:32）。
2. 索引新增 `meta` 表記錄 `source_date`（官方日期）與 `built_at`。
3. **關鍵設計陷阱**：不可拿「本地建立時間」當「來源日期」。
   建立時間永遠晚於官方日期（下載後才建），若拿它當來源日期，
   本地索引會看起來比實際新，反而漏掉官方更新。
   例：官方 9/17、本地 9/22 建立 → 若存 9/22，
   官方 9/18-9/23 的更新會被漏掉。
   因此舊索引（無 source_date）一律判定「建議重建一次」。
4. TFDA 開放資料更新頻率約每週（實測來源檔日期 9/17，
   當日為 9/22，落後 5 天）。

## 一之二、檢索品質驗證（跨來源通用機制）

**問題**：檢索詞錯了整份報告就錯了；但怎麼知道檢索詞對了？

**作法是自動驗證。** 判準是「撈回的文獻標題是否含產品核心概念」。
實測校準差距極大，訊號乾淨可用：

| 檢索詞 | 文獻含核心概念比例 |
|---|---|
| ballistocardiography | 94% |
| bladder scanner ultrasound | 91% |
| incontinence pad | 77% |
| wearable device | 0% |
| sensor | 0% |
| 中文原文直接送出 | 0% |

### 判定方式

以「文件中的中英對照」為驗證依據（團隊自己寫的正式術語，
獨立於工具猜的檢索詞）。四級判定：

- **pass** 技術詞命中 ≥35% → 可採用
- **weak** 15-35% → 建議人工檢視
- **fail** <15% → 結果可能不是本產品
- **unverified** 文件無中英對照 → 無法自動驗證，附樣本標題請人工確認

### 三個實測踩到的陷阱

1. **自我循環驗證**：拿工具自己推的技術詞當標準，必定通過。
   智慧尿布案的 `volatile organic compounds` 驗證看似 100% 完美，
   實際撈回「植物揮發性有機化合物」「白酒香氣分析」——
   通用化學詞出現在任何同主題文獻，沒有鑑別力。
   → 只有「文件中英對照」層級可作驗證依據。

2. **誤判正確結果**：無中英對照時概念詞為空，命中率必然 0%。
   若逕判 fail，會把「膀胱容積連續監測」這種正確文獻貼上
   錯誤警告，比不驗證更糟。→ 新增 `unverified` 狀態。

3. **應用場域不參與判定**：Pillow-Based Ballistocardiography
   是最接近心衰竭團隊的先前技術，但標題沒提 heart failure。
   先前技術的本質是「技術相近」，不是「疾病相同」。
   應用場域改為獨立診斷數字。

### 詞形變體檢索

資料庫的詞形處理不一致，同一技術不同詞形撈回的結果幾乎不重疊：

```
ballistocardiography  84 篇
ballistocardiogram    61 篇
→ 共同只有 5 篇，聯集 244 篇
```

只用單一詞形會漏掉大量先前技術。工具會自動以變體補充
（實測心衰竭案例 40 → 108 篇）。對「論文是否破壞新穎性」
這種任務，漏一篇的代價極高。

### 非英文檢索詞偵測

撈回的文獻逾半為非英文標題時判定 fail。實測把中文原文送去查
英文資料庫，會撈回「李晨風電影作品」「健康報」這類完全無關結果，
而報告看起來一切正常 —— 錯誤被掩蓋。

---

## 一之三、論文先前技術來源（M5，免 API key）

專利的先前技術不只有專利。以下四個來源全部免 API key，2026-09 實測可用。

| 來源 | 端點 | 特性 | 筆數（實測） |
|------|------|------|------------|
| Europe PMC | `ebi.ac.uk/europepmc/webservices/rest/search` | 生醫核心，含預印本（source=PPR） | 2,117 |
| OpenAlex | `api.openalex.org/works` | 跨領域，**可依 ROR 機構篩選** | 8,075 |
| Crossref | `api.crossref.org/works` | DOI 後設資料完整，含學位論文 | — |
| arXiv | `export.arxiv.org/api/query` | 工程預印本 | — |

### 關鍵參數與踩坑

**Europe PMC**
- 參數：`query`、`format=json`、`pageSize`、`resultType=core`
- **不要加 `sort=CITED desc`**：實測同一查詢，依引用數排序會撈到
  泛論型高引用回顧（Acinetobacter baumannii、AmpC beta-lactamases），
  相關度排序才會撈到感測器與偵測技術文獻 —— 後者才可能構成新穎性障礙。
- **間歇性空回應**：偶爾只回 `{"version": "6.9"}`（約連 6 次中 1 次，
  間隔 2 秒）。屬伺服器端限流。**必須重試**，否則會靜默漏掉整個來源。
  偵測方式：`set(d.keys()) <= {"version"}`。
- `resultType=core` 回傳約 63 KB／8 筆；`lite` 約 7.5 KB，可省頻寬。

**OpenAlex**
- 機構篩選：`filter=authorships.institutions.ror:<ROR ID>`
- `select` 參數可大幅減少回傳量
- **type:patent 回傳 0 筆** —— OpenAlex 不含專利，不要用來查專利
- 論文的 `cited_by` 不含專利引用（實測 count 為 0），
  NPL 的專利引用關係需從專利端查

**長庚體系 ROR ID（自我碰撞檢測用）**
| 機構 | ROR |
|------|-----|
| 長庚大學 | `https://ror.org/00d80zx46` |
| 長庚紀念醫院 | `https://ror.org/02verss31` |
| 林口長庚 | `https://ror.org/02dnn6q67` |
| 基隆長庚 | `https://ror.org/020dg9f27` |
| 長庚兒童醫院 | `https://ror.org/054e9ag92` |

> ROR API 已改版：舊路徑 `api.ror.org/organizations` 的 `name` 欄位
> 不存在，需用 `api.ror.org/v2/organizations` 並解析 `names[]`。

**Crossref**
- `select` 可只取需要的欄位
- 需過濾 `type` 為 `component`／`reference-entry`／`peer-review`
  者（圖表附件與百科條目，非真正文獻）

**Semantic Scholar** — 實測 HTTP 429（限流），未採用。

### 作者與機構查詢（`source_authors.py`）

| 功能 | OpenAlex 參數 |
|------|--------------|
| 查機構論文 | `filter=authorships.institutions.ror:<ROR>` |
| 找作者 | `/authors?search=<name>&filter=last_known_institutions.ror:<ROR>` |
| 作者著作 | `filter=authorships.author.id:<ID>` |
| 作者+主題 | `filter=authorships.author.id:<ID>,title_and_abstract.search:<topic>` |
| 台灣機構全清單 | `/institutions?filter=country_code:tw`（實測 520 個） |

**實測踩坑**

1. **搜尋中文姓名的英文譯名會混入他人**。實測 `search=Yung-Chun Chang`
   的 5 筆結果含 Yee-Chun Chen、C.M. Wang、Yenchun Jim Wu —— 全非同一人。
   → 工具必須列出候選由人挑選，不可自動認定。

2. **作者+主題要用 filter 內的 `title_and_abstract.search`**，
   優於外層 `search` 參數（後者較寬鬆）。

3. **OpenAlex 同一篇論文有多筆記錄**：arXiv 預印本、Zenodo、
   期刊正式版各有獨立 DOI。實測同一篇「Mind the Gap」出現 3 次
   （`10.48550/arxiv.2608.06752` 與兩個無 DOI 者）。
   → 去重主鍵用**標題正規化**而非 DOI；合併時保留**最早公開日期**。

4. **台灣機構 ROR 要用中文或完整英文名查，且必須驗證國家**。
   實測 `China Medical University` 以英文名查會撈到
   **中國鋼鐵（China Steel）**。正確 ROR 為 `00v408z34`
   （OpenAlex 反查可得）。內建清單的 38 所均已驗證為台灣境內機構。

5. OpenAlex 作者聚合**仍可能錯誤合併**同一人的多個身分，
   或把不同人併為一人。ORCID 較可靠，但仍需檢視。

### 各國優惠期法源（已逐一查證）

| 地區 | 期間 | 涵蓋學術發表 | 法源 |
|------|------|------------|------|
| 台灣 | 12 個月 | 是 | 專利法第 22 條第 3 項（2022 年由 6 個月放寬） |
| 美國 | 12 個月 | 是 | 35 U.S.C. 102(b)（AIA） |
| 日本 | 12 個月 | 是（須程序聲明） | 特許法第 30 條 |
| 韓國 | 12 個月 | 是（有程序要求） | 特許法第 30 條 |
| 中國 | 6 個月 | **僅限規定的學術／技術會議** | 專利法第 24 條 |
| 歐洲 | 6 個月 | **否** | EPC Art. 55（僅明顯濫用與官方展覽） |

> 台灣法條原文（law.moj.gov.tw 實測）：「申請人出於本意或非出於本意
> 所致公開之事實發生後十二個月內申請者，該事實非屬第一項各款或前項
> 不得取得發明專利之情事。」
>
> 網路常見「6 個月」為 2022 年修法前舊資訊，勿引用。

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
