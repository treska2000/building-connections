"""Controlled config degradation for calibrating the clustering gate's tau*.

Calibrating the clustering gate requires NEGATIVE examples with a continuous ground-truth
quality label: take a clean config, corrupt it at intensity∈[0,1], and produce
(degraded_config, quality) pairs for the tau* sweep. Four conservative operators are
implemented, one per defect family: pairflip (label-noise), merge (granularity),
distractor (semantic), truncate (text-surface). All operators share a common contract:
single intensity knob, determinism at fixed seed, input never mutated (deepcopy), quality∈[0,1],
lexical centroid similarity held-out from the gate embedder (optional embed_fn switches to
embeddings), schema compatibility (tags/axes, description/description_en/description_ru),
and each edit is tagged under _degraded for audit trail. Dependencies: stdlib only plus
validation.loader / validation.textutil — embeddings are never imported internally.

Контролируемое ухудшение конфигов для калибровки порога tau* кластеризационного гейта.
Калибровка требует негативных примеров с непрерывной ground-truth-меткой качества: чистый
конфиг портится с интенсивностью intensity∈[0,1] и даёт пары (ухудшённый_конфиг, quality)
для sweep'а tau*. Реализованы 4 консервативных оператора по одному из каждой семьи дефектов:
pairflip (label-noise), merge (granularity), distractor (semantic), truncate (text-surface).
Общий контракт: одна ручка intensity, детерминизм при фиксированном seed, вход не мутируется,
quality∈[0,1], лексическое сходство центроидов held-out от эмбеддера гейта, совместимость
со схемой tags/axes и полями описаний, каждая правка помечается в _degraded (аудит-трейл).
Зависимости: stdlib + validation.loader / validation.textutil, без внутренних эмбеддингов.
"""
from __future__ import annotations

import copy
import math
import random
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Optional, Sequence

from .core import loader
from .core import textutil as TU

# loader НЕ экспонирует аксессор описаний — порядок приоритета полей держим здесь.
# loader exposes no description accessor — keep the field-priority list here.
DESCRIPTION_FIELDS = ("description", "description_en", "description_ru")

EmbedFn = Callable[[str], Sequence[float]]


# ─────────────────────────── result / base class ──────────────────────────
@dataclass
class DegradationResult:
    """One degradation outcome: config is a deepcopy of the input with edits applied.

    Результат одного ухудшения. config — deepcopy входа с правками."""
    operator: str
    family: str
    intensity: float
    seed: int
    config: dict                       # ухудшённый конфиг (dict или list — как на входе)
    ground_truth_quality: float        # [0,1]
    corrupted_terms: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)


class Degrader(ABC):
    """Abstract base for degradation operators.

    Subclasses implement _run(); apply() enforces the shared contract
    (deepcopy, no-op at intensity=0, similarity construction, result packing).

    Абстрактная база операторов ухудшения. Подкласс реализует _run(); apply() обеспечивает
    общий контракт (deepcopy, no-op при intensity=0, построение сходств, упаковка результата)."""
    name: str = ""
    family: str = ""

    def apply(self, config, intensity: float, *, seed: int = 0,
              embed_fn: Optional[EmbedFn] = None, **kw) -> DegradationResult:
        """Apply the degrader: deepcopy input, skip at intensity 0, pack and return result.

        In: config (clean config dict or list), intensity (0..1), seed, embed_fn.
        Out: DegradationResult.

        Применить деградер: deepcopy входа, no-op при intensity=0, упаковка результата.
        In: config (чистый конфиг dict или list), intensity (0..1), seed, embed_fn.
        Out: DegradationResult."""
        intensity = float(intensity)
        cfg = copy.deepcopy(config)                       # (3) вход не мутируется
        if intensity <= 0.0:                              # (1) no-op
            return DegradationResult(self.name, self.family, 0.0, seed, cfg, 1.0, [],
                                     {"noop": True})
        rng = random.Random(seed)                         # (2) детерминизм
        sim = _CategorySim(cfg, embed_fn=embed_fn)        # (5) lexical | embed
        quality, corrupted, notes = self._run(cfg, intensity, rng, sim, **kw)
        quality = max(0.0, min(1.0, float(quality)))      # (4) clamp в [0,1]
        return DegradationResult(self.name, self.family, intensity, seed, cfg,
                                 quality, corrupted, notes)

    @abstractmethod
    def _run(self, cfg, intensity, rng, sim, **kw):
        """Run the operator in-place and return quality metrics.

        In: cfg (already deepcopied), intensity, rng, sim, extra kw.
        Out: (quality, corrupted_terms, notes).

        Выполнить оператор in-place и вернуть метрики качества.
        In: cfg (уже deepcopy), intensity, rng, sim, доп. kw.
        Out: (quality, corrupted_terms, notes)."""
        raise NotImplementedError


