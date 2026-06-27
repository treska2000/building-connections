"""R3 · Source quality — a purely QUALITATIVE check via enrichment (arXiv + OpenAlex);
without enrichment the whole requirement is PENDING. The formal source counter lives in R1.
DEFAULT aggregation (calibratable): red per-source = unresolvable / retracted; red per-term =
any red source or ALL sources off-field; per-term pass = no red ∧ sanity ∧ groups ≥ min_groups;
config = share of passing terms ≥ min_share_attested. Positive signals (S3/S5/S7) are context.

R3 · Достоверность источников — чисто КАЧЕСТВЕННАЯ проверка через обогащение (arXiv +
OpenAlex); без обогащения всё требование = PENDING. Формальный счётчик источников — в R1.
Агрегация-DEFAULT (калибруемо): red per-source = не резолвится / retracted; red per-term =
любой red-источник или ВСЕ источники из чужого поля; pass per-term = нет red ∧ sanity ∧
groups ≥ min_groups; config = share проходящих ≥ min_share_attested. Сигналы S3/S5/S7 — контекст.
"""
from __future__ import annotations
import re
from ..core.loader import get_sources
from ..core import textutil as TU
from ..enrich import arxiv_id_from_url, PEER_REVIEW_HINT

ARXIV = re.compile(r"arxiv\.org/(abs|pdf)/\d{4}\.\d{4,5}", re.I)

# deterministic mapping: area/field → allowed arXiv archives (extensible)
FIELD_TO_ARXIV_PREFIX = {
    "computer science": {"cs", "stat.ml", "eess"},
    "artificial intelligence": {"cs", "stat.ml"},
    "machine learning": {"cs", "stat.ml"},
    "mathematics": {"math", "math-ph", "stat"},
    "physics": {"physics", "astro-ph", "cond-mat", "hep", "gr-qc", "quant-ph", "nucl", "math-ph"},
    "biology": {"q-bio"},
    "economics": {"econ", "q-fin"},
    "statistics": {"stat", "math"},
    "electrical engineering": {"eess", "cs"},
}


def _allowed_prefixes(cfg) -> set[str] | None:
    """Allowed arXiv archives for the config's specialty (field/subfield/area).

    In: cfg. Out: set of prefixes | None (unknown field → unknown, not red).
    Допустимые архивы arXiv по specialty конфига (field/subfield/area).
    In: cfg. Out: set префиксов | None (поле неизвестно → unknown, не red).
    """
    sp = cfg.get("specialty") or {}
    if isinstance(sp, str):
        sp = {"area": sp}
    for key in ("field", "subfield", "area"):
        v = (sp.get(key) or "").lower().strip()
        if v in FIELD_TO_ARXIV_PREFIX:
            return FIELD_TO_ARXIV_PREFIX[v]
    return None


def _field_match(primary_category: str | None, allowed: set[str] | None) -> bool | None:
    """S4: whether the source sits in the expected field.

    In: arXiv primary_category, allowed. Out: bool | None (no data).
    S4: своя ли область у источника.
    In: arXiv primary_category, allowed. Out: bool | None (нет данных).
    """
    if not primary_category or allowed is None:
        return None
    arch = primary_category.split(".")[0].lower()
    return arch in allowed or primary_category.lower() in allowed


def _independent_groups(works: list[dict]) -> int | None:
    """S6: number of independent author groups (union-find: shared author OR institution =
    one group).

    In: works (OpenAlex records). Out: int | None (no data).
    S6: число независимых авторских групп (union-find: общий автор ИЛИ институт = одна группа).
    In: works (записи OpenAlex). Out: int | None (нет данных).
    """
    known = [w for w in works if w and w.get("found")]
    if not known:
        return None
    parent = list(range(len(known)))

    def find(x):
        """Union-find root with path compression. / Корень union-find со сжатием путей."""
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        """Merge two union-find sets. / Объединить два множества union-find."""
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(known)):
        for j in range(i + 1, len(known)):
            ai = set(known[i]["authors"]) | set(known[i]["institutions"])
            aj = set(known[j]["authors"]) | set(known[j]["institutions"])
            if ai & aj:
                union(i, j)
    return len({find(i) for i in range(len(known))})


def _metric(value, ok, det, requires=None, gameable=False, note=None, counter=None, examples=None):
    """Build the unified metric-contract dict.

    In: contract fields. Out: dict.
    Конструктор словаря метрики единого контракта.
    In: поля контракта. Out: dict.
    """
    m = {"value": value, "pass": ok, "deterministic": det, "requires": requires,
         "gameable": gameable}
    if note:
        m["note"] = note
    if counter:
        m["counter"] = counter
    if examples:
        m["examples"] = examples
    return m


