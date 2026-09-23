"""檢索結果的相關性驗證與多候選檢索。

## 為什麼需要這個模組

檢索詞對了，結果才對；但**怎麼知道檢索詞對了**？過去只能靠人工逐篇看。
實測發現這個判斷可以自動化 —— 同一產品、不同檢索詞，撈回文獻的
「標題含產品核心概念的比例」差距極大：

    檢索詞                      含核心概念比例
    ballistocardiography       94%   ███████████████████
    bladder scanner ultrasound 91%   ██████████████████
    incontinence pad sensor    77%   ███████████████
    wearable device             0%
    sensor                      0%
    中文原文直接送出             0%

0% 對比 77-94% 是乾淨可用的判準，不需要人工介入。

## 另一個實測發現（決定要跑多變體）

同一技術的詞形變體，撈回的結果幾乎不重疊：

    ballistocardiography   84 篇
    ballistocardiogram     61 篇
    → 共同只有 5 篇，聯集 244 篇

資料庫的詞形處理不一致（有的做詞幹、有的做精確比對），
只用單一詞形會漏掉三分之二的先前技術。
對「論文是否破壞專利新穎性」這種任務，漏掉一篇的代價極高，
因此多變體檢索是必要的，不是最佳化。

## 設計原則

1. **驗證用的概念詞必須獨立於檢索詞**，否則會自我循環。
   概念詞取自文件本身（中英對照的英文全稱與縮寫）。
2. **漸進式候選**：最具體的詞先試，通過驗證就停（省時間）。
3. **驗證不過就不給結果**，改為明確警告並要求人工指定。
   給出看似正常的錯誤結果，比不給結果危險得多。
"""

from __future__ import annotations

import re
import time

from .query_terms import extract_bilingual_pairs, is_condition_zh

# 詞形變體規則：英文技術詞的常見構詞後綴。
#
# 為什麼用「去掉尾綴取詞幹」而不是窮舉所有變體：
# ballistocardiography 的詞幹是 ballistocardiograph，
# 而 ballistocardiograph 本身就是 ballistocardiogram /
# ballistocardiographic / ballistocardiography 的共同前綴。
# 用詞幹當驗證詞，一個 token 就涵蓋所有變體。
_STEM_RULES = [
    ("graphy", "graph"),
    ("gram", "graph"),
    ("graphic", "graph"),
    ("metry", "meter"),
    ("metric", "meter"),
    ("scope", "scop"),
    ("scopy", "scop"),
    ("scopic", "scop"),
    ("tion", "t"),
    ("s", ""),
]

# 產生多變體檢索詞用的後綴替換
_VARIANT_MAP = {
    "graphy": ["gram", "graphic", "graph"],
    "gram": ["graphy", "graphic"],
    "metry": ["meter", "metric"],
    "meter": ["metry", "metric"],
    "scope": ["scopy", "scopic"],
    "scopy": ["scope", "scopic"],
}

# 驗證門檻
MIN_SAMPLE = 5          # 少於此篇數不做判定（樣本不足）
PASS_RATE = 0.35        # 核心概念命中率達此值 → 通過
WEAK_RATE = 0.15        # 低於此值 → 明顯撈錯


def _stem(word: str) -> str:
    """取英文技術詞的詞幹（用於驗證與變體產生）。

    radiography → radiograph；ballistocardiogram → ballistocardiograph
    """
    w = (word or "").strip().lower()
    if len(w) < 6:
        return w
    for suffix, repl in _STEM_RULES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 5:
            return w[: len(w) - len(suffix)] + repl
    return w


def generate_variants(term: str) -> list[str]:
    """產生技術詞的詞形變體（供多變體檢索用）。

    實測必要性：ballistocardiography 與 ballistocardiogram 撈回的
    文獻只有 5 篇重疊（84 與 61 篇，聯集 244 篇）。
    只用單一詞形會漏掉大量先前技術。
    """
    t = (term or "").strip()
    if not t or not t.isascii():
        return []
    words = t.split()
    last = words[-1].lower()
    out = [t]
    for suffix, alts in _VARIANT_MAP.items():
        if last.endswith(suffix):
            base = last[: len(last) - len(suffix)]
            for a in alts:
                out.append(" ".join(words[:-1] + [base + a]))
            break
    # 去重保序
    seen: set[str] = set()
    return [x for x in out if not (x.lower() in seen or seen.add(x.lower()))]


