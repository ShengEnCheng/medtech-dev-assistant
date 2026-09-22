"""報告產生（Markdown / JSON）。

報告結構刻意對齊 Biodesign 與 SPARK 的審查需求，
讓同一份報告可以同時作為：課程交付物、SPARK 季度審查附件、
育成輔導依據、政府補助計畫的競爭分析段落。
"""

from __future__ import annotations

from .schema import MODULE_LABELS, Report


def to_markdown(report: Report) -> str:
    L: list[str] = []
    A = L.append
    A(f"# 商品化評估報告 — {report.device_query}")
    A("")
    A(f"- **資料檢索日**：{report.generated}")
    A(f"- **產品說明**：{report.product_description}")
    A(f"- **檢索詞（FDA／專利）**：{report.device_query}")
    A(f"- **檢索詞（TFDA 台灣）**：{report.tfda_query}")
    if getattr(report.burden, "condition", ""):
        A(f"- **疾病檢索詞（PubMed）**：{report.burden.condition}")
    A(f"- **執行模組**：{'、'.join(MODULE_LABELS[m] for m in report.modules_run)}")
    A(f"- **資料來源**：{', '.join(report.sources)}")
    A("")

    # 章節編號動態產生（模組可勾選，不能寫死）
    n = 0
    if "burden" in report.modules_run:
        n += 1
        A(_burden_section(report, n))
    if "regulatory" in report.modules_run:
        n += 1
        A(_regulatory_section(report, n))
    if "competitor" in report.modules_run:
        n += 1
        A(_competitor_section(report, n))
    if "patent" in report.modules_run:
        n += 1
        A(_patent_section(report, n))
    if "paper" in report.modules_run:
        n += 1
        A(_paper_section(report, n))

    n += 1
    A(_feedback_section(report, n))
    n += 1
    A(_gaps_section(report, n))

    A("---")
    A("")
    A(f"> **免責聲明**：{report.disclaimer}")
    return "\n".join(L)


_CN = "一二三四五六七八九十"


def _cn(n: int) -> str:
    """章節編號：1→一、2→二…"""
    return _CN[n - 1] if 1 <= n <= len(_CN) else str(n)


def _burden_section(report: Report, n: int) -> str:
    """疾病負擔與市場推估（Biodesign Stage 2.1／2.2／2.4）。"""
    b = report.burden
    L: list[str] = []
    A = L.append
    A(f"## {_cn(n)}、疾病負擔與市場推估（Biodesign Stage 2.1／2.2／2.4）")
    A("")
    if not b.condition:
        A(f"> {b.note or '未指定疾病檢索詞，未執行。'}")
        A("")
        return "\n".join(L)

    A(f"**疾病／臨床狀態檢索詞**：{b.condition}")
    A("")
    A("> Biodesign 需求篩選的第一問是「這個病有多嚴重」。"
      "以下數字為 PubMed 文獻摘要的**原文引用**，非本工具推算，"
      "惟摘要擷取可能混入不相關文獻，**需人工核對原始文獻**。")
    A("")

    for label in ["流行病學", "經濟負擔", "現行治療", "治療侷限"]:
        d = (b.sections or {}).get(label) or {}
        arts = d.get("articles") or []
        nums = d.get("numbers") or []
        A(f"### {label}")
        A("")
        if nums:
            A("**文獻中的量化數字（原文引用）**")
            A("")
            for x in nums[:5]:
                A(f"- {str(x).strip()}")
            A("")
        if arts:
            A("**文獻來源**")
            A("")
            for a in arts[:4]:
                j = f" — *{a.get('journal','')}*" if a.get("journal") else ""
                A(f"- [{str(a.get('title',''))[:110]}]({a.get('url','')})"
                  f"{j} ({a.get('date','')})")
            A("")
        if not nums and not arts:
            A("（未檢索到；可於進階設定手動指定疾病檢索詞）")
            A("")

    rb = b.reimbursement or {}
    if rb.get("pages"):
        A("### 台灣健保給付現況")
        A("")
        A("> 給付是台灣醫材商品化的關鍵門檻。特材是否納入給付、"
          "給付點數與自費差額，直接決定醫院採購意願。"
          "**須核對最新支付標準。**")
        A("")
        for label, info in rb["pages"].items():
            A(f"- **{label}**：{info.get('url','')}")
        A("")
        if rb.get("note"):
            A(f"> {rb['note']}")
            A("")
    elif rb.get("error"):
        A(f"> 健保給付查詢未完成：{rb['error']}")
        A("")

    if b.market_calc:
        mc = b.market_calc
        A("### 由上而下市場推估（Biodesign 官方方法）")
        A("")
        for s in mc.get("steps", []):
            A(f"- {s}")
        A("")
        A(f"**推估結果**：US$ {mc.get('total_usd', 0):,}")
        A("")
        A(f"> {mc.get('caveat','')}")
        A("")
        A("> Biodesign 要求雙向計算，另需「由下而上」"
          "（各現有解法 × 使用人數 × 單價）。"
          "兩者落差本身就是重要資訊，不是誤差。")
        A("")

    if b.errors:
        A("**檢索狀況**")
        A("")
        for e in b.errors:
            A(f"- {e.get('source','')}：{e.get('error','')}")
        A("")
    if b.note:
        A(f"> {b.note}")
        A("")
    return "\n".join(L)