# ─────────────────────────── shape-tolerant helpers ───────────────────────
# Конфиг приходит в двух формах: dict {terms, tags|axes, …} ИЛИ голый list терминов.
# Эти хелперы прячут различие; парсинг tags/axes/категорий — через loader.
def _is_list_cfg(cfg) -> bool:
    return isinstance(cfg, list)


def _cfg_like(cfg) -> dict:
    """Wrap a bare list as a dict for loader calls (list → {'terms': list}). / dict-обёртка для вызовов loader (голый list → {'terms': list})."""
    return cfg if isinstance(cfg, dict) else {"terms": cfg}


def _terms(cfg) -> list:
    """Return the live terms list inside an already-deepcopied config. / Живой список терминов внутри (уже deepcopy'нутого) конфига."""
    return cfg if isinstance(cfg, list) else cfg.setdefault("terms", [])


def _category_names(cfg) -> list:
    """Return category names: explicit from tags/axes or inferred from term tags. / Имена категорий: явные (tags/axes конфига) либо выведенные из тегов терминов."""
    explicit = loader.get_category_names(_cfg_like(cfg))
    if explicit:
        return list(dict.fromkeys(explicit))             # дедуп, порядок сохранён
    members, _ = loader.build_membership(_cfg_like(cfg))
    return sorted(members)


def _term_tag_field(term: dict) -> str:
    """Return the tag field of a term: 'tags' (priority) or 'axes'; default 'tags'. / Поле тегов термина: 'tags' (приоритет) | 'axes'; по умолчанию 'tags'."""
    return "tags" if "tags" in term else ("axes" if "axes" in term else "tags")


def _dominant_tag_field(cfg) -> str:
    """Return the tag field for new terms, following the convention of existing ones. / Поле тегов для НОВЫХ терминов — по конвенции существующих."""
    for t in _terms(cfg):
        if isinstance(t, dict) and ("tags" in t or "axes" in t):
            return _term_tag_field(t)
    return "tags"


def _cfg_category_field(cfg) -> Optional[str]:
    """Return the top-level category list field name (dict configs only). / Поле верхнеуровневого списка категорий (только для dict-конфига)."""
    if not isinstance(cfg, dict):
        return None
    return "tags" if "tags" in cfg else ("axes" if "axes" in cfg else None)


def _primary_cat(term: dict) -> Optional[str]:
    """Return the declared category of a term: its first tag (multi-tag → first). / Заявленная категория термина = первый тег (multi-tag → первый)."""
    tags = loader.get_term_tags(term)
    return tags[0] if tags else None


def _retag_term(term: dict, old: str, new: str) -> None:
    """Replace old with new in the term's tag list, deduplicating while preserving order. / Заменить old→new в тегах термина (дедуп, порядок сохранён)."""
    fld = _term_tag_field(term)
    seen, out = set(), []
    for t in (new if t == old else t for t in term.get(fld, [])):
        if t not in seen:
            seen.add(t)
            out.append(t)
    term[fld] = out


def _remove_category(cfg, name: str) -> None:
    """Remove a category from the top-level list (no-op for list configs). / Убрать категорию из верхнеуровневого списка (no-op для list-конфига)."""
    fld = _cfg_category_field(cfg)
    if not fld:
        return
    cfg[fld] = [c for c in cfg.get(fld, [])
                if (c.get("name") if isinstance(c, dict) else c) != name]


