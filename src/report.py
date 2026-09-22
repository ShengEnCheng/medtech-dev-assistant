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
    A(f"- **執行模組**：{'、'.join(MODULE_LABELS[m] for m in report.modules_run)}")
    A(f"- **資料來源**：{', '.join(report.sources)}")
    A("")

    if "regulatory" in report.modules_run:
        A(_regulatory_section(report))
    if "competitor" in report.modules_run:
        A(_competitor_section(report))
    if "patent" in report.modules_run:
        A(_patent_section(report))

    A(_feedback_section(report))
    A("---")
    A("")
    A(f"> **免責聲明**：{report.disclaimer}")
    return "\n".join(L)


def _regulatory_section(report: Report) -> str:
    r = report.regulatory
    L: list[str] = []
    A = L.append
    A("## 一、法規路徑判定")
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


def _competitor_section(report: Report) -> str:
    c = report.competitor
    L: list[str] = []
    A = L.append
    A("## 二、競品與市場地景")
    A("")
    if c.taiwan_relaxed:
        A("")
        A(f"> 注意：中文檢索詞「{c.tfda_query}」嚴格比對無結果，"
          "已放寬比對條件，廠商清單可能包含不相關品項，請人工確認。")
        A("")
    A("")
    A(f"**台灣取證總件數**：{c.taiwan_total}　|　**競爭密度**：{c.density}")
    used = "、".join(c.tfda_used_terms) if c.tfda_used_terms else c.tfda_query
    A(f"　（實際使用檢索詞「{used}」）")
    if c.taiwan_licensees_sample and c.taiwan_licensees_sample < c.taiwan_total:
        A("")
        A(f"（廠商分群抽樣 {c.taiwan_licensees_sample} 筆，總件數為全庫統計）")
    A("")
    if c.taiwan_licensees:
        A("**台灣已取證廠商（前 10）**")
        A("")
        A("| 申請商 | 許可證件數 | 醫器主類別 |")
        A("|---|---|---|")
        for x in c.taiwan_licensees[:10]:
            A(f"| {x.applicant} | {x.license_count} | {'、'.join(x.categories[:2])} |")
        A("")
    if c.international:
        A("**國際競品（510(k) 取證件數）**")
        A("")
        A("| 申請人 | 件數 | 最近取證 |")
        A("|---|---|---|")
        for x in c.international[:10]:
            A(f"| {x.applicant} | {x.clearance_count} | {x.latest_clearance} |")
        A("")
    A(f"> {c.note}")
    A("")
    return "\n".join(L)


def _patent_section(report: Report) -> str:
    p = report.patent
    L: list[str] = []
    A = L.append
    A("## 三、專利與 FTO 初篩")
    A("")
    A(f"**檢索詞擴充**：{'、'.join(p.search_terms)}")
    A("")
    A(f"**使用來源**：{'、'.join(p.sources_used) or '—'}"
      f"　|　**FPO 總命中**：{p.fpo_total_matches if p.fpo_total_matches else '未取得'}"
      f"　|　**FPO 檢索頁數**：{p.max_page or '未取得'}"
      f"　|　**Google Patents 命中**：{p.total_hits or '未取得'}")
    A("")
    if p.fpo_total_matches or p.total_hits:
        A("> 檢索命中數為全文檢索的粗篩結果，**不是**相關專利件數；"
          "專利密度請看下方「高相關專利」件數。")
        A("")
    if p.high_risk:
        A(f"**高相關專利（依標題重疊度排序，列前 12 件／共 {len(p.high_risk)} 件）**")
        A("")
        A("| 公開號 | 標題 | 風險 | 連結 |")
        A("|---|---|---|---|")
        for x in p.high_risk[:12]:
            A(f"| {x.document} | {x.title[:50]} | {x.risk} | [查閱]({x.url}) |")
        A("")
    if p.assignees:
        A("**主要專利佈局者（Google Patents）**")
        A("")
        A("| 申請人 | 件數 | 最近申請 |")
        A("|---|---|---|")
        for x in p.assignees[:8]:
            A(f"| {x.assignee} | {x.count} | {x.latest or '—'} |")
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


def _feedback_section(report: Report) -> str:
    fb = report.screening_feedback.get("adjustments", {})
    L: list[str] = []
    A = L.append
    A("## 四、對需求篩選分數的調整建議")
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