def _gaps_section(report: Report, n: int) -> str:
    """待團隊補齊的資訊（Biodesign 要求，工具無法代勞）。

    為什麼要寫這一節：
    Biodesign 的風險矩陣（紅／黃／綠）與需求標準（must-have／nice-to-have）
    必須由團隊訪談與臨床觀察得出。若不標出缺口，
    團隊會誤以為「報告產出＝評估完成」，在最關鍵處交白卷。
    """
    L: list[str] = []
    A = L.append
    A(f"## {_cn(n)}、待團隊補齊的資訊（Biodesign 要求，本工具無法代勞）")
    A("")
    A("> 以下為 Biodesign 方法論中必須由團隊完成的部分。"
      "工具只把外部證據攤在桌上，**判斷仍由團隊與 mentor 負責**。")
    A("")
    A("### 1. 需求陳述（Need Statement）")
    A("")
    A("格式：一種 [解決什麼問題] 的方法，用於 [目標族群]，以達成 [期望結果]")
    A("")
    A("- 不可含任何解法線索（不可寫「用超音波」）")
    A("- 三要素齊備：問題、族群、結果")
    A("- 需經利害關係人訪談驗證")
    A("")
    A("### 2. 需求標準（Need Criteria）")
    A("")
    A("Biodesign 要求 3-5 條 must-have 加 3-5 條 nice-to-have，每條具體可量測。")
    A("")
    A("| 類別 | 本工具已提供 | 團隊仍需補 |")
    A("|---|---|---|")
    A("| 療效 | 疾病負擔、現行療法失效比例 | 要改善到什麼程度才算有意義 |")
    A("| 成本 | 間接資料（貿易額、醫療支出） | 決策者可接受的價格上限（須有來源） |")
    A("| 安全 | 競品召回紀錄 | 可量測的安全指標與門檻 |")
    A("| 易用性 | — | 使用時間、訓練需求、場域要求 |")
    A("")
    A("> 官方提醒：療效與成本一定要放進 must-have。"
      "另需做 JEDI 檢查（是否對某些族群較不利）。")
    A("")
    A("### 3. 利害關係人分析（Stage 2.3）")
    A("")
    A("- 誰決定買（決策者）？誰使用（使用者）？誰付錢（付款者）？")
    A("- 各自最在意什麼？對新方案的接受度？")
    A("- **須以訪談取得，無法從資料庫推得。**")
    A("")
    A("### 4. 風險矩陣（Stage 4.6 交付物）")
    A("")
    A("| 構面 | 紅 | 黃 | 綠 |")
    A("|---|---|---|---|")
    A("| 智財（IP） | | | |")
    A("| 法規 | | | |")
    A("| 商業模式／給付 | | | |")
    A("| 技術可行性 | | | |")
    A("")
    A("紅＝可能讓專案停擺；黃＝需額外資源但可處理；綠＝風險合理。")
    A("")
    A("> 本工具有提供依據的構面：智財（專利地景）、法規（分類路徑）、"
      "給付（健保特材）。**技術可行性需團隊以原型驗證。**")
    A("")
    A("### 5. 現行標準治療與其侷限（Stage 2.2）")
    A("")
    A("- 目前醫師實際怎麼處理這個問題？")
    A("- 現有方案哪裡不夠好？（療效、成本、操作、併發症）")
    A("- 本工具從 PubMed 提供線索，**實地臨床觀察仍不可取代**。")
    A("")
    return "\n".join(L)