def derive_concepts(description: str, plan: dict | None = None) -> dict:
    """從「文件本身」推導驗證用的核心概念詞。

    關鍵：概念詞必須獨立於檢索詞，否則驗證會自我循環
    （用 A 查、再檢查有沒有 A，必定通過）。

    這裡的來源是中英對照 —— 那是團隊自己寫下的正式術語，
    與「工具猜的檢索詞」是不同的資訊。

    回傳 {"primary": [...], "primary_from_document": [...],
          "context": [...]}
      primary : 技術或裝置的詞幹，文獻必須命中其一
      primary_from_document : 來自文件中英對照的技術詞幹
          （**唯一可作為驗證依據的層級**，理由見 score_relevance）
      context : 疾病或應用場域詞，供檢視與診斷
    """
    text = description or ""
    plan = plan or {}
    pairs = extract_bilingual_pairs(text)

    from_doc: list[str] = []      # 團隊自己寫的（可信）
    from_tool: list[str] = []     # 工具猜的（不可作為驗證依據）
    context: list[str] = []

    for p in pairs:
        en = (p.get("en") or "").strip()
        zh = p.get("zh") or ""
        if not en:
            continue
        if is_condition_zh(zh):
            context.append(en.lower())
        else:
            from_doc.append(_stem(en))

    # 技術詞表的詞：工具從內建表推出的，屬不可信層級。
    #
    # 為什麼不能拿它當驗證依據（實測教訓）：
    # 智慧尿布案的技術詞是 "volatile organic compounds"，
    # 拿它當標準去驗證同一批結果，必定 100% 命中 ——
    # 但撈回的是「植物的揮發性有機化合物」「白酒香氣分析」，
    # 跟尿布毫無關係。通用化學／工程詞語在任何同主題文獻都會出現，
    # 沒有鑑別力。用它驗證等於沒驗證。
    for t in (plan.get("tech") or []):
        s = _stem(t)
        if s.isascii() and len(s) >= 5 and s not in from_doc:
            from_tool.append(s)
    if plan.get("condition"):
        context.append(plan["condition"].lower())

    # 縮寫只當 context：例如 BCG 同時是卡介苗，誤判風險高
    for p in pairs:
        ab = (p.get("abbr") or "").strip().lower()
        if len(ab) >= 2 and ab not in context:
            context.append(ab)

    def _uniq(xs):
        seen: set[str] = set()
        return [x for x in xs if x and not (x in seen or seen.add(x))]

    doc_u, tool_u = _uniq(from_doc), _uniq(from_tool)
    return {
        "primary": _uniq(doc_u + tool_u)[:6],
        "primary_from_document": doc_u[:6],
        "primary_from_tool": tool_u[:6],
        "context": _uniq(context)[:6],
    }


def _phrase_hit(blob: str, phrase: str, min_tokens: int = 2) -> bool:
    """片語是否命中 blob。

    先試整串包含；失敗則退回詞元級比對（文獻含 >= min_tokens 個詞元）。
    為什麼需要詞元級：疾病詞常是長片語
    （catheter-associated urinary tract infection），
    文獻標題只會出現其中幾個字，整串包含會永遠比對不到。
    """
    phrase = phrase.lower().strip()
    if not phrase:
        return False
    if phrase in blob:
        return True
    tokens = [t for t in re.split(r"[\s\-/,()]+", phrase)
              if len(t) >= 5 and t.isalpha()]
    if len(tokens) < 2:
        return bool(tokens) and tokens[0] in blob
    need = min(min_tokens, len(tokens))
    return sum(1 for t in tokens if t in blob) >= need


