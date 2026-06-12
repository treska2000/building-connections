"""R2 · Решаемость (Solvability) — OR-Tools. mode-1/2 полные; mode-3 — на decoy_for из входа
(margin-вывод обманок через эмбеддер остаётся requires). Спека: req2_solvability_spec_2026-06-07.md.

Вердикт R2 = pass_base (mode-1); mode-2/3 — углубления, репортятся, но не валят гейт
до калибровки τ₂/τ₃ (acceptance: min_vpy_mode2/3 = null → проверяется только exists).
"""
from __future__ import annotations
from . import solver as S
from .loader import get_decoys


def _mode1_eligible(members, term_tags) -> int:
    elig = 0
    for a, terms in members.items():
        solo = sum(1 for t in terms if len(term_tags[t]) == 1)
        if solo >= 4:
            elig += 1
    return elig


def _vpy_pass(samp, min_vpy):
    """exists обязателен; VPY-порог — только если задан (иначе калибровка TBD)."""
    if not samp["exists"]:
        return False
    if min_vpy is None:
        return True
    return (samp["vpy"] or 0) >= min_vpy


def run(cfg, members, term_tags, thr) -> dict:
    metrics = {}
    n_samples = thr.get("n_samples", 400)
    seed = thr.get("seed", 42)

    if not S.HAVE_ORTOOLS:
        for k in ("exists_valid_puzzle_mode1", "exists_valid_puzzle_mode2",
                  "exists_valid_puzzle_mode3"):
            metrics[k] = {"value": None, "pass": None, "deterministic": True,
                          "requires": "ortools", "gameable": False}

    # mode-1 (чистый пазл)
    elig1 = _mode1_eligible(members, term_tags)
    metrics["is_eligible_mode1"] = {"value": elig1, "pass": elig1 >= 4,
                                    "deterministic": True, "requires": None,
                                    "gameable": True, "counter": "R5 grounding термина"}
    samp = S.sample_valid_puzzles(members, term_tags, n_samples=n_samples,
                                  seed=seed) if S.HAVE_ORTOOLS else None
    if samp:
        metrics["exists_valid_puzzle_mode1"] = {"value": samp["exists"], "pass": samp["exists"],
                                                "deterministic": True, "requires": None, "gameable": False}
        metrics["good_puzzle_yield_mode1"] = {"value": samp["vpy"],
                                              "pass": (samp["vpy"] or 0) >= thr.get("min_vpy", 0.7),
                                              "deterministic": True, "requires": None, "gameable": False,
                                              "note": f"effective={samp['effective']} ceiling={samp['ceiling']}"}

    # mode-2 (пересечения как ловушки)
    pairs = S.mode2_cooccurrence_pairs(members)
    metrics["is_eligible_mode2"] = {"value": pairs, "pass": pairs >= 1,
                                    "deterministic": True, "requires": None,
                                    "gameable": True, "counter": "R5 grounding термина"}
    samp2 = None
    if S.HAVE_ORTOOLS:
        samp2 = S.sample_valid_puzzles_mode2(members, term_tags, n_samples=n_samples, seed=seed)
        v2 = thr.get("min_vpy_mode2")
        note2 = f"vpy2={samp2['vpy']} drawn={samp2['samples_drawn']}"
        if samp2["samples_drawn"] == 0:
            note2 += (" · кандидаты не извлекаются: overlap слишком плотный для 16 "
                      "непересекающихся слов (нужно |tag \\ overlap| ≥ 4 вокруг ловушки)")
        metrics["exists_valid_puzzle_mode2"] = {
            "value": samp2["exists"], "pass": _vpy_pass(samp2, v2),
            "deterministic": True, "requires": None, "gameable": False,
            "note": note2 + ("" if v2 is not None else " · τ₂ не задан (калибровка TBD) — гейт по exists")}

    # mode-3 (явные обманки): decoy_for из входа; margin-вывод — requires embedder
    decoy_map = {t["name"]: set(get_decoys(t)) for t in cfg.get("terms", [])
                 if t.get("name") and get_decoys(t)}
    decoy_pool = len(decoy_map)
    metrics["is_eligible_mode3"] = {"value": decoy_pool,
                                    "pass": decoy_pool >= 4 if decoy_pool else None,
                                    "deterministic": True,
                                    "requires": None if decoy_pool else "embedder (margin → decoy_for)",
                                    "gameable": True,
                                    "note": "пул = явные decoy_for_tags; margin-кандидаты добавятся после эмбеддера"}
    if S.HAVE_ORTOOLS and decoy_pool:
        samp3 = S.sample_valid_puzzles_mode3(members, term_tags, decoy_map,
                                             n_samples=n_samples, seed=seed)
        v3 = thr.get("min_vpy_mode3")
        metrics["exists_valid_puzzle_mode3"] = {
            "value": samp3["exists"], "pass": _vpy_pass(samp3, v3),
            "deterministic": True, "requires": None, "gameable": False,
            "note": f"vpy3={samp3['vpy']} drawn={samp3['samples_drawn']} (trap_quality≥1)"
                    + ("" if v3 is not None else " · τ₃ не задан (калибровка TBD) — гейт по exists")}
    elif "exists_valid_puzzle_mode3" not in metrics:
        metrics["exists_valid_puzzle_mode3"] = {"value": None, "pass": None, "deterministic": True,
                                                "requires": "decoy_for во входе ИЛИ embedder (margin)",
                                                "gameable": False}

    # вердикт R2: базовый минимум = mode-1 (предусловие + однозначный пазл)
    m1ok = metrics["is_eligible_mode1"]["pass"] and metrics.get(
        "exists_valid_puzzle_mode1", {}).get("pass")
    return {"requirement": "R2 · Решаемость", "metrics": metrics,
            "pass": bool(m1ok) if m1ok is not None else None,
            "extra": (samp or {})}