def _regulatory_section(report: Report, n: int) -> str:
    r = report.regulatory
    L: list[str] = []
    A = L.append
    A(f"## {_cn(n)}、法規路徑判定")
    A("")
    if r.matched_terms:
        A(f"（實際命中檢索詞：{'、'.join(r.matched_terms)}）")
        A("")
    if r.classification:
        A("**分類判定**")
        A("")
        A("| 裝置品名 | 等級 | 法規編號 | 專科 |")
        A("|---|---|---|---|")
        for c in r.classification:
            A(f"| {c.device_name} | Class {c.device_class} | "
              f"{c.regulation_number} | {c.specialty} |")
        A("")
    A(f"**路徑研判**：{r.path_hint}")
    A("")
    if r.predicates:
        A(f"**前導裝置（510(k)，共 {r.predicates_total or len(r.predicates)} 筆，列前 {len(r.predicates)}）**")
        A("")
        A("| K 號 | 品名 | 申請人 | 決定日 |")
        A("|---|---|---|---|")
        for x in r.predicates:
            A(f"| {x.k_number} | {x.device_name[:60]} | {x.applicant} | {x.decision_date} |")
        A("")
    if r.tfda_same_class:
        A(f"**台灣同類已取證**（實際使用檢索詞「{r.tfda_used_terms or r.tfda_query}」，"
          f"共 {r.tfda_total} 筆，列前 {len(r.tfda_same_class)}）")
        if r.tfda_relaxed:
            A("")
            A("> 注意：嚴格比對無結果，已放寬比對條件，"
              "清單可能包含不相關品項，請人工確認。")
        A("")
        A("| 許可證字號 | 級數 | 品名 | 類別 | 申請商 |")
        A("|---|---|---|---|---|")
        for x in r.tfda_same_class[:8]:
            A(f"| {x.license_no} | {x.class_level} | {x.name_zh[:40]} | "
              f"{x.category} | {x.applicant} |")
        A("")
    if r.to_confirm:
        A("**待專業確認**")
        A("")
        for x in r.to_confirm:
            A(f"- {x}")
        A("")
    A(f"> {r.disclaimer}")
    A("")
    return "\n".join(L)


