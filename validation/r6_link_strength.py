"""ВЫВЕДЕН ИЗ ПАЙПЛАЙНА (2026-06-12): модуль не подключён в run.py до редизайна
по ревизии 2026-06-11 (source-пространство); код сохранён как референс старой спеки.
OUT OF THE PIPELINE (2026-06-12): not wired into run.py until the redesign per the
2026-06-11 revision (source space); kept as a reference for the old spec.

R6 · Сила связи категория↔термин — LEGACY-плейсхолдер старой спеки
(sim_in / sim_margin / pmi, requires embedder + arXiv-корпус); в required_gates
не входит. Ждёт редизайна по ревизии 2026-06-11: cluster_cohesion + label_fidelity +
NPMI термин↔термин в пространстве абстрактов источников.

R6 · Category↔term link strength — LEGACY placeholder of the old spec
(sim_in / sim_margin / pmi, requires embedder + arXiv corpus); not in required_gates.
Awaits the 2026-06-11 redesign: cluster_cohesion + label_fidelity + term↔term NPMI
in the space of source abstracts.
"""
from __future__ import annotations


def run(cfg, members, term_tags, thr, enrich=None) -> dict:
    """Возвращает pending-метрики R6 (все pass = None до редизайна/эмбеддера).
    Вход: cfg, members, term_tags, thr, enrich. Выход: dict {requirement, metrics, pass=None}.
    Returns pending R6 metrics (all pass = None until the redesign/embedder).
    In: cfg, members, term_tags, thr, enrich. Out: dict {requirement, metrics, pass=None}."""
    metrics = {
        "sim_in": {"value": None, "pass": None, "deterministic": True,
                   "requires": "embedder (Gemini)", "gameable": False,
                   "note": "cos(emb(термин|описание), emb(категория)); эмбеддить ОПИСАНИЕ, не голое имя"},
        "sim_margin": {"value": None, "pass": None, "deterministic": True,
                       "requires": "embedder (Gemini)", "gameable": False,
                       "note": "sim_in − max_{чужая категория} cos; чистый/обманка"},
        "pmi": {"value": None, "pass": None, "deterministic": True,
                "requires": "arXiv-корпус (со-встречаемость)", "gameable": False,
                "note": "log[p(t,c)/(p(t)p(c))]; ортогонален эмбеддингу, ловит накрутку парафразом"},
    }
    if enrich is not None and enrich.embedder_available():
        metrics["sim_in"]["note"] += "  [enrich доступен — реализовать вызовы]"
    return {"requirement": "R6 · Сила связи", "metrics": metrics, "pass": None}
