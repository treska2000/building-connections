"""R4 · Достоверность источников: source_count — детерминированный анкор;
S1–S7 + faithfulness — через обогащение (arXiv + OpenAlex, спека
source_signals_deterministic_2026-06-07). Агрегация-DEFAULT (калибруемо):
red per-source = не резолвится / retracted; red per-term = любой red-источник или ВСЕ
источники из чужого поля; pass per-term = нет red ∧ sanity ∧ groups ≥ min_groups;
конфиг = share проходящих ≥ min_share_attested. Позитивные сигналы (S3/S5/S7) — контекст.

R4 · Source quality: source_count is the deterministic anchor; S1–S7 + faithfulness
run via enrichment (arXiv + OpenAlex, spec source_signals_deterministic_2026-06-07).
DEFAULT aggregation (calibratable): red per-source = unresolvable / retracted;
red per-term = any red source or ALL sources off-field; per-term pass = no red ∧ sanity ∧
groups ≥ min_groups; config = share of passing terms ≥ min_share_attested.
Positive signals (S3/S5/S7) are context, not gates.
"""
from __future__ import annotations
import re
from .loader import get_sources
from . import textutil as TU
from .enrich import arxiv_id_from_url, PEER_REVIEW_HINT

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
    """Допустимые архивы arXiv по specialty конфига (field/subfield/area).
    Вход: cfg. Выход: set префиксов | None (поле неизвестно → unknown, не red).
    Allowed arXiv archives per the config's specialty (field/subfield/area).
    In: cfg. Out: set of prefixes | None (unknown field → unknown, not red)."""
    sp = cfg.get("specialty") or {}
    if isinstance(sp, str):
        sp = {"area": sp}
    for key in ("field", "subfield", "area"):
        v = (sp.get(key) or "").lower().strip()
        if v in FIELD_TO_ARXIV_PREFIX:
            return FIELD_TO_ARXIV_PREFIX[v]
    return None


def _field_match(primary_category: str | None, allowed: set[str] | None) -> bool | None:
    """S4: своя ли область у источника. Вход: primary_category arXiv, allowed.
    Выход: bool | None (нет данных).
    S4: is the source in the expected field. In: arXiv primary_category, allowed.
    Out: bool | None (no data)."""
    if not primary_category or allowed is None:
        return None
    arch = primary_category.split(".")[0].lower()
    return arch in allowed or primary_category.lower() in allowed


def _independent_groups(works: list[dict]) -> int | None:
    """S6: число независимых авторских групп (union-find: общий автор ИЛИ институт =
    одна группа). Вход: works (записи OpenAlex). Выход: int | None (нет данных).
    S6: number of independent author groups (union-find: shared author OR institution =
    one group). In: works (OpenAlex records). Out: int | None (no data)."""
    known = [w for w in works if w and w.get("found")]
    if not known:
        return None
    parent = list(range(len(known)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
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
    """Конструктор словаря метрики единого контракта. Вход: поля контракта. Выход: dict.
    Constructor of the unified metric-contract dict. In: contract fields. Out: dict."""
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
    """Считает метрики достоверности источников; без обогащения — только source_count.
    Вход: cfg, members, term_tags, thr (пороги R4), enrich (Enrichment | None).
    Выход: dict {requirement, metrics, pass}.
    Computes source-quality metrics; without enrichment only source_count is evaluated.
    In: cfg, members, term_tags, thr (R4 thresholds), enrich (Enrichment | None).
    Out: dict {requirement, metrics, pass}."""
    terms = cfg.get("terms", [])
    n = len(terms)
    counts = []
    with_sources = 0
    with_arxiv = 0
    for t in terms:
        src = get_sources(t)
        counts.append(len(src))
        if src:
            with_sources += 1
        if any(ARXIV.search(s["url"]) for s in src):
            with_arxiv += 1
    share_3plus = round(sum(1 for c in counts if c >= 3) / n, 3) if n else 0.0

    metrics = {
        "source_count": _metric(
            {"share_terms_with_sources": round(with_sources / n, 3) if n else 0,
             "share_3plus": share_3plus, "with_arxiv": with_arxiv},
            share_3plus >= thr.get("min_share_3sources", 0.9),
            det=True, gameable=True,
            counter="independent_source_groups + attestation"),
    }

    if enrich is None or not enrich.sources_available():
        metrics["source_sanity_check"] = _metric(
            None, None, det=True, requires="sources (arXiv attestation: термин в абстракте)",
            gameable=True, counter="сила связи R6")
        metrics["source_attestation"] = _metric(
            None, None, det=True, requires="arXiv + OpenAlex",
            note="resolves · retracted · peer_review · field_match · citations · "
                 "independent_groups · corpus_frequency")
        return {"requirement": "R4 · Достоверность источников", "metrics": metrics,
                "pass": metrics["source_count"]["pass"]}

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
        # faithfulness / sanity: the term occurs in title+abstract of >=1 source
        texts = [(m.get("title", "") + " " + m.get("abstract", ""))
                 for m in s1_known if m.get("exists")]
        sane = any(TU.term_in_text(name, tx) for tx in texts) if texts else None
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
        note=f"термин в title+abstract ≥1 источника; known={sanity_known}/{len(term_srcs)}"
             + (f" · WARNING network: {net_note}" if net_note else ""))
    metrics["source_attestation"] = _metric(
        {"share_attested": share_attested, "reds": reds[:8]},
        None if share_attested is None else (
            not reds and share_attested >= thr.get("min_share_attested", 0.9)),
        det=det, gameable=False,
        note=f"reds={len(reds)} · min_groups={min_groups} · cites_hi={hi_cite} "
             f"· агрегация-DEFAULT (калибруемо)" + (" · live API (недетерм. до пина снапшота)" if not det else ""))
    metrics["per_term"] = _metric(per_term, None, det=det, note="справочно, не гейт")

    gates = [metrics["source_count"]["pass"], metrics["source_sanity_check"]["pass"],
             metrics["source_attestation"]["pass"]]
    known = [g for g in gates if g is not None]
    passed = all(known) if known else None
    return {"requirement": "R4 · Достоверность источников", "metrics": metrics, "pass": passed}
