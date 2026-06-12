"""R6 · Сила связи категория↔термин. sim_in / margin / PMI — requires embedder + arXiv-корпус."""
from __future__ import annotations


def run(cfg, members, term_tags, thr, enrich=None) -> dict:
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