def _competitor_section(report: Report, n: int) -> str:
    c = report.competitor
    L: list[str] = []
    A = L.append
    A(f"## {_cn(n)}、競品與市場地景（全球）")
    A("")
    if c.taiwan_relaxed:
        A(f"> 注意：中文檢索詞「{c.tfda_query}」嚴格比對無結果，"
          "已放寬比對條件，台灣廠商清單可能包含不相關品項，請人工確認。")
        A("")

    # --- 總覽 ---
    A(f"**競爭密度**：{c.density}")
    A("")
    A("| 構面 | 數據 | 來源 |")
    A("|---|---|---|")
    A(f"| 台灣已取證件數 | {c.taiwan_total} | TFDA 許可證資料集 |")
    if c.udi_total is not None:
        A(f"| 全球已上市器材 | {c.udi_total:,} | openFDA UDI |")
    if c.manufacturer_total is not None:
        A(f"| 全球登記製造廠 | {c.manufacturer_total:,} | openFDA 製造廠登記 |")
    if c.pma_total:
        A(f"| 美國高風險核准（PMA） | {c.pma_total:,} | openFDA PMA |")
    if c.recall_total:
        A(f"| 召回紀錄 | {c.recall_total:,} | openFDA 召回 |")
    A("")

    # --- 台灣 ---
    if c.taiwan_licensees:
        used = "、".join(c.tfda_used_terms) if c.tfda_used_terms else c.tfda_query
        A(f"### 台灣已取證廠商（檢索詞「{used}」）")
        A("")
        A("| 申請商 | 許可證件數 | 醫器主類別 |")
        A("|---|---|---|")
        for x in c.taiwan_licensees[:12]:
            A(f"| {x.applicant} | {x.license_count} | {'、'.join(x.categories[:2])} |")
        A("")

    # --- 全球品牌商 ---
    if c.international:
        term = f"（檢索詞「{c.udi_matched_term}」）" if c.udi_matched_term else ""
        A(f"### 全球主要品牌商{term}")
        A("")
        A("| 公司 | 在庫品項數 | 樣本產品 |")
        A("|---|---|---|")
        for x in c.international[:15]:
            samp = (x.samples[0][:45] if x.samples else "—")
            A(f"| {x.applicant} | {x.clearance_count} | {samp} |")
        A("")

    # --- 全球製造國分布 ---
    if c.manufacturer_countries:
        A("### 全球製造廠國別分布")
        A("")
        A("| 國家 | 登記廠數 | 樣本廠商 |")
        A("|---|---|---|")
        for x in c.manufacturer_countries[:15]:
            samp = (x.get("samples") or ["—"])[0][:40]
            A(f"| {x.get('country','')} | {x.get('count',0)} | {samp} |")
        A("")

    # --- 高風險器材 ---
    if c.pma_entries:
        A(f"### 美國高風險器材核准（PMA，共 {c.pma_total or len(c.pma_entries)} 筆）")
        A("")
        A("> 有 PMA 紀錄代表此類產品在美國屬 Class III 最高風險等級，"
          "需臨床證據，法規門檻與成本最高。")
        A("")
        A("| PMA 編號 | 申請人 | 產品名 |")
        A("|---|---|---|")
        for x in c.pma_entries[:8]:
            A(f"| {x.get('pma_number','')} | {x.get('applicant','')[:32]} | "
              f"{x.get('trade_name','')[:50]} |")
        A("")

    # --- 召回紀錄（競品品質風險）---
    if c.recall_firms:
        A(f"### 競品召回紀錄（共 {c.recall_total or 0} 筆）")
        A("")
        A("> 同類產品若有召回，代表技術難度高或品管門檻高 —— "
          "既是風險，也是訴求「更可靠設計」的切入機會。")
        A("")
        A("| 召回廠商 | 件數 |")
        A("|---|---|")
        for x in c.recall_firms[:10]:
            A(f"| {x['firm']} | {x['count']} |")
        A("")

    # --- 全球市場規模 ---
    if c.market_rows:
        A(f"### 全球醫療器材貿易額（UN Comtrade {c.market_year}）")
        A("")
        A(f"> **這不是本產品的市場規模。** HS {c.market_hs}"
          f"（{c.market_hs_desc or '醫療儀器及用具'}）涵蓋**全部**醫療器材，"
          "僅供理解產業量級與各國市場大小排序。"
          "本產品市場請用「疾病負擔」章節的由上而下推估。")
        A("")
        A("| 國家 | 進口額（十億美元） |")
        A("|---|---|")
        for x in c.market_rows[:12]:
            A(f"| {x.get('country','')} | {(x.get('value_usd') or 0)/1e9:.2f} |")
        A("")
        if c.market_total_usd:
            A(f"（主要市場加總：{c.market_total_usd/1e9:.1f} 十億美元 — "
              "此數字為 HS 全類別加總，**不可**作為本產品市場規模引用）")
            A("")
    elif c.market_error:
        A(f"### 全球市場規模")
        A("")
        A(f"（UN Comtrade 查詢未完成：{c.market_error}，可稍後重試）")
        A("")

    # --- 各國醫療支出 ---
    if c.health_spending:
        A("### 各國醫療支出（佔 GDP 比例，World Bank）")
        A("")
        A("| 國家 | 佔比 | 年度 |")
        A("|---|---|---|")
        for x in c.health_spending[:10]:
            A(f"| {x.get('country','')} | {x.get('value_pct','')}% | {x.get('year','')} |")
        A("")

    A(f"> {c.note}")
    A("")
    return "\n".join(L)