def _desc_fields(term: dict) -> list:
    """Return the non-empty description fields present in a term. / Присутствующие непустые поля описаний термина."""
    return [f for f in DESCRIPTION_FIELDS if isinstance(term.get(f), str) and term[f].strip()]


def _term_text(term: dict) -> str:
    """Return term text (name + all description fields) for tokenisation or embeddings. / Текст термина (name + все описания) — для токенов/эмбеддингов."""
    parts = [term.get("name", "")] + [str(term.get(f, "")) for f in DESCRIPTION_FIELDS if term.get(f)]
    return " ".join(p for p in parts if p)


def _mark(term: dict, info: dict) -> None:
    """Append edit info to _degraded audit trail on the term (supports compose). / Пометить правку под ключом _degraded (список — аудит-трейл, поддерживает compose)."""
    term.setdefault("_degraded", []).append(info)


# ─────────────────────────── category similarity ──────────────────────────
def _cos_counter(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _mean_vec(vecs) -> list:
    vecs = [list(map(float, v)) for v in vecs if v is not None]
    if not vecs:
        return []
    d = len(vecs[0])
    return [sum(v[i] for v in vecs) / len(vecs) for i in range(d)]


def _cos_vec(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class _CategorySim:
    """Centroid-based category similarity, built on the original categories before any edits.

    By default uses lexical bag-of-stem-tokens via textutil (cosine of Counter vectors),
    held-out from the gate embedder. With embed_fn, uses mean term embeddings per category.

    Сходство категорий по центроидам, строится на исходных категориях до правок оператора.
    По умолчанию лексически (мешок стем-токенов через textutil, косинус Counter-векторов),
    held-out от эмбеддера гейта. С embed_fn — усреднённый эмбеддинг терминов, косинус векторов."""

    def __init__(self, cfg, embed_fn: Optional[EmbedFn] = None):
        self.embed_fn = embed_fn
        text_of = {}
        for t in _terms(cfg):
            nm = t.get("name") if isinstance(t, dict) else None
            if nm and nm not in text_of:
                text_of[nm] = _term_text(t)
        members, _ = loader.build_membership(_cfg_like(cfg))
        self._vec: dict = {}
        for cat, names in members.items():
            if embed_fn is None:
                c: Counter = Counter()
                for nm in names:
                    c.update(TU.tokenize(text_of.get(nm, "")))
                self._vec[cat] = c
            else:
                self._vec[cat] = _mean_vec([embed_fn(text_of[nm]) for nm in names if nm in text_of])

    def sim(self, a: str, b: str) -> float:
        """Return cosine similarity between two category centroids.

        In: a, b (category names). Out: float in [0, 1].

        Возвращает косинусную близость двух центроидов категорий.
        In: a, b (имена категорий). Out: float в [0, 1]."""
        va, vb = self._vec.get(a), self._vec.get(b)
        if va is None or vb is None:
            return 0.0
        return _cos_counter(va, vb) if self.embed_fn is None else _cos_vec(va, vb)

    def nearest(self, src: str, cats) -> Optional[str]:
        """Return the nearest OTHER category to src with deterministic tie-break by name.

        In: src (source category name), cats (iterable of candidate names). Out: str or None.

        Возвращает ближайшую ДРУГУЮ категорию к src (детерм. tie-break по имени).
        In: src (имя исходной категории), cats (итерируемые кандидаты). Out: str или None."""
        cand = [c for c in cats if c != src and c in self._vec]
        if not cand:
            return None
        return sorted(cand, key=lambda c: (-self.sim(src, c), c))[0]


# ─────────────────────────── operators ────────────────────────────────────
class PairFlip(Degrader):
    """A3 pairflip (label-noise): re-label a fraction of terms into the nearest other category.

    k=round(intensity*n) terms are moved; k is fixed, giving the cleanest ARI/AMI curve.
    Acts as a boundary hard negative. quality = 1 − flipped/n_terms.

    A3 pairflip (label-noise): переразметить долю терминов в ближайшую другую категорию.
    k=round(i·n) терминов перемещается; k фиксирован → кривая ARI/AMI самая чистая;
    boundary hard negative. quality = 1 − flipped/n_terms."""
    name, family = "pairflip", "label-noise"

    def _run(self, cfg, intensity, rng, sim, **kw):
        terms = _terms(cfg)
        n = len(terms)
        cats = _category_names(cfg)
        pool = [i for i, t in enumerate(terms) if _primary_cat(t)]
        rng.shuffle(pool)                                 # seeded
        k = min(round(intensity * n), len(pool))
        flipped = []
        for i in sorted(pool[:k]):
            t = terms[i]
            src = _primary_cat(t)
            tgt = sim.nearest(src, cats)
            if tgt is None:
                continue
            _retag_term(t, src, tgt)
            _mark(t, {"op": "pairflip", "from": src, "to": tgt})
            flipped.append(t.get("name"))
        quality = 1.0 - (len(flipped) / n if n else 0.0)
        return quality, flipped, {"n_terms": n, "flipped": len(flipped)}


class Merge(Degrader):
    """B1 merge (granularity): fuse the m nearest category pairs into one.

    Terms from the absorbed category are re-tagged to the survivor (lexicographically smaller
    name); the absorbed category is removed from the top-level list.
    m=max(1, round(intensity * floor(K/2))), greedy by descending similarity.
    quality = (K-m)/K (step-wise label, acceptable). Hurts homogeneity and k-choice.

    B1 merge (granularity): слить m ближайших пар категорий. Термины поглощаемой
    переразметить в выживающую (лексикографически меньшее имя), поглощаемую убрать из
    верхнеуровневого списка. m=max(1,round(i·⌊K/2⌋)), жадно по убыванию близости.
    quality = (K−m)/K (ступенчатая метка). Бьёт по homogeneity, задействует выбор k."""
    name, family = "merge", "granularity"

    def _run(self, cfg, intensity, rng, sim, **kw):
        cats = _category_names(cfg)
        K = len(cats)
        if K < 2:
            return 1.0, [], {"reason": "<2 categories", "K": K}
        m = max(1, round(intensity * (K // 2)))
        ranked = sorted((( -sim.sim(a, b), a, b) for a, b in combinations(sorted(cats), 2)),
                        key=lambda x: (x[0], x[1], x[2]))   # по убыванию близости, детерм.
        used, pairs = set(), []
        for _, a, b in ranked:
            if a in used or b in used:
                continue
            used.update((a, b))
            pairs.append((a, b))
            if len(pairs) >= m:
                break
        corrupted = []
        for a, b in pairs:
            survivor, absorbed = sorted([a, b])           # lexicographically smaller survives
            for t in _terms(cfg):
                if absorbed in loader.get_term_tags(t):
                    _retag_term(t, absorbed, survivor)
                    _mark(t, {"op": "merge", "absorbed": absorbed, "into": survivor})
                    corrupted.append(t.get("name"))
            _remove_category(cfg, absorbed)
        quality = (K - len(pairs)) / K
        return quality, corrupted, {"K": K, "merged_pairs": pairs}


class Distractor(Degrader):
    """C1 distractor (semantic): inject real off-topic terms into a target category.

    bank mode (distractor_bank=[{name, description}]): add bank entries as new members of
    the target. self mode (default): take a real term from a FAR category in the same config
    and re-tag it into the target (genuine term, but off-topic).
    Knobs: intensity = fraction injected/moved; proximity∈[0,1] = similarity of source to
    target (0=far/easy, 1=boundary/hard — keep below the ambiguity point).
    quality = 1 − corrupted/n_after.

    C1 distractor (semantic): реальные off-topic термины в категорию.
    bank-режим: добавить записи банка как новых членов цели.
    self-режим (по умолчанию): взять реальный термин из ДАЛЁКОЙ категории того же конфига
    и переразметить в цель (термин настоящий, но off-topic).
    Ручки: intensity = доля вброшенных/перемещённых; proximity∈[0,1] = близость источника
    к цели (0=далёкий/лёгкий, 1=приграничный/тяжёлый). quality = 1 − corrupted/n_after."""
    name, family = "distractor", "semantic"

    def _run(self, cfg, intensity, rng, sim, *, proximity: float = 0.0,
             distractor_bank=None, target: Optional[str] = None, **kw):
        cats = _category_names(cfg)
        if len(cats) < 2:
            return 1.0, [], {"reason": "<2 categories"}
        T = target or sorted(cats)[0]                     # детерминированная цель
        terms = _terms(cfg)
        n = len(terms)
        corrupted = []

        if distractor_bank:                               # ─ bank-режим
            fld = _dominant_tag_field(cfg)
            k = round(intensity * n)
            bank = list(distractor_bank)
            for j in range(k):
                d = bank[j % len(bank)]
                nt = {"name": d.get("name"), fld: [T]}
                if d.get("description"):
                    nt["description"] = d["description"]
                nt["_degraded"] = [{"op": "distractor", "mode": "bank", "into": T}]
                terms.append(nt)
                corrupted.append(nt["name"])
            n_after = len(terms)
            quality = 1.0 - (k / n_after if n_after else 0.0)
            return quality, corrupted, {"mode": "bank", "added": k, "target": T}

        # ─ self-режим: переместить реальные термины из категорий по proximity-предпочтению
        cand = [(i, t) for i, t in enumerate(terms)
                if T not in loader.get_term_tags(t) and _primary_cat(t)]
        sims = [sim.sim(_primary_cat(t), T) for _, t in cand]
        lo, hi = (min(sims), max(sims)) if sims else (0.0, 0.0)
        scored = []
        for (i, t), s in zip(cand, sims):
            sn = (s - lo) / (hi - lo) if hi > lo else 0.0   # нормировка близости к цели
            pref = -abs(sn - proximity)                     # 0=далёкий … 1=ближний
            scored.append((pref, t.get("name", ""), i))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))      # лучшее предпочтение, детерм.
        k = min(round(intensity * n), len(scored))
        for _, _, i in scored[:k]:
            t = terms[i]
            src = _primary_cat(t)
            _retag_term(t, src, T)
            _mark(t, {"op": "distractor", "mode": "self", "from": src, "into": T})
            corrupted.append(t.get("name"))
        n_after = len(terms)
        quality = 1.0 - (len(corrupted) / n_after if n_after else 0.0)
        return quality, corrupted, {"mode": "self", "moved": len(corrupted),
                                    "target": T, "proximity": proximity}


class Truncate(Degrader):
    """D2 truncate (text-surface): shorten descriptions to the first ceil((1-i)*len) words.

    Names and category assignments are not touched. Monotone, embedder-agnostic, removes
    disambiguating context. quality = 1 − removed_words/total_words.

    D2 truncate (text-surface): усечь описания, оставив первые ceil((1−i)·len) слов.
    Имя и принадлежности не трогаем. Монотонна, embedder-agnostic, убирает разрешающий
    контекст. quality = 1 − removed_words/total_words."""
    name, family = "truncate", "text-surface"

    def _run(self, cfg, intensity, rng, sim, **kw):
        total = removed = 0
        corrupted = []
        for t in _terms(cfg):
            touched = False
            for f in _desc_fields(t):
                words = str(t[f]).split()
                L = len(words)
                total += L
                keep = math.ceil((1.0 - intensity) * L)
                if keep < L:
                    t[f] = " ".join(words[:keep])
                    removed += (L - keep)
                    touched = True
            if touched:
                _mark(t, {"op": "truncate"})
                corrupted.append(t.get("name"))
        quality = 1.0 - (removed / total if total else 0.0)
        return quality, corrupted, {"total_words": total, "removed_words": removed}


# ─────────────────────────── registry + facade ────────────────────────────
DEGRADERS: dict = {d.name: d for d in (PairFlip(), Merge(), Distractor(), Truncate())}


def _normalize_ops(operators, default_intensity):
    """Normalise operator specs to a list of (name, intensity) pairs.

    Accepts: 'pairflip', ('pairflip', 0.3), or a list of such items.
    Out: [(name, intensity), ...].

    Нормализует спецификации операторов в список пар (name, intensity).
    Принимает: 'pairflip', ('pairflip', 0.3) или список таких.
    Out: [(name, intensity), ...]."""
    if isinstance(operators, str) or (isinstance(operators, tuple) and len(operators) == 2
                                      and isinstance(operators[0], str)):
        operators = [operators]
    out = []
    for op in operators:
        if isinstance(op, str):
            out.append((op, float(default_intensity)))
        elif isinstance(op, (tuple, list)) and len(op) == 2:
            out.append((op[0], float(op[1])))
        else:
            raise ValueError(f"bad operator spec: {op!r}")
    return out


def degrade(config, operators, intensity: float = 0.25, *, seed: int = 0,
            embed_fn: Optional[EmbedFn] = None, compose: bool = False, **op_kwargs):
    """Apply one or more degradation operators to a config.

    compose=False: each operator is applied independently to the ORIGINAL (returns a list of
    DegradationResult, or a single result if only one operator). compose=True: chain operators
    (output→input), returning one composite result with quality = product of per-stage
    qualities (independence assumption). Unknown operator name raises KeyError. op_kwargs
    (proximity, distractor_bank, target, ...) are forwarded to each operator.

    In: config, operators ('pairflip' | ('pairflip', 0.3) | list), intensity, seed, embed_fn,
    compose, op_kwargs. Out: DegradationResult or list[DegradationResult].

    Применить оператор(ы) ухудшения к конфигу.
    compose=False: каждый оператор применяется к ОРИГИНАЛУ независимо (список DegradationResult,
    или один результат при одном операторе). compose=True: цепочка (выход→вход), один совокупный
    результат, quality = произведение поэтапных (допущение независимости дефектов). Неизвестный
    оператор → KeyError. op_kwargs прокидываются в операторы.
    In: config, operators, intensity, seed, embed_fn, compose, op_kwargs.
    Out: DegradationResult или list[DegradationResult].
    """
    ops = _normalize_ops(operators, intensity)
    for name, _ in ops:
        if name not in DEGRADERS:
            raise KeyError(name)

    if not compose:
        results = [DEGRADERS[name].apply(config, inten, seed=seed, embed_fn=embed_fn, **op_kwargs)
                   for name, inten in ops]
        return results[0] if len(results) == 1 else results

    cur, q, corrupted, stages = config, 1.0, [], []
    for name, inten in ops:
        r = DEGRADERS[name].apply(cur, inten, seed=seed, embed_fn=embed_fn, **op_kwargs)
        cur = r.config
        q *= r.ground_truth_quality
        corrupted += r.corrupted_terms
        stages.append({"operator": name, "intensity": inten, "quality": r.ground_truth_quality})
    return DegradationResult("+".join(n for n, _ in ops), "composite",
                             float(ops[-1][1]), seed, cur, q, corrupted, {"stages": stages})


def intensity_sweep(config, operator, grid=(0.0, 0.1, 0.25, 0.5, 0.75, 1.0), *,
                    seed: int = 0, embed_fn: Optional[EmbedFn] = None, **op_kwargs):
    """Run a single operator over an intensity grid for tau* calibration.

    In: config, operator (name or spec), grid (sequence of floats), seed, embed_fn, op_kwargs.
    Out: list[DegradationResult] — one result per grid point, each with config and ground_truth_quality.

    Прогон одного оператора по сетке intensity для калибровки tau*.
    In: config, operator, grid, seed, embed_fn, op_kwargs.
    Out: list[DegradationResult] — по одному результату на точку сетки."""
    return [degrade(config, operator, intensity=p, seed=seed, embed_fn=embed_fn, **op_kwargs)
            for p in grid]
