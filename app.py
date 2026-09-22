"""醫療產品開發助手 — Streamlit 介面。

啟動：streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src import analyze
from src.auth import logout_button, require_login
from src.schema import MODULE_LABELS, MODULE_ORDER
from src.report import to_markdown
from src import tfda_index

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"

st.set_page_config(
    page_title="醫療產品開發助手",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------- 存取控制
if not require_login():
    st.stop()

# 索引路徑（雲端部署時會從 repo 內的精簡副本自動還原）
TFDA_DB = tfda_index.get_db_path(DATA_DIR)

# ---------------------------------------------------------------- 側邊欄
with st.sidebar:
    st.markdown("## 🩺 醫療產品開發助手")
    st.caption("Biodesign / SPARK 商品化評估")

    st.markdown("---")
    info = tfda_index.stats(TFDA_DB)
    if info.get("exists"):
        st.success(
            f"TFDA 索引就緒\n\n{info['rows']:,} 筆 · {info['size_mb']} MB\n\n"
            f"建立於 {info['built_at']}"
        )
    else:
        st.warning("TFDA 索引未建立")
        st.caption("台灣競品與法規模組需要本地索引")
        if st.button("建立索引（約 2 分鐘）", width="stretch"):
            with st.spinner("下載並建立 TFDA 索引…"):
                try:
                    csv_path = tfda_index.download_csv(DATA_DIR)
                    res = tfda_index.build(csv_path, TFDA_DB)
                    st.success(f"完成：{res['rows']:,} 筆")
                    st.rerun()
                except Exception as exc:
                    st.error(f"建立失敗：{exc}")

    st.markdown("---")
    st.markdown("### 外部資料源")
    from src import source_epo
    epo = source_epo.status()
    if epo["configured"]:
        st.success(f"EPO OPS 已連線\n\n{epo['message']}")
    else:
        st.info(
            "**EPO OPS 未設定**\n\n"
            "專利申請人分析需要 EPO 憑證。\n\n"
            "申請方式見 `docs/EPO_SETUP.md`（免費）"
        )

    st.markdown("---")
    st.markdown("### 關於")
    st.caption(
        "本工具為早期探索輔助。專利為非正式 FTO 檢索、"
        "法規為分類初判、競品不推算市占率。"
        "所有結論須經專業確認。"
    )
    st.markdown("---")
    logout_button()

# ---------------------------------------------------------------- 主畫面
st.title("醫療產品開發助手")
st.markdown(
    "輸入一段產品說明，勾選需要的模組，產出商品化評估報告。"
)

col_left, col_right = st.columns([3, 2], gap="large")

with col_left:
    product = st.text_area(
        "產品說明",
        height=170,
        placeholder=(
            "例如：我們想開發一款智慧型氣管內管固定裝置，用於加護病房插管病人"
            "翻身時，透過壓力與位移感測即時偵測管子是否滑脫，並發出警示提醒護理人員。"
        ),
        help="用自然中文描述即可，不需要寫成正式規格書。",
    )

with col_right:
    st.markdown("**選擇要產出的報告**")
    st.caption("可只選一項，也可三項全選")
    picked = []
    for m in MODULE_ORDER:
        if st.checkbox(MODULE_LABELS[m], value=True, key=f"mod_{m}"):
            picked.append(m)

    with st.expander("進階設定"):
        manual = st.text_input(
            "手動指定 FDA／專利檢索詞（英文）",
            placeholder="endotracheal tube holder",
            help=(
                "自動抽詞若失焦（例如抽成 sensor 這種泛用詞），"
                "或法規模組抓不到分類時，在這裡指定 FDA 官方品名。"
            ),
        )
        manual_tw = st.text_input(
            "手動指定 TFDA 檢索詞（中文）",
            placeholder="氣管內管",
            help=(
                "台灣競品用。TFDA 品名是中文，系統會自動從產品說明抽中文詞；"
                "若抽得不準，在這裡指定（例如「氣管內管」「導尿管」「尿布」）。"
            ),
        )
        need = st.text_input("Need Statement（選填）", placeholder="")

    run = st.button("開始評估", type="primary", width="stretch",
                    disabled=not product.strip() or not picked)

# ---------------------------------------------------------------- 執行
if run:
    bar = st.progress(0.0, text="準備中…")

    def progress(step: int, total: int, msg: str) -> None:
        bar.progress(min(step / max(total, 1), 0.95), text=msg)

    try:
        rep = analyze.analyze(
            product.strip(),
            modules=picked,
            device_query=manual.strip() or None,
            tfda_query=manual_tw.strip() or None,
            need_statement=need.strip() or None,
            tfda_db=TFDA_DB if TFDA_DB.exists() else None,
            progress=progress,
        )
        bar.progress(1.0, text="完成")
        st.session_state["report"] = rep
    except Exception as exc:
        bar.empty()
        st.error(f"執行失敗：{exc}")
        st.stop()

# ---------------------------------------------------------------- 結果
rep = st.session_state.get("report")
if rep:
    st.markdown("---")
    for w in analyze.warnings(rep):
        st.warning(w)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("檢索詞", rep.device_query)
    m2.metric("台灣取證件數",
              rep.competitor.taiwan_total if "competitor" in rep.modules_run else "—")
    m3.metric("高相關專利",
              len(rep.patent.high_risk) if "patent" in rep.modules_run else "—")
    cls = {c.device_class for c in rep.regulatory.classification if c.device_class}
    m4.metric("FDA 分類", "/".join(f"Class {c}" for c in sorted(cls)) if cls else "未取得")

    tabs = st.tabs(
        [MODULE_LABELS[m] for m in rep.modules_run] + ["調整建議", "完整報告"]
    )
    idx = 0

    if "regulatory" in rep.modules_run:
        with tabs[idx]:
            r = rep.regulatory
            if r.matched_terms:
                st.caption(f"實際命中檢索詞：{'、'.join(r.matched_terms)}")
            if r.classification:
                st.markdown("**分類判定**")
                st.dataframe(
                    [{"裝置品名": c.device_name, "等級": f"Class {c.device_class}",
                      "法規編號": c.regulation_number, "專科": c.specialty}
                     for c in r.classification],
                    width="stretch", hide_index=True)
            st.info(f"**路徑研判**：{r.path_hint}")
            if r.predicates:
                st.markdown(f"**前導裝置（510(k)，共 {r.predicates_total or len(r.predicates)} 筆）**")
                st.dataframe(
                    [{"K 號": x.k_number, "品名": x.device_name,
                      "申請人": x.applicant, "決定日": x.decision_date}
                     for x in r.predicates],
                    width="stretch", hide_index=True)
            if r.tfda_same_class:
                st.markdown("**台灣同類已取證**")
                st.dataframe(
                    [{"許可證字號": x.license_no, "級數": x.class_level,
                      "中文品名": x.name_zh, "類別": x.category, "申請商": x.applicant}
                     for x in r.tfda_same_class],
                    width="stretch", hide_index=True)
            if r.to_confirm:
                st.markdown("**待專業確認**")
                for x in r.to_confirm:
                    st.markdown(f"- {x}")
            st.caption(r.disclaimer)
        idx += 1

    if "competitor" in rep.modules_run:
        with tabs[idx]:
            c = rep.competitor
            st.info(f"**競爭密度**：{c.density}")

            # 全球總覽指標
            ov = [{"構面": "台灣已取證件數", "數據": f"{c.taiwan_total}",
                   "來源": "TFDA 許可證資料集"}]
            if c.udi_total is not None:
                ov.append({"構面": "全球已上市器材", "數據": f"{c.udi_total:,}",
                           "來源": "openFDA UDI"})
            if c.manufacturer_total is not None:
                ov.append({"構面": "全球登記製造廠", "數據": f"{c.manufacturer_total:,}",
                           "來源": "openFDA 製造廠登記"})
            if c.pma_total:
                ov.append({"構面": "美國高風險核准（PMA）", "數據": f"{c.pma_total:,}",
                           "來源": "openFDA PMA"})
            if c.recall_total:
                ov.append({"構面": "競品召回紀錄", "數據": f"{c.recall_total:,}",
                           "來源": "openFDA 召回"})
            if c.market_total_usd:
                ov.append({"構面": "主要市場進口總額",
                           "數據": f"{c.market_total_usd/1e9:.1f} 十億美元",
                           "來源": f"UN Comtrade {c.market_year}"})
            st.dataframe(ov, width="stretch", hide_index=True)

            c1, c2 = st.columns(2)
            with c1:
                used = "、".join(c.tfda_used_terms) if c.tfda_used_terms else c.tfda_query
                st.markdown(f"**台灣已取證廠商**（檢索詞「{used}」）")
                if c.taiwan_licensees:
                    st.dataframe(
                        [{"申請商": x.applicant, "件數": x.license_count,
                          "類別": "、".join(x.categories[:2])}
                         for x in c.taiwan_licensees],
                        width="stretch", hide_index=True)
                else:
                    st.caption("未檢索到對應產品")
            with c2:
                st.markdown("**全球主要品牌商**")
                if c.international:
                    st.dataframe(
                        [{"公司": x.applicant, "品項數": x.clearance_count,
                          "樣本產品": (x.samples[0][:40] if x.samples else "—")}
                         for x in c.international],
                        width="stretch", hide_index=True)
                else:
                    st.caption("未檢索到對應產品")

            g1, g2 = st.columns(2)
            with g1:
                st.markdown("**全球製造廠國別分布**")
                if c.manufacturer_countries:
                    st.dataframe(
                        [{"國家": x.get("country", ""), "登記廠數": x.get("count", 0)}
                         for x in c.manufacturer_countries],
                        width="stretch", hide_index=True)
                else:
                    st.caption("未取得")
            with g2:
                st.markdown(f"**全球市場規模**（HS {c.market_hs}，{c.market_year}）")
                if c.market_rows:
                    st.dataframe(
                        [{"國家": x.get("country", ""),
                          "進口額（十億美元）":
                              round((x.get("value_usd") or 0) / 1e9, 2)}
                         for x in c.market_rows[:10]],
                        width="stretch", hide_index=True)
                else:
                    st.caption(f"未取得（{c.market_error or '—'}）")

            if c.pma_entries:
                with st.expander(f"美國高風險器材核准（PMA，共 {c.pma_total} 筆）"):
                    st.caption("有 PMA 紀錄代表此類產品在美國屬 Class III "
                               "最高風險等級，需臨床證據。")
                    st.dataframe(
                        [{"PMA 編號": x.get("pma_number", ""),
                          "申請人": x.get("applicant", ""),
                          "產品名": x.get("trade_name", "")}
                         for x in c.pma_entries],
                        width="stretch", hide_index=True)

            if c.recall_firms:
                with st.expander(f"競品召回紀錄（共 {c.recall_total} 筆）"):
                    st.caption("同類產品若有召回，代表技術難度高或品管門檻高 —— "
                               "既是風險，也是訴求「更可靠設計」的切入機會。")
                    st.dataframe(
                        [{"召回廠商": x["firm"], "件數": x["count"]}
                         for x in c.recall_firms],
                        width="stretch", hide_index=True)

            if c.health_spending:
                with st.expander("各國醫療支出（佔 GDP 比例，World Bank）"):
                    st.dataframe(
                        [{"國家": x.get("country", ""),
                          "佔比": f"{x.get('value_pct','')}%",
                          "年度": x.get("year", "")}
                         for x in c.health_spending],
                        width="stretch", hide_index=True)

            st.caption(c.note)
        idx += 1

    if "patent" in rep.modules_run:
        with tabs[idx]:
            p = rep.patent
            st.caption(f"檢索詞擴充：{'、'.join(p.search_terms)}")
            st.caption(f"來源：{'、'.join(p.sources_used) or '—'}　"
                       f"檢索頁數：{p.max_page or '—'}")
            if p.offices_searched:
                labels = {"US": "美國", "USAPP": "美國申請案", "EP": "歐洲",
                          "WO": "PCT 國際", "JP": "日本", "DE": "德國"}
                st.success("**收錄專利局**："
                           + "、".join(labels.get(o, o) for o in p.offices_searched))
                if p.office_counts:
                    st.dataframe(
                        [{"專利局": labels.get(o, o), "本次命中筆數": n}
                         for o, n in sorted(p.office_counts.items(),
                                            key=lambda x: -x[1])],
                        width="stretch", hide_index=True)
                st.caption("未涵蓋台灣、中國、韓國專利局。"
                           "台灣佈局需另查 TIPO，中國需查 CNIPA，韓國需查 KIPRIS。")
            if p.high_risk:
                st.markdown(f"**高相關專利（共 {len(p.high_risk)} 件）**")
                st.dataframe(
                    [{"公開號": x.document, "標題": x.title,
                      "風險": x.risk, "連結": x.url} for x in p.high_risk],
                    width="stretch", hide_index=True,
                    column_config={"連結": st.column_config.LinkColumn()})
            if p.assignees:
                st.markdown("**主要專利佈局者（Google Patents）**")
                st.dataframe(
                    [{"申請人": x.assignee, "件數": x.count, "最近申請": x.latest}
                     for x in p.assignees],
                    width="stretch", hide_index=True)
            if p.to_confirm:
                st.markdown("**待專業確認**")
                for x in p.to_confirm:
                    st.markdown(f"- {x}")
            if p.errors:
                with st.expander("檢索狀況"):
                    for e in p.errors:
                        st.caption(f"{e.get('source')}：{e.get('error') or e.get('fallback', '')}")
            st.caption(p.disclaimer)
        idx += 1

    with tabs[idx]:
        fb = rep.screening_feedback.get("adjustments", {})
        if fb:
            st.dataframe(
                [{"篩選維度": v.get("label"), "調整": f"{v['delta']:+d}",
                  "理由": v["reason"]} for v in fb.values()],
                width="stretch", hide_index=True)
        st.caption(rep.screening_feedback.get("note", ""))
    idx += 1

    with tabs[idx]:
        md = to_markdown(rep)
        st.markdown(f"**資料來源**：{', '.join(rep.sources)}")
        st.download_button(
            "下載 Markdown 報告", md,
            file_name=f"商品化評估_{rep.generated}_{rep.device_query[:20]}.md",
            mime="text/markdown", width="stretch")
        st.download_button(
            "下載 JSON", json.dumps(rep.to_dict(), ensure_ascii=False, indent=2),
            file_name=f"商品化評估_{rep.generated}.json",
            mime="application/json", width="stretch")
        if st.button("同時存到 reports/ 目錄"):
            paths = analyze.save(rep, REPORT_DIR)
            st.success(f"已儲存：{paths['markdown'].name}")
        with st.expander("報告預覽", expanded=True):
            st.markdown(md)