def _patent_section(report: Report, n: int) -> str:
    p = report.patent
    L: list[str] = []
    A = L.append
    A(f"## {_cn(n)}、專利與 FTO 初篩")
    A("")
    A(f"**檢索詞擴充**：{'、'.join(p.search_terms)}")
    A("")
    A(f"**使用來源**：{'、'.join(p.sources_used) or '—'}"
      f"　|　**總命中**：{p.fpo_total_matches if p.fpo_total_matches else '未取得'}"
      f"　|　**檢索頁數**：{p.max_page or '未取得'}")
    if p.offices_searched:
        labels = {
            "US": "美國", "USAPP": "美國申請案", "EP": "歐洲",
            "WO": "PCT 國際", "JP": "日本", "DE": "德國",
        }
        A("")
        A(f"**收錄專利局**：{'、'.join(labels.get(o, o) for o in p.offices_searched)}")
        if p.office_counts:
            A("")
            A("| 專利局 | 本次檢索命中筆數 |")
            A("|---|---|")
            for o, n in sorted(p.office_counts.items(), key=lambda x: -x[1]):
                A(f"| {labels.get(o, o)} | {n} |")
    A("")
    if p.fpo_total_matches:
        A("> 檢索命中數為全文檢索的粗篩結果，**不是**相關專利件數；"
          "專利密度請看下方「高相關專利」件數。")
        A("")
    A("> 未涵蓋台灣、中國、韓國專利局。台灣佈局需另查 TIPO，"
      "中國需查 CNIPA，韓國需查 KIPRIS。")
    A("")
    if p.high_risk:
        A(f"**高相關專利（依標題重疊度排序，列前 12 件／共 {len(p.high_risk)} 件）**")
        A("")
        A("| 公開號 | 標題 | 風險 | 連結 |")
        A("|---|---|---|---|")
        for x in p.high_risk[:12]:
            A(f"| {x.document} | {x.title[:50]} | {x.risk} | [查閱]({x.url}) |")
        A("")
    if p.epo_assignees:
        A(f"**主要專利佈局者**（EPO OPS，共 {len(p.epo_assignees)} 家）")
        A("")
        A("> 專利申請人分布回答「誰在這個領域佈局最密」。"
          "佈局密集代表技術已被卡位，需評估迴避設計空間。")
        A("")
        A("| 申請人 | 專利件數 | 樣本專利 |")
        A("|---|---|---|")
        for x in p.epo_assignees[:12]:
            samp = (x.get("samples") or [""])[0][:48]
            A(f"| {x['assignee'][:42]} | {x['count']} | {samp} |")
        A("")
    elif p.epo_note:
        A(f"> {p.epo_note}")
        A("")
    if p.to_confirm:
        A("**待專業確認**")
        A("")
        for x in p.to_confirm:
            A(f"- {x}")
        A("")
    if p.errors:
        A("**檢索狀況**")
        A("")
        for e in p.errors:
            A(f"- {e.get('source')}：{e.get('error', e.get('fallback', ''))}")
        A("")
    A(f"> {p.disclaimer}")
    A("")
    return "\n".join(L)


