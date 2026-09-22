"""醫療產品開發助手 — 資料結構定義。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

TODAY = date.today().isoformat()

MODULE_LABELS = {
    "regulatory": "法規路徑判定",
    "competitor": "競品與市場地景",
    "patent": "專利與 FTO 初篩",
}

MODULE_ORDER = ["regulatory", "competitor", "patent"]


@dataclass
class ClassificationEntry:
    device_name: str = ""
    device_class: str = ""
    regulation_number: str = ""
    specialty: str = ""
    definition: str = ""


@dataclass
class PredicateDevice:
    k_number: str = ""
    device_name: str = ""
    applicant: str = ""
    decision_date: str = ""


@dataclass
class TfdaRecord:
    license_no: str = ""
    class_level: str = ""
    name_zh: str = ""
    name_en: str = ""
    category: str = ""
    applicant: str = ""
    maker_country: str = ""
    valid_date: str = ""


@dataclass
class RegulatoryResult:
    query: str = ""
    query_date: str = TODAY
    matched_terms: list[str] = field(default_factory=list)
    classification: list[ClassificationEntry] = field(default_factory=list)
    predicates_total: int | None = None
    predicates: list[PredicateDevice] = field(default_factory=list)
    tfda_query: str = ""
    tfda_used_terms: str = ""
    tfda_total: int = 0
    tfda_relaxed: bool = False
    tfda_same_class: list[TfdaRecord] = field(default_factory=list)
    path_hint: str = ""
    to_confirm: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    disclaimer: str = "法規分類初判，最終以 TFDA / FDA 正式判定為準。"


@dataclass
class LicenseeGroup:
    applicant: str = ""
    license_count: int = 0
    categories: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)


@dataclass
class InternationalCompetitor:
    applicant: str = ""
    clearance_count: int = 0
    latest_clearance: str = ""
    samples: list[str] = field(default_factory=list)


@dataclass
class RecallEntry:
    firm: str = ""
    product: str = ""
    classification: str = ""
    reason: str = ""
    date: str = ""
    country: str = ""


@dataclass
class CompetitorResult:
    query: str = ""
    query_date: str = TODAY
    tfda_query: str = ""
    tfda_used_terms: list[str] = field(default_factory=list)
    taiwan_total: int = 0
    taiwan_licensees_sample: int = 0
    taiwan_relaxed: bool = False
    taiwan_licensees: list[LicenseeGroup] = field(default_factory=list)
    international: list[InternationalCompetitor] = field(default_factory=list)

    # --- 全球資料（openFDA UDI / registrationlisting / PMA / enforcement）---
    udi_total: int | None = None
    udi_matched_term: str = ""
    global_companies: list[dict] = field(default_factory=list)
    manufacturer_total: int | None = None
    manufacturer_countries: list[dict] = field(default_factory=list)
    pma_total: int | None = None
    pma_entries: list[dict] = field(default_factory=list)
    recall_total: int | None = None
    recall_firms: list[dict] = field(default_factory=list)
    recall_entries: list[RecallEntry] = field(default_factory=list)

    # --- 市場規模（UN Comtrade / World Bank）---
    market_hs: str = "9018"
    market_year: int = 2022
    market_total_usd: float = 0.0
    market_rows: list[dict] = field(default_factory=list)
    market_error: str = ""
    health_spending: list[dict] = field(default_factory=list)

    density: str = ""
    note: str = "基於公開資料，不構成市占率或市場規模結論。"
    errors: list[dict] = field(default_factory=list)


@dataclass
class PatentHit:
    document: str = ""
    title: str = ""
    abstract: str = ""
    url: str = ""
    overlap_terms: int = 0
    risk: str = ""


@dataclass
class AssigneeGroup:
    assignee: str = ""
    count: int = 0
    latest: str = ""
    samples: list[dict] = field(default_factory=list)


@dataclass
class PatentResult:
    query: str = ""
    query_date: str = TODAY
    search_terms: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    max_page: int = 0
    total_hits: int | None = None
    fpo_total_matches: int | None = None
    offices_searched: list[str] = field(default_factory=list)
    office_counts: dict = field(default_factory=dict)
    epo_assignees: list[dict] = field(default_factory=list)
    epo_note: str = ""
    assignees: list[AssigneeGroup] = field(default_factory=list)
    hits: list[PatentHit] = field(default_factory=list)
    high_risk: list[PatentHit] = field(default_factory=list)
    to_confirm: list[str] = field(default_factory=list)
    degraded: bool = False
    errors: list[dict] = field(default_factory=list)
    disclaimer: str = (
        "非正式 FTO 檢索，正式檢索請委由專利師。專利狀態以官方專利局為準。"
        "收錄範圍為 US/EP/WO/JP/DE 等主要專利局（依實測可查者），"
        "未涵蓋台灣、中國、韓國等局，區域佈局需另查各國專利局。"
    )


@dataclass
class Report:
    generated: str = TODAY
    product_description: str = ""
    need_statement: str = ""
    device_query: str = ""
    tfda_query: str = ""
    modules_run: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    regulatory: RegulatoryResult = field(default_factory=RegulatoryResult)
    competitor: CompetitorResult = field(default_factory=CompetitorResult)
    patent: PatentResult = field(default_factory=PatentResult)
    screening_feedback: dict = field(default_factory=dict)
    disclaimer: str = (
        "本報告為早期探索與資料彙整輔助，不提供專利法律意見、"
        "不保證法規核准、不構成市占率或投資建議。"
        "所有結論須經專業確認；資料檢索日見報告首欄。"
    )

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