def run(cfg, members, term_tags, thr, enrich=None) -> dict:
    """Compute the qualitative source-quality metrics; all require enrichment — without it
    every metric is PENDING (pass = None).

    In: cfg, members, term_tags, thr (R3 thresholds), enrich (Enrichment | None).
    Out: dict {requirement, metrics, pass}.
    Считает качественные метрики достоверности; все требуют обогащения — без него все
    метрики PENDING (pass = None).
    In: cfg, members, term_tags, thr (пороги R3), enrich (Enrichment | None).
    Out: dict {requirement, metrics, pass}.
    """
    terms = cfg.get("terms", [])
    metrics = {}

    if enrich is None or not enrich.sources_available():
        metrics["source_sanity_check"] = _metric(
            None, None, det=True, requires="sources (arXiv attestation: термин в абстракте)",
            gameable=True, counter="сила связи R6")
        metrics["source_attestation"] = _metric(
            None, None, det=True, requires="arXiv + OpenAlex",
            note="resolves · retracted · peer_review · field_match · citations · "
                 "independent_groups · corpus_frequency")
        # no quality data without enrichment: the requirement is honestly PENDING
        return {"requirement": "R3 · Источники", "metrics": metrics,
                "pass": None}

    # ── enrichment available ─────────────────────────────────────────────
    det = enrich.deterministic()  # live API → False until the snapshot is pinned
    term_srcs = {t["name"]: [arxiv_id_from_url(s["url"]) for s in get_sources(t)]
                 for t in terms if t.get("name")}
    all_ids = sorted({i for ids in term_srcs.values() for i in ids if i})
    metas = enrich.arxiv.metas(all_ids)
    works = enrich.openalex.works_by_arxiv(all_ids) if enrich.openalex_available() else {}
    allowed = _allowed_prefixes(cfg)
    min_groups = thr.get("min_independent_groups", 3)
    hi_cite = thr.get("citations_hi", 100)

    sanity_pass = 0
    sanity_known = 0
    attested = 0
    attest_known = 0
    reds = []
    per_term = {}
    for name, ids in term_srcs.items():
        ids = [i for i in ids if i]
        if not ids:
            per_term[name] = {"verdict": "red", "why": "нет arXiv-источников"}
            reds.append({"term": name, "why": "нет arXiv-источников"})
            attest_known += 1
            continue
        ms = [metas.get(i) for i in ids]
        ws = [works.get(i) for i in ids] if works else []
        # S1 (red: fake link)
        s1_known = [m for m in ms if m is not None]
        red = any(m.get("exists") is False for m in s1_known)
        why = "фейк-ссылка (не резолвится на arXiv)" if red else None
        # S2 (red: retraction); missing OpenAlex record != retracted
        if not red and any(w and w.get("found") and w.get("is_retracted") for w in ws):
            red, why = True, "источник ретрагирован"
        # S4 (red only when ALL sources are off-field)
        fm = [_field_match((m or {}).get("primary_category"), allowed) for m in s1_known]
        if not red and fm and all(v is False for v in fm):
            red, why = True, "все источники из чужого поля"
        # faithfulness / sanity: the term occurs in title+abstract of >= K of its
        # sources (K = min_sane_sources_per_term; raise it for hand-picked top-N sources)
        texts = [(m.get("title", "") + " " + m.get("abstract", ""))
                 for m in s1_known if m.get("exists")]
        k_sane = thr.get("min_sane_sources_per_term", 1)
        n_matched = sum(1 for tx in texts if TU.term_in_text(name, tx))
        sane = (n_matched >= k_sane) if texts else None
        if sane is not None:
            sanity_known += 1
            if sane:
                sanity_pass += 1
        # S6 independence
        groups = _independent_groups(ws) if ws else None
        # positive context signals: S3 / S5 / S7
        peer = any((m.get("journal_ref") or
                    (m.get("doi") and "48550" not in m.get("doi", "")) or
                    PEER_REVIEW_HINT.search(m.get("comment", "")))
                   for m in s1_known if m.get("exists"))
        cites = max((w.get("cited_by_count", 0) for w in ws if w and w.get("found")),
                    default=None) if ws else None
        freq = enrich.openalex.term_count(name) if enrich.openalex_available() else None

        ok = (not red) and bool(sane) and (groups is None or groups >= min_groups)
        if s1_known:
            attest_known += 1
            if ok:
                attested += 1
        if red:
            reds.append({"term": name, "why": why})
        per_term[name] = {"verdict": "red" if red else ("green" if ok else "yellow"),
                          "why": why, "sane": sane, "groups": groups, "peer_review": peer,
                          "max_citations": cites, "corpus_freq": freq}

    share_sane = round(sanity_pass / sanity_known, 3) if sanity_known else None
    share_attested = round(attested / attest_known, 3) if attest_known else None
    net_note = "; ".join((enrich.arxiv.errors + getattr(enrich.openalex, "errors", []))[:3]) or None

    metrics["source_sanity_check"] = _metric(
        share_sane,
        None if share_sane is None else share_sane >= thr.get("min_share_sane", 0.9),
        det=det, gameable=True, counter="сила связи R6",
        note=f"термин в title+abstract ≥{thr.get('min_sane_sources_per_term', 1)} "
             f"источника(ов); known={sanity_known}/{len(term_srcs)}"
             + (f" · WARNING network: {net_note}" if net_note else ""))
    metrics["source_attestation"] = _metric(
        {"share_attested": share_attested, "reds": reds[:8]},
        None if share_attested is None else (
            not reds and share_attested >= thr.get("min_share_attested", 0.9)),
        det=det, gameable=False,
        note=f"reds={len(reds)} · min_groups={min_groups} · cites_hi={hi_cite} "
             f"· агрегация-DEFAULT (калибруемо)" + (" · live API (недетерм. до пина снапшота)" if not det else ""))
    metrics["per_term"] = _metric(per_term, None, det=det, note="справочно, не гейт")

    gates = [metrics["source_sanity_check"]["pass"],
             metrics["source_attestation"]["pass"]]
    known = [g for g in gates if g is not None]
    passed = all(known) if known else None
    return {"requirement": "R3 · Источники", "metrics": metrics, "pass": passed}