def _paper_section(report: Report, n: int) -> str:
    """論文先前技術（non-patent literature）。

    為什麼獨立成章：專利的先前技術不只有專利。學術論文同樣破壞新穎性，
    而且校園團隊最常見的致命情況是「自己的老師已發表過」。
    此章同時提醒預印本日期與各國優惠期差異。
    """
    pp = report.paper
    L: list[str] = []
    A = L.append
    A(f"## {_cn(n)}、論文先前技術（非專利文獻）")
    A("")

    if pp.degraded or not pp.papers:
        A(f"（未取得論文資料：{pp.note or '檢索未回傳結果'}）")
        A("")
        return "\n".join(L)

    A(f"- **檢索詞**：{pp.query}")
    A(f"- **命中**：{len(pp.papers)} 篇"
      f"（預印本 {pp.preprint_count} 篇）")
    A(f"- **各來源**：{', '.join(f'{k} {v} 篇' for k, v in pp.counts.items())}")
    A("")
    A("> **專利的新穎性判斷，先前技術不只有專利。**"
      "學術論文、會議論文、學位論文、預印本同樣會破壞新穎性。")
    A("")

    # --- 機構自我碰撞（最高風險項，放最前面）---
    if pp.self_collision:
        A("### 1. 機構自我碰撞（優先確認）")
        A("")
        A(f"偵測到長庚體系 **{len(pp.self_collision)} 篇**同主題論文。"
          "校園團隊最常見的專利風險是**己方已發表**，"
          "請先確認這些論文是否揭露了擬申請的技術特徵。")
        A("")
        A("| 機構 | 標題 | 發表日 | 期刊 |")
        A("|---|---|---|---|")
        for c in pp.self_collision[:12]:
            A(f"| {c.get('institution','')} "
              f"| {(c.get('title') or '')[:70]} "
              f"| {c.get('date','')} "
              f"| {(c.get('venue') or '')[:32]} |")
        A("")

    # --- 論文清單 ---
    A("### 2. 可能構成先前技術的論文")
    A("")
    A("| 型別 | 標題 | 發表日 | 期刊／來源 | 引用 | 來源 |")
    A("|---|---|---|---|---|---|")
    for p in pp.papers[:20]:
        A(f"| {p.get('type_label') or p.get('type','')} "
          f"| {(p.get('title') or '')[:70]} "
          f"| {p.get('date','')} "
          f"| {(p.get('venue') or '')[:30]} "
          f"| {p.get('cites',0)} "
          f"| {','.join(p.get('sources') or [])} |")
    A("")
    if pp.preprint_count:
        A(f"> **注意預印本**：上表有 {pp.preprint_count} 篇預印本。"
          "預印本的公開日**就是**新穎性判斷的公開日，"
          "通常比正式刊登早 6-12 個月。用正式發表日計算優惠期會算錯。")
        A("")

    # --- 優惠期矩陣 ---
    A("### 3. 各國優惠期對照（學術發表是否適用）")
    A("")
    A("**這是本節最關鍵的資訊**：各國優惠期長度不同，"
      "而且「學術發表算不算」是完全不同的問題。")
    A("")
    A("| 地區 | 優惠期 | 涵蓋學術發表 | 風險 | 說明 |")
    A("|---|---|---|---|---|")
    for g in pp.grace_periods:
        covers = g.get("covers_academic")
        if covers is True:
            ctxt = "是"
        elif covers == "部分":
            ctxt = "**僅限指定學術會議**"
        else:
            ctxt = "**否**"
        A(f"| {g.get('region','')} | {g.get('months','')} 個月 "
          f"| {ctxt} | {g.get('risk','')} | {g.get('note','')} |")
    A("")
    A("> **歐洲是最大的風險區**：EPC Art. 55 的 6 個月優惠期僅限"
      "「明顯濫用」與「官方國際展覽」，**學術發表完全不在適用範圍**。"
      "論文一旦公開，歐洲新穎性即喪失，且無補救方式。")
    A("")
    A("> 中國需注意：專利法第 24 條僅限「**規定的學術會議或技術會議**」"
      "首次發表，**一般期刊論文發表不適用**，會直接破壞新穎性。")
    A("")

    # --- 時序建議 ---
    A("### 4. 時序原則")
    A("")
    A(pp.order_note)
    A("")
    A("---")
    A("")
    A("**本節不做法律判斷**。是否真的構成新穎性障礙，"
      "需比對申請專利範圍與論文的實際技術揭露內容，"
      "屬專利師專業範圍。本節只提供論文清單、發表日期與法源對照。")
    A("")
    return "\n".join(L)


def _feedback_section(report: Report, n: int) -> str:
    fb = report.screening_feedback.get("adjustments", {})
    L: list[str] = []
    A = L.append
    A(f"## {_cn(n)}、對需求篩選分數的調整建議")
    A("")
    if not fb:
        A("（未執行回饋評估）")
        return "\n".join(L)
    A("| 篩選維度 | 調整 | 理由 |")
    A("|---|---|---|")
    for _, v in fb.items():
        label = v.get("label", "")
        A(f"| {label} | {v['delta']:+d} | {v['reason']} |")
    A("")
    A(f"> {report.screening_feedback.get('note', '')}")
    A("")
    return "\n".join(L)