def score_relevance(papers: list[dict], concepts: dict) -> dict:
    """評估撈回文獻與產品的相關性。

    判準分兩個維度（缺一不可，因為單看一個維度會被騙）：
      技術維度（primary）: 文獻是否談到產品的技術核心
      應用維度（context）: 文獻是否談到產品的疾病或應用場域

    為什麼需要兩個維度（實測教訓）：
      只驗技術 → 智慧尿布案例的技術詞 "volatile organic compounds"
      命中率 100%，但撈回的是「植物的揮發性有機化合物」這類
      化學文獻，跟尿布毫無關係。VOC 是通用化學詞，鑑別力不足。
      加上應用維度後，同一批文獻的應用命中率是 0%，訊號正確指出
      「技術詞對了但應用場景不對」。

    為什麼 primary 為空時不判定 fail（實測教訓）：
      膀胱餘尿偵測儀與氣管內管固定裝置沒有中英對照，
      推不出獨立概念詞。若因此判定 fail，會把「膀胱容積連續監測」
      「Endotracheal Tube Holder」這些完全正確的文獻貼上
      「可能不是您的產品」的警告 —— 比不驗證更糟。
      這種情況改回 "unverified"，誠實說明無法自動驗證。

    判定表：
      primary 有值且命中 >=35%：
        context 為空或命中 >=5%  → pass
        context 有值但命中 <5%   → weak（技術對、應用無關）
      命中 15-35%  → weak
      命中 <15%    → fail
      primary 為空 → unverified

    回傳 {"rate":..., "verdict":"pass|weak|fail|unverified|unknown", ...}
    """
    primary = [c.lower() for c in
               (concepts.get("primary_from_document") or [])]
    context = [c.lower() for c in (concepts.get("context") or [])]
    total = len(papers or [])

    # 驗證依據只採「文件中英對照」層級的概念詞。
    #
    # 為什麼不能用工具自己推的詞當依據（實測教訓）：
    # 智慧尿布案的技術詞 "volatile organic compounds" 會被拿來
    # 驗證同一批結果 → 必定 100% 命中 → 看似通過，但撈回的是
    # 「植物揮發性有機化合物」「白酒香氣分析」。通用化學詞出現在
    # 任何同主題文獻，沒有鑑別力，用它驗證等於沒驗證（自我循環）。
    #
    # 只有團隊自己寫下的中英對照，才是獨立於檢索詞的資訊。
    # 這是「能不能自動驗證」的關鍵分水嶺。

    if total < MIN_SAMPLE:
        return {"rate": 0.0, "verdict": "unknown", "hits": 0, "total": total,
                "primary": primary, "context": context, "examples": [],
                "note": f"樣本不足（{total} < {MIN_SAMPLE} 篇），不做判定"}

    # 非英文檢索詞：實測把中文原文送去查英文資料庫會撈回
    # 「李晨風電影作品」「健康報」這類完全無關的結果，
    # 而且報告看起來正常。這是必須擋下的情況。
    non_ascii = [p.get("title", "") for p in papers
                 if not any("a" <= c.lower() <= "z" for c in (p.get("title") or "")
                            if c.isalpha())]
    if len(non_ascii) > total * 0.5:
        return {
            "rate": 0.0, "verdict": "fail", "hits": 0, "total": total,
            "context_rate": 0.0, "primary": primary, "context": context,
            "examples": non_ascii[:3],
            "note": "撈回的文獻多為非英文標題，顯示檢索詞可能不是"
                    "資料庫使用的英文術語。請改以英文技術名稱檢索。",
        }

    if not primary:
        # 無法自動驗證時，附上樣本標題讓使用者能自己判斷。
        # 這是誠實的做法：不假裝驗證過，也不給誤導性的通過標記。
        return {
            "rate": 0.0, "verdict": "unverified", "hits": 0, "total": total,
            "context_rate": 0.0, "primary": primary, "context": context,
            "tool_terms": (concepts.get("primary_from_tool") or []),
            "examples": [(p.get("title") or "") for p in papers[:5]],
            "note": "文件中沒有中英對照，無法自動驗證檢索結果。"
                    "請人工檢視下列標題是否與您的產品相關；"
                    "若不符，請手動指定英文技術名稱。"
                    "（在計畫書補上技術名稱的英文全稱，即可自動驗證）",
        }

    hits, ctx_hits, examples = 0, 0, []
    for p in papers:
        blob = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
        if any(c in blob for c in primary):
            hits += 1
            if len(examples) < 3:
                examples.append(p.get("title") or "")
        if any(_phrase_hit(blob, c) for c in context):
            ctx_hits += 1

    rate = hits / total
    ctx_rate = (ctx_hits / total) if context else 0.0

    # 判定只看技術維度。
    #
    # 為什麼應用場域不參與判定（實測教訓）：
    # 「Dense and Query-Set Prediction for J-Peak Detection in
    # Pillow-Based Ballistocardiography」是最接近心衰竭團隊的先前技術，
    # 但標題完全沒提 heart failure。若要求應用場域命中，這篇會被
    # 貼上「相關性偏低」的錯誤標記。
    # 先前技術的本質是「技術上相近」，不是「疾病相同」。
    #
    # 應用場域改為獨立的診斷數字呈現，讓使用者自己解讀：
    # 「技術詞 100%、應用場域 0%」本身就是有用的情報 ——
    # 表示這些文獻技術相同但沒做你的應用場景。
    if rate >= PASS_RATE:
        verdict = "pass"
    elif rate >= WEAK_RATE:
        verdict = "weak"
    else:
        verdict = "fail"

    return {
        "rate": round(rate, 3),
        "context_rate": round(ctx_rate, 3),
        "verdict": verdict,
        "hits": hits,
        "total": total,
        "primary": primary,
        "context": context,
        "examples": examples,
    }


