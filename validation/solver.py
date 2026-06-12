"""solver.py — CP-SAT движок решаемости (Требование R2). OR-Tools.

count_partitions(W) — число валидных разбивок 16 слов на монохромные четвёрки.
is_unique_puzzle(W) — ровно одна разбивка. sample_valid_puzzles — VPY mode-1.
mode-2: sample_valid_puzzles_mode2 (W содержит ≥1 термин-ловушку с ≥2 тегами из S).
mode-3: sample_valid_puzzles_mode3 (decoy_for: trap_quality = квадры, монохромные
только через decoy-рёбра). Спека: req2_solvability_spec_2026-06-07.md.
"""
from __future__ import annotations
import random
from itertools import combinations
from math import comb

try:
    from ortools.sat.python import cp_model
    HAVE_ORTOOLS = True
except Exception:  # pragma: no cover
    HAVE_ORTOOLS = False


def _quads(words, members):
    wset = set(words)
    quads = set()
    for tag_members in members.values():
        pool = sorted(wset & tag_members)
        if len(pool) >= 4:
            for c in combinations(pool, 4):
                quads.add(frozenset(c))
    return list(quads)


def count_partitions(words, members, cap=2) -> int:
    assert len(words) % 4 == 0 and len(words) > 0
    quads = _quads(words, members)
    if not quads:
        return 0
    m = cp_model.CpModel()
    pick = {i: m.NewBoolVar(f"q{i}") for i in range(len(quads))}
    m.Add(sum(pick.values()) == len(words) // 4)
    by_word = {}
    for i, q in enumerate(quads):
        for w in q:
            by_word.setdefault(w, []).append(i)
    for w in words:
        m.Add(sum(pick[i] for i in by_word[w]) == 1)
    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    solver.parameters.num_search_workers = 1

    class _C(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            super().__init__(); self.count = 0
        def on_solution_callback(self):
            self.count += 1
            if self.count >= cap:
                self.StopSearch()
    cb = _C(); solver.Solve(m, cb)
    return cb.count


def is_unique_puzzle(words, members) -> bool:
    return count_partitions(words, members, cap=2) == 1


def _ceiling_mode1(members) -> int:
    sizes = [len(v) for v in members.values() if len(v) >= 4]
    total = 0
    for combo in combinations(sizes, 4):
        p = 1
        for s in combo:
            p *= comb(s, 4)
        total += p
    return total


def sample_valid_puzzles(members, term_tags, n_samples=400, seed=42) -> dict:
    """mode-1: 4 категории (≥4 членов) × 4 термина = 16 разных слов; certify однозначность."""
    out = {"eligible_tags": 0, "samples_valid": 0, "samples_drawn": 0,
           "vpy": None, "ceiling": _ceiling_mode1(members), "effective": None,
           "exists": False, "examples_valid": []}
    eligible = [a for a, v in members.items() if len(v) >= 4]
    out["eligible_tags"] = len(eligible)
    if len(eligible) < 4:
        return out
    rng = random.Random(seed)
    drawn = 0; attempts = 0
    while drawn < n_samples and attempts < n_samples * 20:
        attempts += 1
        tags = rng.sample(eligible, 4)
        words = []
        for a in tags:
            words += rng.sample(sorted(members[a]), 4)
        if len(set(words)) != 16:
            continue
        drawn += 1
        if is_unique_puzzle(words, members):
            out["samples_valid"] += 1
            out["exists"] = True
            if len(out["examples_valid"]) < 2:
                out["examples_valid"].append({"tags": tags, "words": words})
    out["samples_drawn"] = drawn
    if drawn:
        out["vpy"] = round(out["samples_valid"] / drawn, 4)
        out["effective"] = round(out["ceiling"] * out["vpy"])
    return out


def mode2_cooccurrence_pairs(members) -> int:
    names = list(members)
    return sum(1 for a, b in combinations(names, 2) if len(members[a] & members[b]) >= 4)


def _draw_partition(tags, members, rng):
    """4 категории (tags) → 16 РАЗЛИЧНЫХ слов (по 4 на категорию). None при невозможности."""
    used: set = set()
    words = []
    for a in tags:
        pool = sorted(members[a] - used)
        if len(pool) < 4:
            return None
        pick = rng.sample(pool, 4)
        words += pick
        used.update(pick)
    return words


def sample_valid_puzzles_mode2(members, term_tags, n_samples=400, seed=42) -> dict:
    """mode-2: кандидат W содержит ≥1 термин v с |tags(v) ∩ S| ≥ 2 (ловушка-пересечение).
    valid_2 = is_unique(W, member_of). VPY_2 — доля валидных среди кандидатов."""
    out = {"samples_drawn": 0, "samples_valid": 0, "vpy": None,
           "exists": False, "examples_valid": []}
    eligible = [a for a, v in members.items() if len(v) >= 4]
    if len(eligible) < 4:
        return out
    rng = random.Random(seed)
    drawn = 0
    attempts = 0
    while drawn < n_samples and attempts < n_samples * 20:
        attempts += 1
        tags = rng.sample(eligible, 4)
        words = _draw_partition(tags, members, rng)
        if words is None:
            continue
        sset = set(tags)
        if not any(len(term_tags.get(w, set()) & sset) >= 2 for w in words):
            continue  # кандидат mode-2 обязан содержать ловушку-пересечение
        drawn += 1
        if is_unique_puzzle(words, members):
            out["samples_valid"] += 1
            out["exists"] = True
            if len(out["examples_valid"]) < 2:
                out["examples_valid"].append({"tags": tags, "words": words})
    out["samples_drawn"] = drawn
    if drawn:
        out["vpy"] = round(out["samples_valid"] / drawn, 4)
    return out


def trap_quality(words, members, members_r3) -> int:
    """Квадры, монохромные под R3 = member_of ∪ decoy_for, но НЕ под member_of:
    соблазнительная неверная четвёрка существует только благодаря decoy-рёбрам."""
    q_member = set(map(frozenset, _quads(words, members)))
    q_r3 = set(map(frozenset, _quads(words, members_r3)))
    return len(q_r3 - q_member)


def sample_valid_puzzles_mode3(members, term_tags, decoy_map,
                               n_samples=400, seed=42) -> dict:
    """mode-3: W из member_of-партиции, содержит ≥1 decoy-термин, чья цель в S.
    valid_3 = is_unique(W, member_of) ∧ trap_quality(W) ≥ 1."""
    out = {"samples_drawn": 0, "samples_valid": 0, "vpy": None,
           "exists": False, "examples_valid": [], "decoy_pool": len(decoy_map)}
    eligible = [a for a, v in members.items() if len(v) >= 4]
    if len(eligible) < 4 or not decoy_map:
        return out
    # R3-отношение: членство + decoy-рёбра
    members_r3 = {a: set(v) for a, v in members.items()}
    for term, targets in decoy_map.items():
        for t in targets:
            members_r3.setdefault(t, set()).add(term)
    rng = random.Random(seed)
    drawn = 0
    attempts = 0
    while drawn < n_samples and attempts < n_samples * 20:
        attempts += 1
        tags = rng.sample(eligible, 4)
        words = _draw_partition(tags, members, rng)
        if words is None:
            continue
        sset = set(tags)
        if not any(decoy_map.get(w, set()) & sset for w in words):
            continue  # кандидат mode-3 обязан содержать живую обманку
        drawn += 1
        tq = trap_quality(words, members, members_r3)
        if tq >= 1 and is_unique_puzzle(words, members):
            out["samples_valid"] += 1
            out["exists"] = True
            if len(out["examples_valid"]) < 2:
                out["examples_valid"].append({"tags": tags, "words": words,
                                              "trap_quality": tq})
    out["samples_drawn"] = drawn
    if drawn:
        out["vpy"] = round(out["samples_valid"] / drawn, 4)
    return out
