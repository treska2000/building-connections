#!/usr/bin/env python3
"""req2_vpy_calibration.py — калибровка порогов τ₂/τ₃ для VPY (R2, mode-2/3).

Запуск:  python3 req2_vpy_calibration.py [--fast] [--out-dir DIR] [--time-budget S]
Выходы:  req2_vpy_calibration_results.json, vpy_calibration_plots/*.png

Детерминизм: MASTER_SEED=20260612, все rng — random.Random с производными
seed'ами; CP-SAT с num_search_workers=1 (внутри solver.py). Два прогона дают
идентичный results.json (поле meta.runtime_s исключается из сравнения).
Тяжёлые блоки кэшируются поэлементно в .vpy_calib_cache/ — кэш только
ускоряет перезапуск, на числа не влияет (каждый блок детерминирован сам по
себе); для чистого пере-прогона удалите кэш. --time-budget S прерывает работу
после S секунд (код выхода 3) — перезапуск продолжит с места остановки.

Эксперименты:
  E1  — фазовая карта VPY₂ по структурным параметрам пула (m, o, n_pairs);
  E1b — механизм: P(unique | k ловушек на доске) — проверка модели swap-циклов;
  E2  — распределения VPY₂ по классам пулов (2 рецепта позитивов, 6 негативов);
  E3  — выбор τ₂ на train-половине, проверка на held-out, составной гейт;
  E4  — статнадёжность: Wilson CI, flip-rate гейта по N, зона pending;
  E5  — перенос на mode-3 (τ₃): рецепты с decoy_map, отличия профиля;
  SAN — sanity на реальных конфигах (goldens) — не как истина, а как проверка,
        что порог не отрезает всё живое.

Переиспользует validation/solver.py (или validation_pipeline/solver.py) —
не копирует его. Спека: req2_solvability_spec_2026-06-07.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from itertools import combinations

# ---------------------------------------------------------------- solver path
HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_DIRS = [
    os.path.join(HERE, "validation"),            # запуск из корня building-connections
    os.path.join(HERE, "validation_pipeline"),   # layout ресёрч-папки
    os.path.dirname(HERE),                       # скрипт лежит в validation/research/
    HERE,                                        # скрипт лежит рядом с solver.py
]
for _d in _CANDIDATE_DIRS:
    if os.path.isfile(os.path.join(_d, "solver.py")):
        sys.path.insert(0, _d)
        break
else:
    sys.exit("solver.py не найден ни в validation/, ни в validation_pipeline/")

import solver as S  # noqa: E402

if not S.HAVE_ORTOOLS:
    sys.exit("ortools не установлен — CP-SAT недоступен")

MASTER_SEED = 20260612


# ---------------------------------------------------------------- кэш стадий
class BudgetExceeded(Exception):
    pass


class StageCache:
    """Поэлементный кэш детерминированных блоков. Не влияет на числа."""

    def __init__(self, cache_dir: str, time_budget: float | None):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.t0 = time.time()
        self.budget = time_budget

    def get(self, key: str, fn):
        path = os.path.join(self.dir, key + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        if self.budget and time.time() - self.t0 > self.budget:
            raise BudgetExceeded(key)
        val = fn()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(val, f, ensure_ascii=False, sort_keys=True)
        return val


# ================================================================ utils
def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% CI для биномиальной доли."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X ≤ k), X~Binom(n,p) — без scipy, через log-комбинации."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    total = 0.0
    for i in range(k + 1):
        total += math.exp(
            math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(max(p, 1e-300)) + (n - i) * math.log(max(1 - p, 1e-300)))
    return min(1.0, total)


def gate_flip_prob(p_true: float, n: int, tau: float) -> float:
    """Вероятность, что выборочный VPY окажется по «неправильную» сторону τ."""
    k_tau = math.ceil(tau * n)  # pass ⟺ k ≥ k_tau
    p_pass = 1.0 - binom_cdf(k_tau - 1, n, p_true)
    return p_pass if p_true < tau else 1.0 - p_pass


def rnd(x, nd=4):
    return None if x is None else round(x, nd)


# ================================================================ генератор пулов
def build_pool(K: int, m: int, pair_overlaps=(), triple_overlaps=(),
               decoy_spec=(), seed: int = 0):
    """Синтетический пул.

    K, m            — число категорий и базовый размер каждой;
    pair_overlaps   — [(i, j, o)] : категории c_i, c_j делят o терминов;
    triple_overlaps — [(i, j, k, o)]: o терминов с тремя тегами;
    decoy_spec      — [(home_i, target_j, leak)]: decoy-термин из c_home,
                      помеченный decoy_for→c_target; leak=True → термин ТАЙНО
                      ещё и член target (испорченная разметка).
    Размер категории = m включая разделяемые термины (как в реальных конфигах).
    """
    cats = [f"c{i}" for i in range(K)]
    members = {c: set() for c in cats}
    term_tags: dict[str, set] = {}
    decoy_map: dict[str, set] = {}
    tid = 0

    def new_term(tags):
        nonlocal tid
        t = f"t{tid:04d}"
        tid += 1
        term_tags[t] = set(tags)
        for c in tags:
            members[c].add(t)
        return t

    for (i, j, k, o) in triple_overlaps:
        for _ in range(o):
            new_term([cats[i], cats[j], cats[k]])
    for (i, j, o) in pair_overlaps:
        for _ in range(o):
            new_term([cats[i], cats[j]])
    for c in cats:
        while len(members[c]) < m:
            new_term([c])
    for (hi, tj, leak) in decoy_spec:
        home, target = cats[hi], cats[tj]
        # decoy — отдельный «чистый» член home-категории (поверх m базовых)
        d = new_term([home])
        decoy_map[d] = {target}
        if leak:  # порча: decoy на самом деле член своей цели
            term_tags[d].add(target)
            members[target].add(d)
    return members, term_tags, decoy_map


# --------------------------------------------------------------- рецепты mode-2
# Каждый рецепт: rng -> params_dict. Класс = МЕХАНИЗМ, а не значение VPY.
def recipe_good_sparse(rng):
    """Позитив: ловушки есть (2–4 пары, o=1–2), вокруг каждой запас ≥4."""
    K = rng.choice([6, 8])
    m = rng.choice([6, 7, 8])
    n_pairs = rng.randint(2, min(4, K // 2))
    o = rng.choice([1, 2])
    pairs = [(2 * i, 2 * i + 1, o) for i in range(n_pairs)]
    return dict(K=K, m=m, pair_overlaps=pairs)


def recipe_good_rich(rng):
    """Позитив: больше ловушечного материала, но запас сохранён (m−o ≥ 5)."""
    K = rng.choice([8, 10])
    m = rng.choice([8, 9])
    n_pairs = rng.randint(3, K // 2)
    o = rng.choice([2, 3])
    pairs = [(2 * i, 2 * i + 1, o) for i in range(n_pairs)]
    return dict(K=K, m=m, pair_overlaps=pairs)


def recipe_neg_dense(rng):
    """Негатив: плотное пересечение o=4–5 при m−o < 4 → голод сэмплера/обвал."""
    o = rng.choice([4, 5])
    m = o + rng.randint(0, 2)  # m−o ∈ {0,1,2} < 4
    K = rng.choice([6, 8])
    pairs = [(0, 1, o)]
    if rng.random() < 0.5:
        pairs.append((2, 3, o))
    return dict(K=K, m=m, pair_overlaps=pairs)


def recipe_neg_dup(rng):
    """Негатив: категория-дубль (две категории делят m−1 терминов)."""
    K = rng.choice([6, 8])
    m = rng.choice([6, 7])
    return dict(K=K, m=m, pair_overlaps=[(0, 1, m - 1)])


def recipe_neg_chain(rng):
    """Негатив: треугольник A∩B, B∩C, C∩A (o=3) + 2 термина A∩B∩C.
    Слабые цепочки (o=2, без триплетов) структурно неотличимы от
    good_sparse — это граница фазы, см. E1 и методологию §границы."""
    K = 6
    m = rng.choice([7, 8])
    o = 3
    pairs = [(0, 1, o), (1, 2, o), (2, 0, o)]
    triples = [(0, 1, 2, 2)]
    return dict(K=K, m=m, pair_overlaps=pairs, triple_overlaps=triples)


def recipe_neg_deficit(rng):
    """Негатив: дефицит ловушек — мультитеговых терминов нет вообще."""
    return dict(K=rng.choice([6, 8]), m=rng.choice([6, 7, 8]), pair_overlaps=[])


def recipe_neg_adversarial(rng):
    """Adversarial №1: минимум ловушечного материала при соблюдении текущего
    pre2 (ровно одна пара с ∩=4), m≫o. Эмпирический итог (см. результаты):
    ∩≥4 вынуждает 4 со-категорийных ловушки → swap-циклы → VPY₂≈0.33–0.57,
    высоко накрутить НЕ удаётся; ловится контр-метрикой extractable_pairs."""
    m = rng.choice([12, 14, 16])
    K = rng.choice([8, 10])
    return dict(K=K, m=m, pair_overlaps=[(0, 1, 4)])


def recipe_neg_adversarial_scatter(rng):
    """Adversarial №2 («scatter», под extraction-aware-редефиницию pre2):
    4 РАЗНЕСЁННЫХ пересечения по o=1 при больших категориях — на доске почти
    всегда ровно одна ловушка → VPY₂→1, pair-контр-метрики проходят.
    Структурно совпадает с минимально-достаточным «хорошим» пулом (floor) —
    см. honest-секцию методологии."""
    m = rng.choice([10, 12])
    K = rng.choice([8, 10])
    pairs = [(2 * i, 2 * i + 1, 1) for i in range(4)]
    return dict(K=K, m=m, pair_overlaps=pairs)


RECIPES_MODE2 = {
    "good_sparse": (recipe_good_sparse, 1),
    "good_rich": (recipe_good_rich, 1),
    "neg_dense": (recipe_neg_dense, 0),
    "neg_dup": (recipe_neg_dup, 0),
    "neg_chain": (recipe_neg_chain, 0),
    "neg_deficit": (recipe_neg_deficit, 0),
    "neg_adversarial": (recipe_neg_adversarial, 0),
    "neg_adversarial_scatter": (recipe_neg_adversarial_scatter, 0),
}


# --------------------------------------------------------------- рецепты mode-3
def _decoys_for(rng, K, n_decoys, leak=False):
    out = []
    for _ in range(n_decoys):
        hi = rng.randrange(K)
        tj = rng.randrange(K)
        while tj == hi:
            tj = rng.randrange(K)
        out.append((hi, tj, leak))
    return out


def recipe3_good_clean(rng):
    """Позитив mode-3: чистая база (без пересечений) + 4–8 честных decoys."""
    K = rng.choice([6, 8])
    m = rng.choice([6, 7, 8])
    return dict(K=K, m=m, pair_overlaps=[],
                decoy_spec=_decoys_for(rng, K, rng.randint(4, 8)))


def recipe3_good_sparse(rng):
    """Позитив mode-3: умеренные пересечения + честные decoys."""
    p = recipe_good_sparse(rng)
    p["decoy_spec"] = _decoys_for(rng, p["K"], rng.randint(4, 8))
    return p


def recipe3_neg_leak(rng):
    """Негатив mode-3: decoys тайно ЧЛЕНЫ своих целей (испорченная разметка) —
    «обманка» не обманка, trap_quality → 0."""
    K = rng.choice([6, 8])
    m = rng.choice([6, 7, 8])
    return dict(K=K, m=m, pair_overlaps=[],
                decoy_spec=_decoys_for(rng, K, rng.randint(4, 8), leak=True))


def recipe3_neg_dense(rng):
    """Негатив mode-3: честные decoys поверх засорённой базы (дубль/цепочка)."""
    base = recipe_neg_dup(rng) if rng.random() < 0.5 else recipe_neg_chain(rng)
    base["decoy_spec"] = _decoys_for(rng, base["K"], rng.randint(4, 8))
    return base


RECIPES_MODE3 = {
    "good3_clean": (recipe3_good_clean, 1),
    "good3_sparse": (recipe3_good_sparse, 1),
    "neg3_leak": (recipe3_neg_leak, 0),
    "neg3_dense": (recipe3_neg_dense, 0),
}


def _pool_seed(recipes: dict, cls: str, idx: int) -> int:
    cls_i = sorted(recipes).index(cls)
    return MASTER_SEED + 104729 * (cls_i + 1) + idx


def materialize(recipes: dict, cls: str, idx: int):
    """Восстановить пул класса cls с индексом idx (детерминированно)."""
    seed = _pool_seed(recipes, cls, idx)
    rng = random.Random(seed)
    params = recipes[cls][0](rng)
    members, term_tags, decoy_map = build_pool(seed=seed, **params)
    return params, members, term_tags, decoy_map, seed


# ================================================== инструментированный сэмплер
def vpy2_instrumented(members, term_tags, n_samples=400, seed=42):
    """Реплика S.sample_valid_puzzles_mode2 (та же последовательность rng,
    тот же отбор кандидатов) + регистрация: число ловушек на доске,
    uniqueness по числу ловушек. Эквивалентность проверяется в selftest."""
    eligible = [a for a, v in members.items() if len(v) >= 4]
    res = {"samples_drawn": 0, "samples_valid": 0, "vpy": None,
           "by_trap_count": {}, "mean_traps": None}
    if len(eligible) < 4:
        return res
    rng = random.Random(seed)
    drawn = valid = 0
    attempts = 0
    by_k: dict[int, list] = {}
    trap_total = 0
    while drawn < n_samples and attempts < n_samples * 20:
        attempts += 1
        axes = rng.sample(eligible, 4)
        words = S._draw_partition(axes, members, rng)
        if words is None:
            continue
        sset = set(axes)
        k = sum(1 for w in words if len(term_tags.get(w, set()) & sset) >= 2)
        if k < 1:
            continue
        drawn += 1
        trap_total += k
        uniq = S.is_unique_puzzle(words, members)
        valid += uniq
        cell = by_k.setdefault(k, [0, 0])
        cell[0] += 1
        cell[1] += uniq
    res["samples_drawn"] = drawn
    res["samples_valid"] = valid
    if drawn:
        res["vpy"] = round(valid / drawn, 4)
        res["mean_traps"] = round(trap_total / drawn, 3)
        res["by_trap_count"] = {str(k): {"n": v[0], "p_unique": round(v[1] / v[0], 4)}
                                for k, v in sorted(by_k.items())}
    return res


# ============================================ структурные контр-метрики (без solver)
def mean_traps_per_board(members, term_tags, n_boards=200, seed=0):
    """Среднее число ловушек на mode-2 кандидата — БЕЗ CP-SAT (дёшево,
    детерминированно). Контр-метрика против adversarial-минимизации ловушек."""
    eligible = [a for a, v in members.items() if len(v) >= 4]
    if len(eligible) < 4:
        return None
    rng = random.Random(seed)
    drawn = 0
    attempts = 0
    total = 0
    while drawn < n_boards and attempts < n_boards * 20:
        attempts += 1
        axes = rng.sample(eligible, 4)
        words = S._draw_partition(axes, members, rng)
        if words is None:
            continue
        sset = set(axes)
        k = sum(1 for w in words if len(term_tags.get(w, set()) & sset) >= 2)
        if k < 1:
            continue
        drawn += 1
        total += k
    return round(total / drawn, 3) if drawn else None


def structural_counters(members, term_tags):
    """Детерминированные счётчики пула для составного гейта (anti-gaming)."""
    n_terms = len(term_tags)
    multi = [t for t, tg in term_tags.items() if len(tg) >= 2]
    pairs_any = 0
    pairs_extractable = 0
    for a, b in combinations(sorted(members), 2):
        inter = len(members[a] & members[b])
        if inter >= 1:
            pairs_any += 1
            if len(members[a] - members[b]) >= 4 and len(members[b] - members[a]) >= 4:
                pairs_extractable += 1
    return {
        "n_terms": n_terms,
        "n_categories": len(members),
        "trap_terms": len(multi),
        "trap_share": round(len(multi) / n_terms, 4) if n_terms else None,
        "trapped_pairs_any": pairs_any,                  # |a∩b| ≥ 1
        "trapped_pairs_extractable": pairs_extractable,  # ∩≥1 и запас ≥4 с обеих сторон
        "cooccurrence_pairs_ge4": S.mode2_cooccurrence_pairs(members),  # текущее pre2
    }


# ================================================================ эксперименты
def e1_phase(cache: StageCache, n_samples: int):
    """Фазовая карта VPY₂(m, o, n_pairs), K=8, 2 сида на ячейку."""
    grid = []
    for m in (5, 6, 7, 8, 9):
        def unit(m=m):
            rows = []
            for o in (1, 2, 3, 4, 5):
                if o >= m:
                    continue
                for n_pairs in (1, 2, 4):
                    vpys, drawns = [], []
                    for rep in range(2):
                        seed = MASTER_SEED + 7919 * (m * 100 + o * 10 + n_pairs) + rep
                        members, term_tags, _ = build_pool(
                            K=8, m=m,
                            pair_overlaps=[(2 * i, 2 * i + 1, o) for i in range(n_pairs)],
                            seed=seed)
                        r = S.sample_valid_puzzles_mode2(members, term_tags,
                                                         n_samples=n_samples, seed=seed)
                        vpys.append(r["vpy"])
                        drawns.append(r["samples_drawn"])
                    vv = [v for v in vpys if v is not None]
                    rows.append({"m": m, "o": o, "n_pairs": n_pairs,
                                 "vpy2_mean": rnd(sum(vv) / len(vv)) if vv else None,
                                 "drawn_mean": sum(drawns) / len(drawns),
                                 "headroom": m - o})
            return rows
        grid.extend(cache.get(f"e1_m{m}", unit))
    return grid


def e1b_mechanism(cache: StageCache, n_samples: int):
    """P(unique | k ловушек) на трёх репрезентативных профилях."""
    profiles = [
        ("sparse m7 o2 np2", dict(K=8, m=7, pair_overlaps=[(0, 1, 2), (2, 3, 2)])),
        ("rich m8 o3 np4", dict(K=8, m=8,
                                pair_overlaps=[(0, 1, 3), (2, 3, 3), (4, 5, 3), (6, 7, 3)])),
        ("tight m8 o4 np1", dict(K=8, m=8, pair_overlaps=[(0, 1, 4)])),
    ]
    out = {}
    for name, params in profiles:
        def unit(params=params):
            members, term_tags, _ = build_pool(seed=MASTER_SEED + 13, **params)
            return vpy2_instrumented(members, term_tags,
                                     n_samples=n_samples, seed=MASTER_SEED + 13)
        out[name] = cache.get("e1b_" + name.replace(" ", "_"), unit)
    return out


def gen_class_pools(cache: StageCache, recipes: dict, pools_per_class: int,
                    mode3: bool, n_samples: int, prefix: str):
    """Пулы по классам + VPY. Чётный idx → train, нечётный → test."""
    rows = []
    CHUNK = 10  # гранулярность кэша (укладывается в короткие сессии)
    for cls, (recipe, label) in sorted(recipes.items()):
        def unit_chunk(cls, label, lo, hi):
            crows = []
            for idx in range(lo, hi):
                params, members, term_tags, decoy_map, seed = materialize(recipes, cls, idx)
                if mode3:
                    r = S.sample_valid_puzzles_mode3(members, term_tags, decoy_map,
                                                     n_samples=n_samples, seed=seed)
                else:
                    r = S.sample_valid_puzzles_mode2(members, term_tags,
                                                     n_samples=n_samples, seed=seed)
                counters = structural_counters(members, term_tags)
                counters["mean_traps_per_board"] = mean_traps_per_board(
                    members, term_tags, seed=seed)
                row = {"class": cls, "label": label, "idx": idx,
                       "split": "train" if idx % 2 == 0 else "test",
                       "vpy": r["vpy"], "drawn": r["samples_drawn"],
                       "params": {k: v for k, v in params.items() if k != "decoy_spec"},
                       "counters": counters}
                if mode3:
                    row["counters"]["decoy_pool"] = len(decoy_map)
                    # кросс-модовая проверка: VPY₂ того же пула (для составного
                    # гейта — порча базы маскируется decoy-кондиционированием)
                    r2 = S.sample_valid_puzzles_mode2(members, term_tags,
                                                      n_samples=n_samples, seed=seed)
                    row["vpy2"] = r2["vpy"]
                    row["drawn2"] = r2["samples_drawn"]
                crows.append(row)
            return crows
        for lo in range(0, pools_per_class, CHUNK):
            hi = min(lo + CHUNK, pools_per_class)
            rows.extend(cache.get(
                f"{prefix}_{cls}_{lo}_{hi}",
                lambda cls=cls, label=label, lo=lo, hi=hi: unit_chunk(cls, label, lo, hi)))
    return rows


def score_of(row) -> float:
    """Скор пула для τ-гейта: VPY, а недоставаемость (drawn=0) → 0."""
    return row["vpy"] if row["vpy"] is not None else 0.0


def roc_pr(rows):
    """ROC по скорам пулов; positive class = «хороший пул»."""
    pts = sorted({score_of(r) for r in rows} | {0.0, 1.0})
    P = sum(1 for r in rows if r["label"] == 1)
    Ng = sum(1 for r in rows if r["label"] == 0)
    roc = []
    for tau in pts:
        tp = sum(1 for r in rows if r["label"] == 1 and score_of(r) >= tau)
        fp = sum(1 for r in rows if r["label"] == 0 and score_of(r) >= tau)
        roc.append((fp / Ng if Ng else 0.0, tp / P if P else 0.0, tau))
    roc_sorted = sorted(roc)
    auc = 0.0
    for (x1, y1, _), (x2, y2, _) in zip(roc_sorted, roc_sorted[1:]):
        auc += (x2 - x1) * (y1 + y2) / 2
    return roc, round(auc, 4)


def f1_at(rows, tau):
    tp = sum(1 for r in rows if r["label"] == 1 and score_of(r) >= tau)
    fp = sum(1 for r in rows if r["label"] == 0 and score_of(r) >= tau)
    fn = sum(1 for r in rows if r["label"] == 1 and score_of(r) < tau)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


def select_tau(rows_train, exclude_classes=("neg_adversarial",)):
    """Три критерия на train. Adversarial исключён из подбора τ: он по
    построению неотличим по VPY (см. anti-gaming) — его ловит составной
    гейт, а не τ."""
    fit = [r for r in rows_train if r["class"] not in exclude_classes]
    goods = sorted(score_of(r) for r in fit if r["label"] == 1)
    bads = sorted(score_of(r) for r in fit if r["label"] == 0)
    taus = [round(0.01 * i, 2) for i in range(101)]
    tau_f1 = max(taus, key=lambda t: (f1_at(fit, t), -t))
    # q05 — ПО СЛАБЕЙШЕМУ позитивному механизму, не по пулу позитивов:
    # иначе сильный подкласс (напр. clean) маскирует нижний край слабого
    # и порог режет заявленный позитивный рецепт целиком
    q05_by_class = {}
    for cls in sorted({r["class"] for r in fit if r["label"] == 1}):
        vals = sorted(score_of(r) for r in fit if r["class"] == cls)
        q_idx = max(0, math.ceil(0.05 * len(vals)) - 1)
        q05_by_class[cls] = vals[q_idx]
    tau_q05 = min(q05_by_class.values())
    tau_fpr5 = None
    for t in taus:
        bad_pass = sum(1 for b in bads if b >= t) / len(bads) if bads else 0.0
        if bad_pass <= 0.05:
            tau_fpr5 = t
            break
    good_min = goods[0] if goods else None
    bad_max = bads[-1] if bads else None
    # итоговое правило: τ = q05(хороших) − шум измерения (Wilson half-width
    # при N=400) — нижний край «живого» с поправкой на биномиальный шум.
    # Строже середины зазора к НЕВИДАННЫМ промежуточным порчам, при этом
    # не режет хорошие. Если негативы подпирают (зазор < 0.05) — midpoint.
    lo, hi = wilson_ci(round(tau_q05 * 400), 400)
    tau_noise = round(tau_q05 - (hi - lo) / 2, 2)
    if bad_max is not None and bad_max < tau_noise - 0.05:
        tau_rule, tau_final = "q05_minus_noise", tau_noise
    elif good_min is not None and bad_max is not None and good_min > bad_max:
        tau_rule, tau_final = "gap_midpoint", round((good_min + bad_max) / 2, 2)
    else:
        tau_rule, tau_final = "max_f1", tau_f1
    return {"tau_max_f1": tau_f1, "tau_q05_good": rnd(tau_q05),
            "q05_by_class": {k: rnd(v) for k, v in q05_by_class.items()},
            "tau_fpr5": tau_fpr5, "tau_rule": tau_rule, "tau_final": tau_final,
            "good_min": rnd(good_min), "bad_max": rnd(bad_max)}


def _vpy_verdict(vpy, valid_k, drawn, tau, min_drawn_frac, n_samples):
    """Трёхзначный вердикт: fail / pending / pass.
    pending — Wilson 95% CI выборочного VPY накрывает τ (решение на этом N
    статистически не определено — нужен больший N или ручной разбор)."""
    if drawn < min_drawn_frac * n_samples:
        return "fail"  # голод сэмплера: кандидаты не извлекаются
    lo, hi = wilson_ci(valid_k, drawn)
    if lo <= tau <= hi:
        return "pending"
    return "pass" if (vpy or 0.0) >= tau else "fail"


def eval_gate(rows, tau, min_drawn_frac, n_samples, counters_gate=None):
    """Оценка гейта по классам: вердикты pass/pending/fail; контр-метрики
    (если заданы) переводят pass → fail."""
    by_cls = {}
    for r in rows:
        valid_k = round((r["vpy"] or 0.0) * r["drawn"])
        v = _vpy_verdict(r["vpy"], valid_k, r["drawn"], tau,
                         min_drawn_frac, n_samples)
        if v != "fail" and counters_gate:
            c = r["counters"]
            if not all(c.get(k) is not None and c.get(k) >= thr
                       for k, thr in counters_gate.items()):
                v = "fail"
        cell = by_cls.setdefault(r["class"], {"label": r["label"], "n": 0,
                                              "pass": 0, "pending": 0, "fail": 0})
        cell["n"] += 1
        cell[v] += 1
    for c in by_cls.values():
        c["pass_rate"] = round(c["pass"] / c["n"], 3)
        c["pending_rate"] = round(c["pending"] / c["n"], 3)
        c["hard_fail_rate"] = round(c["fail"] / c["n"], 3)
    return by_cls


def eval_gate3_composite(rows3, tau3, tau2, min_drawn_frac, n_samples):
    """Составной гейт mode-3: vpy3-гейт ∧ (mode-2-гейт, если в пуле есть
    overlap-материал). Ловит порчу базы, замаскированную decoy-кондиционированием.
    Вердикт = худший из двух (fail < pending < pass)."""
    order = {"fail": 0, "pending": 1, "pass": 2}
    by_cls = {}
    for r in rows3:
        k3 = round((r["vpy"] or 0.0) * r["drawn"])
        v3 = _vpy_verdict(r["vpy"], k3, r["drawn"], tau3, min_drawn_frac, n_samples)
        v = v3
        if r["counters"]["trapped_pairs_any"] >= 1:  # mode-2 применим
            k2 = round((r.get("vpy2") or 0.0) * r.get("drawn2", 0))
            v2 = _vpy_verdict(r.get("vpy2"), k2, r.get("drawn2", 0), tau2,
                              min_drawn_frac, n_samples)
            v = min(v3, v2, key=order.get)
        cell = by_cls.setdefault(r["class"], {"label": r["label"], "n": 0,
                                              "pass": 0, "pending": 0, "fail": 0})
        cell["n"] += 1
        cell[v] += 1
    for c in by_cls.values():
        c["pass_rate"] = round(c["pass"] / c["n"], 3)
        c["pending_rate"] = round(c["pending"] / c["n"], 3)
        c["hard_fail_rate"] = round(c["fail"] / c["n"], 3)
    return by_cls


def e4_reliability(cache: StageCache, rows_mode2, tau, fast: bool):
    """Flip-rate гейта по N (эмпирика, повторные сиды) + аналитика Binom."""
    # эталоны: любые пулы с ненулевым VPY, включая окрестность τ —
    # именно там решение гейта статистически хрупкое
    cand = [r for r in rows_mode2 if r["vpy"] is not None and r["vpy"] > 0]
    take, targets = [], [tau - 0.05, tau, tau + 0.05, tau + 0.15, 0.6, 0.75, 0.9, 1.0]
    for tgt in targets:
        best = min(cand, key=lambda r: abs(score_of(r) - tgt))
        if best not in take:
            take.append(best)
    n_grid = [50, 100, 200, 400, 800]
    reps = 8 if fast else 12
    out = {"tau": tau, "pools": [], "analytic": []}
    for r in take:
        def unit(r=r):
            _, members, term_tags, _, _ = materialize(RECIPES_MODE2, r["class"], r["idx"])
            ref = S.sample_valid_puzzles_mode2(
                members, term_tags, n_samples=800 if fast else 1600, seed=MASTER_SEED)
            row = {"class": r["class"], "idx": r["idx"], "p_ref": ref["vpy"], "by_n": {}}
            for n in n_grid:
                decisions, vpys = [], []
                for rep in range(reps):
                    rr = S.sample_valid_puzzles_mode2(members, term_tags, n_samples=n,
                                                      seed=MASTER_SEED + 31 * rep + n)
                    v = rr["vpy"] if rr["vpy"] is not None else 0.0
                    vpys.append(v)
                    decisions.append(v >= tau)
                maj = decisions.count(True) >= len(decisions) / 2
                flip = sum(1 for d in decisions if d != maj) / len(decisions)
                lo, hi = wilson_ci(round(vpys[0] * n), n)
                row["by_n"][str(n)] = {"flip_rate": round(flip, 3),
                                       "vpy_spread": rnd(max(vpys) - min(vpys)),
                                       "wilson_halfwidth": rnd((hi - lo) / 2)}
            return row
        out["pools"].append(cache.get(f"e4_{r['class']}_{r['idx']}", unit))
    for p_true in (tau - 0.10, tau - 0.05, tau - 0.02, tau + 0.02, tau + 0.05, tau + 0.10):
        if not 0 < p_true < 1:
            continue
        out["analytic"].append({
            "p_true": rnd(p_true),
            "flip_by_n": {str(n): rnd(gate_flip_prob(p_true, n, tau)) for n in n_grid}})
    return out


def sanity_real_configs(cache: StageCache, n_samples: int, tau2, tau3, min_drawn_frac):
    """Реальные конфиги: VPY₂/VPY₃ + вердикт гейта (sanity, не истина)."""
    try:
        from loader import load_config, build_membership, get_decoys
    except Exception as e:  # pragma: no cover
        return {"error": f"loader не импортируется: {e}"}

    names = ["golden_ai-safety_v2.json", "golden_reasoning_v1.json",
             "_tmp_golden.json", "config_v1_example.json"]
    # configs ищем относительно скрипта И вверх до корня репо
    # (скрипт может лежать в validation/research/)
    roots = [HERE, os.path.dirname(HERE), os.path.dirname(os.path.dirname(HERE))]
    paths = []
    for root in roots:
        for base in ("configs/v1", "configs/v1/from-generator", "."):
            d = os.path.join(root, base)
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f in names or (base != "." and f.endswith(".json")
                                      and f != "schema.json"):
                        paths.append(os.path.join(d, f))

    def verdict(r, tau):
        if r is None:
            return "n/a (нет decoys)"
        v = r["vpy"] if r["vpy"] is not None else 0.0
        if r["samples_drawn"] < min_drawn_frac * n_samples:
            return "fail (drawn starvation)"
        lo, hi = wilson_ci(r["samples_valid"], r["samples_drawn"])
        if lo <= tau <= hi:
            return "pending (τ внутри CI)"
        return "pass" if v >= tau else "fail"

    out, seen = {}, set()
    for p in paths:
        name = os.path.basename(p)
        if name in seen:
            continue
        seen.add(name)

        def unit(p=p):
            cfg = load_config(p)
            if not isinstance(cfg, dict):  # legacy-форматы (список) — пропуск
                return None
            members, term_tags = build_membership(cfg)
            if not members:
                return None
            r2 = S.sample_valid_puzzles_mode2(members, term_tags,
                                              n_samples=n_samples, seed=42)
            decoy_map = {t["name"]: set(get_decoys(t)) for t in cfg.get("terms", [])
                         if t.get("name") and get_decoys(t)}
            r3 = (S.sample_valid_puzzles_mode3(
                members, term_tags, {k: set(v) for k, v in decoy_map.items()},
                n_samples=n_samples, seed=42) if decoy_map else None)
            return {"counters": structural_counters(members, term_tags),
                    "mode2": {"vpy": r2["vpy"], "drawn": r2["samples_drawn"],
                              "valid": r2["samples_valid"]},
                    "mode3": ({"vpy": r3["vpy"], "drawn": r3["samples_drawn"],
                               "valid": r3["samples_valid"],
                               "decoy_pool": len(decoy_map)} if r3 else None)}
        try:
            rec = cache.get("san_" + name.replace(".", "_"), unit)
        except BudgetExceeded:
            raise
        except Exception as e:
            out[name] = {"error": str(e)}
            continue
        if rec is None:
            continue
        # вердикты пересчитываются от текущих τ (дёшево, вне кэша)
        m2 = dict(rec["mode2"])
        m2["verdict"] = verdict({"vpy": m2["vpy"], "samples_drawn": m2["drawn"],
                                 "samples_valid": m2["valid"]}, tau2)
        m3 = None
        if rec["mode3"]:
            m3 = dict(rec["mode3"])
            m3["verdict"] = verdict({"vpy": m3["vpy"], "samples_drawn": m3["drawn"],
                                     "samples_valid": m3["valid"]}, tau3)
        out[name] = {"counters": rec["counters"], "mode2": m2, "mode3": m3}
    return out


# ================================================================ selftest
def selftest(cache: StageCache, n_samples=300):
    """Известные факты + эквивалентность инструментированного сэмплера."""
    def unit():
        res = {}
        # 1) плотное пересечение: 5 и 5 делят 4 → кандидаты mode-2 не извлекаются
        members, term_tags, _ = build_pool(K=6, m=5, pair_overlaps=[(0, 1, 4)],
                                           seed=MASTER_SEED)
        r = S.sample_valid_puzzles_mode2(members, term_tags, n_samples=n_samples, seed=42)
        res["dense_5_5_share4_drawn"] = r["samples_drawn"]
        assert r["samples_drawn"] == 0, "ожидался голод сэмплера на профиле 5&5∩4"
        # 2) категории по 7, 2 общих → VPY₂ в районе 0.7 (известный факт ≈0.72)
        members, term_tags, _ = build_pool(K=4, m=7, pair_overlaps=[(0, 1, 2)],
                                           seed=MASTER_SEED)
        r = S.sample_valid_puzzles_mode2(members, term_tags, n_samples=400, seed=42)
        res["m7_o2_k4_vpy2"] = r["vpy"]
        # 3) инструментированный сэмплер == solver-сэмплер (rng-эквивалентность)
        members, term_tags, _ = build_pool(K=8, m=7,
                                           pair_overlaps=[(0, 1, 2), (2, 3, 2)],
                                           seed=MASTER_SEED + 1)
        a = S.sample_valid_puzzles_mode2(members, term_tags, n_samples=200, seed=7)
        b = vpy2_instrumented(members, term_tags, n_samples=200, seed=7)
        assert (a["vpy"], a["samples_drawn"]) == (b["vpy"], b["samples_drawn"]), \
            "инструментированный сэмплер разошёлся с solver.sample_valid_puzzles_mode2"
        res["instrumented_equiv"] = True
        # 4) одна ловушка на доске не ломает однозначность (механизм swap-циклов)
        k1 = b["by_trap_count"].get("1")
        if k1:
            res["p_unique_given_1_trap"] = k1["p_unique"]
        return res
    return cache.get("selftest", unit)


# ================================================================ plots
def make_plots(results, plots_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(plots_dir, exist_ok=True)

    # --- E1 фазовая карта
    grid = results["e1_phase"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), sharey=True)
    for ax, np_ in zip(axes, (1, 2, 4)):
        ms = sorted({g["m"] for g in grid})
        os_ = sorted({g["o"] for g in grid})
        img = [[next((g["vpy2_mean"] for g in grid
                      if g["m"] == m and g["o"] == o and g["n_pairs"] == np_), None)
                for o in os_] for m in ms]
        data = [[(-0.1 if v is None else v) for v in row] for row in img]
        ax.imshow(data, origin="lower", vmin=-0.1, vmax=1.0, cmap="viridis",
                  aspect="auto")
        ax.set_xticks(range(len(os_)), os_)
        ax.set_yticks(range(len(ms)), ms)
        ax.set_xlabel("o (размер пересечения)")
        ax.set_title(f"n_pairs={np_}")
        for i, m in enumerate(ms):
            for j, o in enumerate(os_):
                v = img[i][j]
                ax.text(j, i, "∅" if v is None else f"{v:.2f}",
                        ha="center", va="center", fontsize=7,
                        color="white" if (v is None or v < 0.6) else "black")
    axes[0].set_ylabel("m (размер категории)")
    fig.suptitle("E1 · VPY₂ по структуре пула (K=8; ∅ = кандидаты не извлекаются)")
    fig.tight_layout()
    fig.savefig(os.path.join(plots_dir, "e1_phase_vpy2.png"), dpi=130)
    plt.close(fig)

    # --- E1b механизм
    fig, ax = plt.subplots(figsize=(7, 4))
    width = 0.25
    for s, (name, r) in enumerate(results["e1b_mechanism"].items()):
        ks = sorted(int(k) for k in r["by_trap_count"])
        xs = [k + (s - 1) * width for k in ks]
        ys = [r["by_trap_count"][str(k)]["p_unique"] for k in ks]
        ax.bar(xs, ys, width=width, label=f"{name} (VPY₂={r['vpy']})")
    ax.set_xlabel("k — число ловушек на доске")
    ax.set_ylabel("P(unique | k)")
    ax.set_title("E1b · Однозначность ломают ≥2 ловушки (swap-циклы)")
    ax.legend(fontsize=8)
    ax.set_ylim(0, 1.05)
    fig.tight_layout()
    fig.savefig(os.path.join(plots_dir, "e1b_trapcount.png"), dpi=130)
    plt.close(fig)

    # --- распределения по классам (mode-2 и mode-3)
    for key, fname, tau_key in (("e2_pools_mode2", "e2_distributions_mode2.png", "tau2"),
                                ("e5_pools_mode3", "e5_distributions_mode3.png", "tau3")):
        rows = results[key]
        classes = sorted({r["class"] for r in rows})
        fig, ax = plt.subplots(figsize=(9, 4.5))
        rngj = random.Random(0)
        for i, cls in enumerate(classes):
            vals = [score_of(r) for r in rows if r["class"] == cls]
            xs = [i + rngj.uniform(-0.18, 0.18) for _ in vals]
            color = "tab:green" if cls.startswith("good") else (
                "tab:orange" if "adversarial" in cls else "tab:red")
            ax.scatter(xs, vals, s=14, alpha=0.65, color=color)
        tau = results["recommendation"][tau_key]
        ax.axhline(tau, color="k", ls="--", lw=1, label=f"τ = {tau}")
        zone = results["recommendation"]["pending_halfwidth"]
        ax.axhspan(tau - zone, tau + zone, color="grey", alpha=0.15,
                   label=f"pending ±{zone}")
        ax.set_xticks(range(len(classes)), classes, rotation=20, ha="right", fontsize=8)
        ax.set_ylabel("VPY (drawn=0 → 0)")
        ax.set_title(f"{key}: распределения по классам пулов")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(plots_dir, fname), dpi=130)
        plt.close(fig)

    # --- ROC
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, key, title in ((axes[0], "e3_roc_mode2", "mode-2"),
                           (axes[1], "e5_roc_mode3", "mode-3")):
        roc = results[key]["roc_points"]
        xs = [p[0] for p in roc]
        ys = [p[1] for p in roc]
        ax.plot(sorted(xs), [y for _, y in sorted(zip(xs, ys))], marker=".", ms=3)
        ax.plot([0, 1], [0, 1], "k:", lw=0.7)
        ax.set_xlabel("FPR (плохой прошёл)")
        ax.set_ylabel("TPR (хороший прошёл)")
        ax.set_title(f"ROC {title} · AUC={results[key]['auc']} (train, без adversarial)")
    fig.tight_layout()
    fig.savefig(os.path.join(plots_dir, "e3_roc.png"), dpi=130)
    plt.close(fig)

    # --- E4 надёжность
    e4 = results["e4_reliability"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for row in e4["pools"]:
        ns = sorted(int(n) for n in row["by_n"])
        axes[0].plot(ns, [row["by_n"][str(n)]["flip_rate"] for n in ns],
                     marker="o", ms=3, label=f"p≈{row['p_ref']}")
    axes[0].set_xscale("log")
    axes[0].set_xlabel("N сэмплов")
    axes[0].set_ylabel("эмпирический flip-rate")
    axes[0].set_title(f"E4 · Стабильность вердикта при τ={e4['tau']}")
    axes[0].legend(fontsize=7)
    for a in e4["analytic"]:
        ns = sorted(int(n) for n in a["flip_by_n"])
        axes[1].plot(ns, [a["flip_by_n"][str(n)] for n in ns],
                     marker="s", ms=3, label=f"p_true={a['p_true']}")
    axes[1].set_xscale("log")
    axes[1].set_xlabel("N сэмплов")
    axes[1].set_ylabel("P(flip) аналитически (Binom)")
    axes[1].set_title("E4 · Чем ближе p к τ, тем дороже стабильность")
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(plots_dir, "e4_n_sensitivity.png"), dpi=130)
    plt.close(fig)


# ================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="уменьшенные сетки (смоук)")
    ap.add_argument("--out-dir", default=HERE)
    ap.add_argument("--skip-real", action="store_true",
                    help="пропустить sanity на реальных конфигах")
    ap.add_argument("--time-budget", type=float, default=None,
                    help="прервать через S секунд (выход 3); перезапуск продолжит")
    args = ap.parse_args()

    t0 = time.time()
    N = 150 if args.fast else 400
    n_phase = 100 if args.fast else 200
    pools_per_class = 10 if args.fast else 30
    cache_dir = os.path.join(args.out_dir,
                             ".vpy_calib_cache_v2" + ("_fast" if args.fast else ""))
    cache = StageCache(cache_dir, args.time_budget)

    try:
        print("· selftest…")
        st = selftest(cache)
        print("  ", st)
        print("· E1: фазовая карта VPY₂…")
        e1 = e1_phase(cache, n_phase)
        print("· E1b: механизм (ловушки на доске)…")
        e1b = e1b_mechanism(cache, 400 if args.fast else 800)
        print("· E2: пулы по классам, mode-2…")
        rows2 = gen_class_pools(cache, RECIPES_MODE2, pools_per_class,
                                mode3=False, n_samples=N, prefix="e2")
        print("· E5: пулы по классам, mode-3…")
        rows3 = gen_class_pools(cache, RECIPES_MODE3, pools_per_class,
                                mode3=True, n_samples=N, prefix="e5")
    except BudgetExceeded as e:
        print(f"!! бюджет времени исчерпан на блоке {e}; перезапустите — "
              f"продолжится с кэша ({cache_dir})")
        sys.exit(3)

    train2 = [r for r in rows2 if r["split"] == "train"]
    test2 = [r for r in rows2 if r["split"] == "test"]
    train3 = [r for r in rows3 if r["split"] == "train"]
    test3 = [r for r in rows3 if r["split"] == "test"]

    print("· E3: выбор τ₂/τ₃ на train…")
    # adversarial исключён из подбора τ₂ (по VPY неотличим — ловит составной гейт);
    # neg3_dense исключён из подбора τ₃ (порча базы маскируется decoy-
    # кондиционированием — ловит кросс-модовый гейт, см. E5)
    ADV = ("neg_adversarial", "neg_adversarial_scatter")
    sel2 = select_tau(train2, exclude_classes=ADV)
    fit_train2 = [r for r in train2 if r["class"] not in ADV]
    roc2, auc2 = roc_pr(fit_train2)
    tau2 = sel2["tau_final"]
    sel3 = select_tau(train3, exclude_classes=("neg3_dense",))
    roc3, auc3 = roc_pr([r for r in train3 if r["class"] != "neg3_dense"])
    tau3 = sel3["tau_final"]

    MIN_DRAWN_FRAC = 0.5
    counters_gate2 = {"trapped_pairs_extractable": 2, "trap_terms": 2}
    gate2_test = eval_gate(test2, tau2, MIN_DRAWN_FRAC, N)
    gate2_test_composite = eval_gate(test2, tau2, MIN_DRAWN_FRAC, N, counters_gate2)
    gate3_test = eval_gate(test3, tau3, MIN_DRAWN_FRAC, N)
    gate3_test_composite = eval_gate3_composite(test3, tau3, tau2, MIN_DRAWN_FRAC, N)

    try:
        print("· E4: надёжность по N…")
        e4 = e4_reliability(cache, rows2, tau2, args.fast)
        real = None
        if not args.skip_real:
            print("· sanity на реальных конфигах…")
            real = sanity_real_configs(cache, N, tau2, tau3, MIN_DRAWN_FRAC)
    except BudgetExceeded as e:
        print(f"!! бюджет времени исчерпан на блоке {e}; перезапустите — "
              f"продолжится с кэша ({cache_dir})")
        sys.exit(3)

    n_rec = 400
    hw = wilson_ci(round(tau2 * n_rec), n_rec)
    pending_halfwidth = round((hw[1] - hw[0]) / 2, 3)

    recommendation = {
        "tau2": tau2, "tau3": tau3, "n_samples": n_rec,
        "min_drawn_frac": MIN_DRAWN_FRAC,
        "pending_halfwidth": pending_halfwidth,
        "pending_rule": "если Wilson 95% CI для VPY накрывает τ — вердикт pending, не fail/pass",
        "composite_gate_mode2": counters_gate2,
        "note": "синтетика даёт относительные выводы; абсолютные значения τ — "
                "стартовые, пересматривать по мере появления реальных goldens",
    }

    results = {
        "selftest": st,
        "e1_phase": e1,
        "e1b_mechanism": e1b,
        "e2_pools_mode2": rows2,
        "e3_tau_selection_mode2": sel2,
        "e3_roc_mode2": {"auc": auc2,
                         "roc_points": [(rnd(a), rnd(b), rnd(c)) for a, b, c in roc2]},
        "e3_gate_heldout_mode2": {"vpy_only": gate2_test,
                                  "composite": gate2_test_composite},
        "e4_reliability": e4,
        "e5_pools_mode3": rows3,
        "e5_tau_selection_mode3": sel3,
        "e5_roc_mode3": {"auc": auc3,
                         "roc_points": [(rnd(a), rnd(b), rnd(c)) for a, b, c in roc3]},
        "e5_gate_heldout_mode3": {"vpy_only": gate3_test,
                                  "composite_cross_mode": gate3_test_composite},
        "real_configs_sanity": real,
        "recommendation": recommendation,
    }
    meta = {"master_seed": MASTER_SEED, "n_samples": N,
            "pools_per_class": pools_per_class, "fast": args.fast,
            "runtime_s": round(time.time() - t0, 1)}

    out_json = os.path.join(args.out_dir, "req2_vpy_calibration_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "results": results}, f, ensure_ascii=False,
                  indent=1, sort_keys=True)
    print(f"→ {out_json}")

    plots_dir = os.path.join(args.out_dir, "vpy_calibration_plots")
    make_plots(results, plots_dir)
    print(f"→ {plots_dir}/")

    print("\n=== РЕКОМЕНДАЦИЯ ===")
    print(json.dumps(recommendation, ensure_ascii=False, indent=2))
    print(f"\nτ-кандидаты mode-2 (train): {sel2}")
    print(f"τ-кандидаты mode-3 (train): {sel3}")
    print("held-out mode-2 (vpy-гейт):  "
          f"{ {k: v['pass_rate'] for k, v in sorted(gate2_test.items())} }")
    print("held-out mode-2 (composite): "
          f"{ {k: v['pass_rate'] for k, v in sorted(gate2_test_composite.items())} }")
    print("held-out mode-3 (vpy-гейт):  "
          f"{ {k: v['pass_rate'] for k, v in sorted(gate3_test.items())} }")
    print("held-out mode-3 (кросс-мод): "
          f"{ {k: v['pass_rate'] for k, v in sorted(gate3_test_composite.items())} }")
    print(f"runtime: {meta['runtime_s']} s")


if __name__ == "__main__":
    main()