def build_candidates(description: str, plan: dict) -> list[dict]:
    """建立檢索候選清單（信心由高到低）。

    為何要「候選清單」而不是單一檢索詞：
    檢索詞錯了整份報告就錯了，而工具無法百分之百猜對。
    與其猜一次就送出，不如準備多個候選、逐一驗證，
    驗證通過才採用 —— 這就是「查了才知道對不對」的具體作法。
    """
    out: list[dict] = []
    plan = plan or {}
    text = description or ""

    def add(q: str, source: str, kind: str = "term"):
        q = (q or "").strip()
        if not q or not q.isascii() or len(q) < 4:
            return
        if any(q.lower() == o["query"].lower() for o in out):
            return
        out.append({"query": q, "source": source, "kind": kind})

    # 1. 文件中的中英對照（最可靠）
    for p in extract_bilingual_pairs(text):
        if p.get("derived") or is_condition_zh(p.get("zh") or ""):
            continue
        add(p.get("en", ""), "文件中英對照")

    # 1b. LLM 建議的候選（use_llm=True 時由 query_terms 帶入）
    #
    # 放在中英對照之後、對照表之前：LLM 比對照表更能抓到創新技術詞，
    # 但仍須實測驗證 —— 實測 LLM 候選品質落差極大
    # （'Ballistocardiograph' 71% vs 'Heart rate variability' 0%），
    # 所以只是「多幾個候選」，由後續驗證決定採用哪個。
    for c in (plan.get("llm_candidates") or []):
        add(c.get("query") or "", c.get("source") or "LLM 建議")

    # 2. 裝置檢索詞（代表「產品是什麼」，比「怎麼運作」更接近產品身分）
    add(plan.get("device") or "", "裝置檢索詞")

    # 3. 技術詞表
    #
    # 為什麼排在裝置詞之後：技術詞可能是通用學科詞
    # （例：volatile organic compounds），單獨查會撈到
    # 化學領域的泛論文獻；裝置詞較能鎖定產品本身。
    for t in (plan.get("tech") or []):
        add(t, "技術詞表")

    # 4. 技術詞 + 疾病（限定應用場域的版本）
    cond = (plan.get("condition") or "").strip()
    if cond:
        for t in (plan.get("tech") or [])[:2]:
            add(f"{t} {cond}", f"{t} ＋ 疾病詞")

    # 5. 裝置詞 + 疾病詞：沒有中英對照時最有效的組合。
    #    實測：膀胱餘尿偵測儀單用 "bladder scanner ultrasound" 撈回
    #    摻雜錐狀光束 CT 的文獻；加上應用場域詞能把範圍收緊。
    if cond and plan.get("device"):
        add(f"{plan['device']} {cond}", "裝置詞 ＋ 疾病詞")

    # 6. 裝置詞的詞形變體（應對資料庫詞形處理不一致）
    for v in generate_variants(plan.get("device") or ""):
        add(v, "裝置詞變體")

    return out


