"""solver.py — CP-SAT движок решаемости (требование R2): однозначность разбивки,
VPY-сэмплеры для mode-1/2/3, trap_quality. Спека: req2_solvability_spec_2026-06-07.md.

solver.py — CP-SAT solvability engine (requirement R2): partition uniqueness,
VPY samplers for modes 1/2/3, trap_quality. Spec: req2_solvability_spec_2026-06-07.md.
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
    """Все монохромные четвёрки: 4-подмножества слов внутри одной категории.
    Вход: words (list[str]), members (dict tag→set). Выход: list[frozenset].
    All monochromatic quads: 4-subsets of words within one category.
    In: words (list[str]), members (dict tag→set). Out: list[frozenset]."""
    wset = set(words)
    quads = set()
    for tag_members in members.values():
        pool = sorted(wset & tag_members)
        if len(pool) >= 4:
            for c in combinations(pool, 4):
                quads.add(frozenset(c))
    return list(quads)


def count_partitions(words, members, cap=2) -> int:
    """Число разбивок W на монохромные четвёрки (CP-SAT точное покрытие). cap — отсечка
    перечисления: возвращается min(истинное число, cap). Дефолт cap=2: вердикту хватает
    трёх исходов — 0 (не решается), 1 (однозначен, сертифицируем), >=2 (неоднозначен,
    невалиден — точное число уже не влияет). Вход: words (|W| кратно 4), members, cap.
    Выход: int 0..cap.
    Number of partitions of W into monochromatic quads (CP-SAT exact cover). cap is an
    enumeration cutoff: the function returns min(true count, cap). Default cap=2: the
    verdict only needs three outcomes — 0 (unsolvable), 1 (unambiguous, certified),
    >=2 (ambiguous, invalid — the exact count no longer matters). In: words (|W|
    divisible by 4), members, cap. Out: int 0..cap."""
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
        m.Add(sum(pick[i] for i in by_word[w]) == 1)  # each word in exactly one picked quad
    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    solver.parameters.num_search_workers = 1  # single worker keeps enumeration deterministic

    class _C(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            super().__init__(); self.count = 0
        def on_solution_callback(self):
            self.count += 1
            if self.count >= cap:
                self.StopSearch()  # distinguishing 0 / 1 / >=2 is all we need
    cb = _C(); solver.Solve(m, cb)
    return cb.count


def is_unique_puzzle(words, members) -> bool:
    """Однозначен ли пазл: ровно одна разбивка. Вход: words, members. Выход: bool.
    Is the puzzle unique: exactly one partition. In: words, members. Out: bool."""
    return count_partitions(words, members, cap=2) == 1


def _ceiling_mode1(members) -> int:
    """Комбинаторный потолок числа досок mode-1: для каждой четвёрки категорий
    перемножается число способов выбрать 4 термина из каждой (C(size, 4)), результаты
    суммируются по всем четвёркам. Вход: members. Выход: int.
    Combinatorial ceiling of mode-1 boards: for every quadruple of categories, multiply
    the number of ways to pick 4 terms from each (C(size, 4)), then sum over all
    quadruples. In: members. Out: int."""
    sizes = [len(v) for v in members.values() if len(v) >= 4]
    total = 0
    for combo in combinations(sizes, 4):
        p = 1
        for s in combo:
            p *= comb(s, 4)
        total += p
    return total


def sample_valid_puzzles(members, term_tags, n_samples=400, seed=42) -> dict:
    """mode-1: сэмплирует доски 4 категории × 4 термина и сертифицирует однозначность.
    Вход: members, term_tags, n_samples, seed. Выход: dict {vpy, exists, ceiling, effective, …}.
    mode-1: samples 4-category × 4-term boards and certifies uniqueness.
    In: members, term_tags, n_samples, seed. Out: dict {vpy, exists, ceiling, effective, …}."""
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
            continue  # overlapping draw — not a board of 16 distinct words
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
    """Предусловие mode-2: число пар категорий с пересечением ≥ 4 терминов.
    Вход: members. Выход: int.
    Mode-2 precondition: number of category pairs sharing ≥ 4 terms.
    In: members. Out: int."""
    names = list(members)
    return sum(1 for a, b in combinations(names, 2) if len(members[a] & members[b]) >= 4)


def _draw_partition(tags, members, rng):
    """4 категории → 16 различных слов (по 4 на категорию, без переиспользования).
    Вход: tags (list), members, rng. Выход: list[str] | None (если не извлекается).
    4 categories → 16 distinct words (4 per category, no reuse).
    In: tags (list), members, rng. Out: list[str] | None (when infeasible)."""
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
    """mode-2: кандидат W обязан содержать термин с ≥ 2 тегами из выбранной четвёрки
    (ловушка-пересечение); valid = is_unique. Вход: members, term_tags, n_samples, seed.
    Выход: dict {vpy, exists, samples_drawn, …}.
    mode-2: a candidate W must contain a term holding ≥ 2 tags within the chosen four
    categories (overlap trap); valid = is_unique. In: members, term_tags, n_samples, seed.
    Out: dict {vpy, exists, samples_drawn, …}."""
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
            continue  # a mode-2 candidate must contain an overlap trap
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
    """Квадры, монохромные под R₃ = member_of ∪ decoy_for, но НЕ под member_of:
    неверная четвёрка существует только из-за decoy-рёбер. Вход: words, members,
    members_r3. Выход: int (число таких квадр).
    Quads monochromatic under R₃ = member_of ∪ decoy_for but NOT under member_of:
    the wrong quad exists only because of decoy edges. In: words, members,
    members_r3. Out: int (count of such quads)."""
    q_member = set(map(frozenset, _quads(words, members)))
    q_r3 = set(map(frozenset, _quads(words, members_r3)))
    return len(q_r3 - q_member)


def sample_valid_puzzles_mode3(members, term_tags, decoy_map,
                               n_samples=400, seed=42) -> dict:
    """mode-3: W из member_of-партиции с ≥ 1 живой обманкой (decoy-цель внутри S);
    valid = is_unique(member_of) ∧ trap_quality ≥ 1. Вход: members, term_tags,
    decoy_map (term→set(tags)), n_samples, seed. Выход: dict {vpy, exists, decoy_pool, …}.
    mode-3: W is a member_of partition with ≥ 1 live decoy (decoy target inside S);
    valid = is_unique(member_of) ∧ trap_quality ≥ 1. In: members, term_tags,
    decoy_map (term→set(tags)), n_samples, seed. Out: dict {vpy, exists, decoy_pool, …}."""
    out = {"samples_drawn": 0, "samples_valid": 0, "vpy": None,
           "exists": False, "examples_valid": [], "decoy_pool": len(decoy_map)}
    eligible = [a for a, v in members.items() if len(v) >= 4]
    if len(eligible) < 4 or not decoy_map:
        return out
    # R3 relation: membership plus decoy edges
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
            continue  # a mode-3 candidate must contain a live decoy
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
