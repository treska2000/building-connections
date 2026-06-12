"""R1 · Объём: терминов ≥ 16, базовых категорий ≥ 4, дублей нет. Детерминированно.
R1 · Volume: ≥ 16 terms, ≥ 4 base categories, zero duplicates. Deterministic."""
from __future__ import annotations
from . import loader


def run(cfg, members, term_tags, thr) -> dict:
    """Считает метрики объёма по графу принадлежности. Вход: cfg (dict конфига),
    members (tag→set терминов), term_tags (термин→set тегов), thr (пороги R1).
    Выход: dict {requirement, metrics, pass, extra}.
    Computes volume metrics over the membership graph. In: cfg (config dict),
    members (tag→term set), term_tags (term→tag set), thr (R1 thresholds).
    Out: dict {requirement, metrics, pass, extra}."""
    n_terms = len(term_tags)
    n_cats = len(members)
    sizes = {a: len(v) for a, v in members.items()}
    base_cats = [a for a, n in sizes.items()
                 if n >= thr.get("min_tag_size", thr.get("min_axis_size", 4))]
    dd = loader.dedup_report(cfg)

    is_terms_count = n_terms >= thr.get("min_terms", 16)
    is_base = len(base_cats) >= thr.get("min_base_tags", thr.get("min_base_axes", 4))
    is_dedup_ok = (dd["dup_terms"] + dd["dup_edges"]) == 0

    metrics = {
        "is_terms_count_ge_16": {"value": n_terms, "pass": is_terms_count,
                                 "deterministic": True, "requires": None,
                                 "gameable": True, "counter": "дедуп + R4 attestation + R5 grounding"},
        "is_dedup": {"value": dd, "pass": is_dedup_ok,
                     "deterministic": True, "requires": None, "gameable": False},
        "is_base_categories_count_ge_4": {"value": len(base_cats), "pass": is_base,
                                          "deterministic": True, "requires": None,
                                          "gameable": True, "counter": "R4 attestation + R5 grounding"},
    }
    # bonus: 3-term categories form a pool of natural decoys (not a penalty)
    pool3 = [a for a, n in sizes.items() if n == 3]
    metrics["bonus_size3_pool"] = {"value": len(pool3), "pass": None,
                                   "deterministic": True, "requires": None, "gameable": False,
                                   "note": "категории по 3 термина → пул обманок (не штраф)"}
    passed = is_terms_count and is_base and is_dedup_ok
    return {"requirement": "R1 · Объём", "metrics": metrics, "pass": passed,
            "extra": {"n_terms": n_terms, "n_categories": n_cats, "tag_sizes": sizes}}
