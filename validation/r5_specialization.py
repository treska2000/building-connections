"""R5 · Покрытие специализации: specialty_filled — детерминированно;
consistency/concentration — поле термина из OpenAlex topics его источников
(fallback: архив arXiv → поле). area_term_consistency = доля терминов с ожидаемым
полем ≥ τ; area_concentration = модальное поле == ожидаемому ∧ modal_share ≥ τ.

R5 · Specialization coverage: specialty_filled is deterministic;
consistency/concentration derive a term's field from the OpenAlex topics of its
sources (fallback: arXiv archive → field). area_term_consistency = share of terms
with the expected field ≥ τ; area_concentration = modal field == expected ∧
modal_share ≥ τ.
"""
from __future__ import annotations
from collections import Counter
from .loader import get_area, get_sources
from .enrich import arxiv_id_from_url

ARXIV_ARCHIVE_TO_FIELD = {
    "cs": "computer science", "stat": "computer science", "eess": "computer science",
    "math": "mathematics", "math-ph": "physics",
    "physics": "physics", "astro-ph": "physics", "cond-mat": "physics",
    "hep-th": "physics", "hep-ph": "physics", "hep-ex": "physics", "gr-qc": "physics",
    "quant-ph": "physics", "nucl-th": "physics", "nucl-ex": "physics",
    "q-bio": "biology", "econ": "economics", "q-fin": "economics",
}


def _expected_field(cfg) -> str | None:
    """Ожидаемое поле из specialty.field конфига. Вход: cfg. Выход: str | None.
    Expected field from the config's specialty.field. In: cfg. Out: str | None."""
    sp = cfg.get("specialty") or {}
    if isinstance(sp, str):
        # a string specialty carries no field — consistency falls back to the OpenAlex distribution
        return None
    v = (sp.get("field") or "").lower().strip()
    return v or None


def _term_field(ids, metas, works) -> str | None:
    """Поле термина = модальное поле его источников: OpenAlex primary_topic.field,
    fallback — архив arXiv. Вход: ids (arXiv id), metas, works. Выход: str | None.
    A term's field = the modal field of its sources: OpenAlex primary_topic.field,
    falling back to the arXiv archive. In: ids (arXiv ids), metas, works. Out: str | None."""
    fields = []
    for i in ids:
        w = works.get(i) if works else None
        if w and w.get("found") and w.get("field"):
            fields.append(w["field"].lower())
            continue
        m = metas.get(i)
        if m and m.get("exists") and m.get("primary_category"):
            arch = m["primary_category"].split(".")[0].lower()
            f = ARXIV_ARCHIVE_TO_FIELD.get(arch)
            if f:
                fields.append(f)
    if not fields:
        return None
    return Counter(fields).most_common(1)[0][0]


def run(cfg, members, term_tags, thr, enrich=None) -> dict:
    """Считает метрики покрытия специализации; без обогащения — только specialty_filled.
    Вход: cfg, members, term_tags, thr (пороги R5), enrich (Enrichment | None).
    Выход: dict {requirement, metrics, pass}.
    Computes specialization-coverage metrics; without enrichment only specialty_filled.
    In: cfg, members, term_tags, thr (R5 thresholds), enrich (Enrichment | None).
    Out: dict {requirement, metrics, pass}."""
    area = get_area(cfg)
    filled = bool(area and str(area).strip())
    metrics = {
        "specialty_filled": {"value": area, "pass": filled,
                             "deterministic": True, "requires": None, "gameable": True,
                             "counter": "area_term_consistency (OpenAlex)"},
    }

    if enrich is None or not enrich.sources_available():
        metrics["area_term_consistency"] = {
            "value": None, "pass": None, "deterministic": True,
            "requires": "OpenAlex (поле термина ⊆ поля area)",
            "note": "share терминов, чьё поле совпадает с area; pass = share ≥ tau"}
        metrics["area_concentration"] = {
            "value": None, "pass": None, "deterministic": True,
            "requires": "OpenAlex (распределение по полям)",
            "note": "modal_field == area И modal_share ≥ tau"}
        return {"requirement": "R5 · Покрытие специализации", "metrics": metrics,
                "pass": filled}

    det = enrich.deterministic()
    tau = thr.get("area_consistency_tau", 0.70)
    expected = _expected_field(cfg)
    term_srcs = {t["name"]: [i for i in (arxiv_id_from_url(s["url"])
                                         for s in get_sources(t)) if i]
                 for t in cfg.get("terms", []) if t.get("name")}
    all_ids = sorted({i for ids in term_srcs.values() for i in ids})
    metas = enrich.arxiv.metas(all_ids)
    works = enrich.openalex.works_by_arxiv(all_ids) if enrich.openalex_available() else {}

    fields = {}
    for name, ids in term_srcs.items():
        fields[name] = _term_field(ids, metas, works)
    known = {k: v for k, v in fields.items() if v}
    dist = Counter(known.values())

    if known and expected:
        share = round(sum(1 for v in known.values() if v == expected) / len(known), 3)
        cons_pass = share >= tau
    else:
        share, cons_pass = None, None
    if known:
        modal, modal_n = dist.most_common(1)[0]
        modal_share = round(modal_n / len(known), 3)
        conc_pass = (expected is None or modal == expected) and modal_share >= tau
    else:
        modal, modal_share, conc_pass = None, None, None

    metrics["area_term_consistency"] = {
        "value": share, "pass": cons_pass, "deterministic": det, "requires": None,
        "gameable": False,
        "note": f"expected={expected} known={len(known)}/{len(fields)} τ={tau}"
                + ("" if det else " · live API")}
    metrics["area_concentration"] = {
        "value": {"modal_field": modal, "modal_share": modal_share, "dist": dict(dist)},
        "pass": conc_pass, "deterministic": det, "requires": None, "gameable": False,
        "note": f"τ={tau}"}

    gates = [filled, cons_pass, conc_pass]
    known_g = [g for g in gates if g is not None]
    return {"requirement": "R5 · Покрытие специализации", "metrics": metrics,
            "pass": all(known_g) if known_g else None}