def search_verified(description: str,
                    plan: dict,
                    search_fn,
                    limit: int = 15,
                    deep: bool = True,
                    progress=None) -> dict:
    """多候選檢索 + 自動驗證 + 取最佳結果。

    流程：
      1. 依序嘗試候選檢索詞（最具體先）
      2. 每次檢索後計算相關性
      3. 通過驗證即採用；並（deep 模式）聯集詞形變體補足覆蓋率
      4. 全部失敗則回報最強者並附明確警告

    search_fn(query, limit) 由呼叫端提供（避免模組相依）。
    """
    concepts = derive_concepts(description, plan)
    candidates = build_candidates(description, plan)
    attempts: list[dict] = []

    if not candidates:
        return {"papers": [], "chosen": "", "relevance": None,
                "attempts": [], "concepts": concepts,
                "warning": "無法從產品說明推導出可用的英文檢索詞，"
                           "請手動指定技術名稱或 FDA 官方品名。"}

    # 候選數量：LLM 模式下候選變多，放寬到 6 個（每次檢索約 2-4 秒）。
    # 仍設上限，避免公開網頁的單次查詢耗時過長。
    limit_n = 6 if plan.get("llm_used") else 4
    chosen_cands = candidates[:limit_n]

    best = None
    pool: list[dict] = []      # 所有候選的結果（供事後改選）
    for i, cand in enumerate(chosen_cands):
        if progress:
            progress(i + 1, len(chosen_cands), cand["query"])
        try:
            res = search_fn(cand["query"], limit)
            papers = res.get("papers", []) if isinstance(res, dict) else res
        except Exception:
            continue
        sc = score_relevance(papers, concepts)
        entry = {"papers": papers, "query": cand["query"],
                 "source": cand["source"], "relevance": sc}
        pool.append(entry)
        attempts.append({"query": cand["query"], "source": cand["source"],
                         "relevance": sc, "count": len(papers)})
        if best is None or sc["rate"] > best["relevance"]["rate"]:
            best = entry
        # 通過驗證即停（省時間）。候選順序已是信心由高到低，
        # 第一個通過的就是最可信的那個。
        if sc["verdict"] == "pass":
            break
        time.sleep(0.5)

    # 無法自動驗證時，改用「裝置檢索詞」的結果。
    #
    # 為什麼（實測）：智慧尿布案的候選第一個是技術詞
    # "volatile organic compounds"（命中化學泛論文獻），
    # 裝置詞 "incontinence pad" 才是代表產品的詞。
    # 在所有候選都無法驗證的情況下，產品身分詞比運作方式詞更可靠。
    if best and best["relevance"]["verdict"] == "unverified":
        for e in pool:
            if e["source"] == "裝置檢索詞":
                best = e
                break

    if best is None:
        return {"papers": [], "chosen": "", "relevance": None,
                "attempts": attempts, "concepts": concepts,
                "warning": "所有候選檢索詞皆查詢失敗，請稍後重試或手動指定。"}

    # deep：聯集詞形變體，補足「同一技術、不同詞形撈到不同文獻」的缺口
    extra_note = ""
    if deep and best["relevance"]["verdict"] in ("pass", "weak"):
        variants = generate_variants(best["query"])[1:3]
        merged = list(best["papers"])
        seen_titles = {(p.get("title") or "").strip().lower() for p in merged}
        added = 0
        for v in variants:
            try:
                res = search_fn(v, limit)
                for p in (res.get("papers", []) if isinstance(res, dict) else res):
                    key = (p.get("title") or "").strip().lower()
                    if key and key not in seen_titles:
                        seen_titles.add(key)
                        merged.append(p)
                        added += 1
            except Exception:
                continue
            time.sleep(0.5)
        if added:
            best["papers"] = merged
            best["relevance"] = score_relevance(merged, concepts)
            extra_note = (f"另以詞形變體（{'、'.join(variants)}）補得 "
                          f"{added} 篇，聯集後共 {len(merged)} 篇")

    warning = ""
    r = best["relevance"]
    v = r["verdict"]
    if v == "fail":
        warning = (
            f"檢索結果與產品核心概念不符（技術命中率 {r['rate']:.0%}，"
            f"判定門檻 {PASS_RATE:.0%}）。**這些文獻可能不是您產品的先前技術。** "
            f"請於「進階設定」手動指定技術名稱。"
        )
    elif v == "weak":
        warning = (
            f"檢索結果相關性偏低（技術命中率 {r['rate']:.0%}），"
            "建議人工檢視標題清單，或於「進階設定」手動指定更精確的技術名稱。"
        )
    elif v == "unverified":
        warning = r.get("note", "")
    elif v == "unknown":
        warning = r.get("note", "")

    # 應用場域診斷：技術命中但應用場域未命中，是有價值的情報。
    # 它告訴團隊「這些文獻技術相同，但沒做你的應用場景」。
    r_final = best["relevance"]
    if (r_final["verdict"] in ("pass", "weak") and r_final.get("context")
            and r_final.get("context_rate", 0) < 0.05):
        extra_note = (extra_note + "｜" if extra_note else "") + (
            f"技術相關性 {r_final['rate']:.0%}，但文獻較少提及您的應用場域"
            f"（{'、'.join(r_final['context'][:3])}）。"
            "代表這些是技術層面的先前技術，請確認與您的應用是否相關。"
        )

    return {
        "papers": best["papers"],
        "chosen": best["query"],
        "chosen_source": best["source"],
        "relevance": best["relevance"],
        "attempts": attempts,
        "concepts": concepts,
        "note": extra_note,
        "warning": warning,
    }
