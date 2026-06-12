"""puzzle_eval.py — НАСЛЕДИЕ: качество банка терминов ЖИВОЙ игры (старые форматы
configs/*.json), 5 метрик: M1 сборка+воспроизводимость (реальный Node-генератор,
сверка с эталоном golden.json), M2–M4 структура банка (широкие категории, глубина),
M5 распространённость термина (LLM-as-judge + web search, двойная проверка,
калибровка). Не часть приёмки v1/v2-конфигов (это acceptance.py) — инструмент
для sampling_test.ipynb. M5 требует ANTHROPIC_API_KEY + пакет anthropic.

puzzle_eval.py — LEGACY: quality of the LIVE game's term bank (old configs/*.json
formats), 5 metrics: M1 assembly+reproducibility (the real Node generator, checked
against the golden.json baseline), M2–M4 bank structure (broad categories, depth),
M5 term recognizability (LLM-as-judge + web search, double-checked, calibrated).
Not part of v1/v2 config acceptance (that is acceptance.py) — a tool for
sampling_test.ipynb. M5 needs ANTHROPIC_API_KEY + the anthropic package.

CLI:      python validation/puzzle_eval.py configs/category-templates-new.json
Notebook: from puzzle_eval import run_report; run_report(path)
"""
from __future__ import annotations

import json
import os
import subprocess
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# A category counts as "broad" starting from this many terms.
BROAD_THRESHOLD = 20
# A category enters the puzzle-generation pool only with >= 4 terms (see the generator).
MIN_GENERATABLE = 4

# Source list for metric 5: the LLM checks term prevalence against these.
# AI-safety canon + encyclopedias; adjust per task.
DEFAULT_SOURCES = [
    "arxiv.org",
    "alignmentforum.org",
    "lesswrong.com",
    "aisafety.info",
    "en.wikipedia.org",
    "distill.pub",
    "anthropic.com",
    "deepmind.google",
    "openai.com",
    "80000hours.org",
]


# --------------------------------------------------------------------------- #
# Config loading and normalization
# --------------------------------------------------------------------------- #
def _pick_lang(value, lang: str):
    """Значение multilang-поля {en, ru} для языка (или как есть). Вход: value, lang. Выход: значение.
    The {en, ru} multilang field value for a language (or as-is). In: value, lang. Out: value."""
    if isinstance(value, dict):
        return value.get(lang, next(iter(value.values())))
    return value


def load_terms(path: str | Path, lang: str = "en") -> list[dict]:
    """Любой из трёх форматов банка → плоский список {name, description, tags}.
    A flat (category-templates-new) · B multilang (evals-*) · C legacy group
    (category-templates: каждый member → термин с тегом = имя группы).
    Вход: path, lang. Выход: list[dict].
    Any of the three bank formats → a flat list of {name, description, tags}.
    A flat (category-templates-new) · B multilang (evals-*) · C legacy group
    (category-templates: each member → a term tagged with the group name).
    In: path, lang. Out: list[dict]."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    terms: list[dict] = []
    for e in raw:
        if "members" in e and "tags" not in e:  # format C
            group = str(_pick_lang(e.get("name"), lang))
            for m in e["members"]:
                terms.append({"name": str(_pick_lang(m, lang)), "description": "", "tags": [group]})
            continue
        name = _pick_lang(e.get("name"), lang)
        if name is None:
            continue
        desc = _pick_lang(e.get("description"), lang) or ""
        tags = _pick_lang(e.get("tags"), lang) or []
        if not isinstance(tags, list):
            tags = [tags]
        terms.append({"name": str(name), "description": str(desc), "tags": [str(t) for t in tags]})
    return terms


def build_tag_index(terms: list[dict]) -> dict[str, list[str]]:
    """Индекс tag → имена терминов с этим тегом. Вход: terms. Выход: dict.
    Index tag → names of terms carrying the tag. In: terms. Out: dict."""
    idx: dict[str, list[str]] = defaultdict(list)
    for t in terms:
        for tag in t["tags"]:
            idx[tag].append(t["name"])
    return dict(idx)


# --------------------------------------------------------------------------- #
# Metrics 2, 3, 4 — term-bank structure
# --------------------------------------------------------------------------- #
@dataclass
class StructuralResult:
    n_terms: int
    n_tags: int
    category_sizes: dict[str, int]
    generatable_tags: int                 # tags with >= MIN_GENERATABLE terms
    broad_categories: list[str]           # metric 2: broad tags (>= BROAD_THRESHOLD)
    broad_count: int                      # metric 2: how many
    broad_share: float                    # metric 2: share of all tags
    avg_category_depth: float             # metric 3: mean depth (terms per category)
    median_category_depth: float
    terms_in_broad: list[str]             # metric 4: terms in >=1 broad category
    terms_in_broad_share: float           # metric 4: their share

    def summary(self) -> str:
        """Человекочитаемая сводка M2–M4. Выход: str. / Human-readable M2–M4 summary. Out: str."""
        return (
            f"терминов={self.n_terms}, категорий={self.n_tags} "
            f"(генерируемых≥{MIN_GENERATABLE}: {self.generatable_tags})\n"
            f"  M2 широкие категории (≥{BROAD_THRESHOLD}): {self.broad_count} шт "
            f"({self.broad_share:.0%}) -> {self.broad_categories or '—'}\n"
            f"  M3 средняя глубина категории: {self.avg_category_depth:.2f} "
            f"(медиана {self.median_category_depth:.1f})\n"
            f"  M4 доля терминов в широкой категории: {self.terms_in_broad_share:.0%} "
            f"({len(self.terms_in_broad)} шт)"
        )


def structural_metrics(terms: list[dict]) -> StructuralResult:
    """Считает M2–M4 по банку терминов. Вход: terms. Выход: StructuralResult.
    Computes M2–M4 over the term bank. In: terms. Out: StructuralResult."""
    idx = build_tag_index(terms)
    sizes = {tag: len(names) for tag, names in idx.items()}
    n_tags = len(sizes)
    broad = sorted([tag for tag, n in sizes.items() if n >= BROAD_THRESHOLD])
    broad_set = set(broad)
    terms_in_broad = sorted({t["name"] for t in terms if broad_set & set(t["tags"])})
    depths = list(sizes.values()) or [0]
    return StructuralResult(
        n_terms=len(terms),
        n_tags=n_tags,
        category_sizes=dict(sorted(sizes.items(), key=lambda kv: -kv[1])),
        generatable_tags=sum(1 for n in sizes.values() if n >= MIN_GENERATABLE),
        broad_categories=broad,
        broad_count=len(broad),
        broad_share=(len(broad) / n_tags) if n_tags else 0.0,
        avg_category_depth=statistics.mean(depths),
        median_category_depth=statistics.median(depths),
        terms_in_broad=terms_in_broad,
        terms_in_broad_share=(len(terms_in_broad) / len(terms)) if terms else 0.0,
    )


# --------------------------------------------------------------------------- #
# Metric 1 — the puzzle assembles + reproducibility (the real Node generator)
# --------------------------------------------------------------------------- #
@dataclass
class AssemblyResult:
    mode: str
    seed: str
    assembles: bool
    reproducible: bool
    board_complete: bool          # 16 tiles on the board
    one_concept_one_category: bool
    golden_hash: str | None
    golden_match: bool | None     # does it match the recorded baseline
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.assembles and self.reproducible and self.board_complete
                    and self.one_concept_one_category)


def _run_node(config_path: str | Path, mode: str, seed: str) -> dict:
    """Запускает repro_check.mjs и парсит JSON. Вход: config_path, mode, seed. Выход: dict.
    Runs repro_check.mjs and parses its JSON. In: config_path, mode, seed. Out: dict."""
    res = subprocess.run(
        ["node", str(HERE / "repro_check.mjs"), str(config_path), mode, seed],
        capture_output=True, text=True, cwd=ROOT,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(f"node harness failed: {res.stderr or res.stdout}")
    return json.loads(res.stdout)


def _golden_path() -> Path:
    """Путь к файлу эталонных хэшей. Выход: Path. / Path to the baseline-hash file. Out: Path."""
    return HERE / "golden.json"


def assembly_metrics(
    config_path: str | Path,
    modes: tuple[str, ...] = ("normal", "advanced"),
    seed: str = "golden-seed-1",
    update_golden: bool = False,
) -> list[AssemblyResult]:
    """M1: гоняет реальный генератор; воспроизводимость = два прогона одного сида
    идентичны; сверка с эталоном golden.json (хэш изменился → пазл стал другим).
    Вход: config_path, modes, seed, update_golden. Выход: list[AssemblyResult].
    M1: drives the real generator; reproducibility = two same-seed runs are identical;
    checked against the golden.json baseline (hash changed → the puzzle changed).
    In: config_path, modes, seed, update_golden. Out: list[AssemblyResult]."""
    golden = {}
    gp = _golden_path()
    if gp.exists():
        golden = json.loads(gp.read_text())

    results: list[AssemblyResult] = []
    new_golden = dict(golden)
    for mode in modes:
        d = _run_node(config_path, mode, seed)
        key = f"{Path(config_path).name}|{mode}|{seed}"
        ghash = d.get("golden_hash")
        prev = golden.get(key)
        match = (prev == ghash) if (prev and not update_golden) else None
        if update_golden or prev is None:
            new_golden[key] = ghash
        results.append(AssemblyResult(
            mode=mode, seed=seed,
            assembles=bool(d.get("assembles")),
            reproducible=bool(d.get("reproducible")),
            board_complete=bool(d.get("boardComplete")),
            one_concept_one_category=bool(d.get("oneConceptOneCategory")),
            golden_hash=ghash, golden_match=match, raw=d,
        ))
    if update_golden or new_golden != golden:
        gp.write_text(json.dumps(new_golden, indent=2))
    return results


# --------------------------------------------------------------------------- #
# Metric 5 — term recognizability (LLM-as-judge + web search)
# --------------------------------------------------------------------------- #
# Calibration set: BEFORE trusting the judge we verify it separates real terms
# from invented ones ("double-check that the LLM both finds and judges").
CALIBRATION_REAL = [
    "Reward Hacking", "Instrumental Convergence", "Mesa-optimization",
    "Goodhart's Law", "Interpretability", "Corrigibility",
]
CALIBRATION_FAKE = [
    "Recursive Gradient Sublimation", "Hyperbolic Value Annealing",
    "Quantum Corrigibility Drift", "Latent Paperclip Entropy",
]

_JUDGE_SCHEMA = {
    "name": "verdict",
    "description": "Вердикт о распространённости термина.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recognized": {"type": "boolean",
                           "description": "true, если термин — реально используемое понятие AI safety/ML"},
            "confidence": {"type": "number", "description": "0..1"},
            "evidence_urls": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["recognized", "confidence", "reason"],
    },
}


def _judge_once(client, model, term, description, sources, stance="researcher"):
    """Один проход судьи с web search; stance задаёт роль для двойной проверки.
    Вход: client, model, term, description, sources, stance. Выход: dict вердикта.
    One judge pass with web search; stance sets the role for the double check.
    In: client, model, term, description, sources, stance. Out: verdict dict."""
    if stance == "researcher":
        role = ("Determine whether the term is a genuinely established concept in "
                "AI safety / alignment / ML. Search the web before deciding.")
    else:  # skeptic: tries to refute
        role = ("You are a skeptic. Try to show the term is NOT an established concept "
                "(possibly invented or fringe). Search the web. Only concede "
                "'recognized: true' if evidence is clearly there.")
    prompt = (
        f"{role}\n\n"
        f"Term: {term!r}\n"
        f"Provided definition (may be wrong): {description!r}\n\n"
        f"Rely on reputable sources such as: {', '.join(sources)}.\n"
        f"Then call the `verdict` tool with your judgement."
    )
    tools = [
        {"type": "web_search_20250305", "name": "web_search",
         "max_uses": 4, "allowed_domains": sources},
        _JUDGE_SCHEMA,
    ]
    msg = client.messages.create(
        model=model, max_tokens=1500, tools=tools,
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": prompt}],
    )
    # loop until the model calls verdict (after its web_search turns)
    convo = [{"role": "user", "content": prompt}]
    for _ in range(4):
        verdict = next((b for b in msg.content
                        if getattr(b, "type", "") == "tool_use" and b.name == "verdict"), None)
        if verdict:
            return verdict.input
        if msg.stop_reason != "tool_use":
            break
        convo.append({"role": "assistant", "content": msg.content})
        # web_search is a server-side tool, its result is already in msg; just continue
        msg = client.messages.create(
            model=model, max_tokens=1500, tools=tools,
            tool_choice={"type": "auto"}, messages=convo,
        )
    return {"recognized": False, "confidence": 0.0, "reason": "no verdict returned",
            "evidence_urls": []}


def judge_term(client, model, term, description, sources=DEFAULT_SOURCES):
    """Двойная проверка термина: независимые researcher + skeptic; recognized=True
    только при согласии ОБОИХ, расхождение → ручная проверка. Вход: client, model,
    term, description, sources. Выход: dict вердикта.
    Double-checks a term: independent researcher + skeptic; recognized=True only when
    BOTH agree, disagreement → human review. In: client, model, term, description,
    sources. Out: verdict dict."""
    r = _judge_once(client, model, term, description, sources, "researcher")
    s = _judge_once(client, model, term, description, sources, "skeptic")
    agree = r["recognized"] == s["recognized"]
    return {
        "term": term,
        "recognized": bool(r["recognized"] and s["recognized"]),
        "agreement": agree,
        "needs_human": not agree,
        "researcher": r,
        "skeptic": s,
    }


def _make_client():
    """Клиент Anthropic для M5 (требует ключ и пакет). Выход: client | RuntimeError.
    Anthropic client for M5 (needs the key and the package). Out: client | RuntimeError."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("нет ANTHROPIC_API_KEY — метрика 5 пропущена")
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("нет пакета anthropic (pip install anthropic) — метрика 5 пропущена") from e
    return anthropic.Anthropic()


def calibrate_judge(model="claude-sonnet-4-6", sources=DEFAULT_SOURCES) -> dict:
    """Калибровка судьи на заведомо реальных/выдуманных терминах; доверять M5 только
    при real_recall и fake_rejection ≈ 1.0. Вход: model, sources. Выход: dict точностей.
    Calibrates the judge on known-real/known-fake terms; trust M5 only when
    real_recall and fake_rejection are ≈ 1.0. In: model, sources. Out: accuracy dict."""
    client = _make_client()
    real = [judge_term(client, model, t, "", sources) for t in CALIBRATION_REAL]
    fake = [judge_term(client, model, t, "", sources) for t in CALIBRATION_FAKE]
    real_recall = sum(x["recognized"] for x in real) / len(real)
    fake_reject = sum(not x["recognized"] for x in fake) / len(fake)
    return {
        "real_recall": real_recall,          # share of real terms recognized as real
        "fake_rejection": fake_reject,       # share of fakes rejected as fakes
        "trustworthy": real_recall >= 0.83 and fake_reject >= 0.75,
        "real_detail": real, "fake_detail": fake,
    }


def recognizability_metrics(terms, model="claude-sonnet-4-6", sources=DEFAULT_SOURCES) -> dict:
    """M5 по всем терминам банка (требует ключ + anthropic). Вход: terms, model, sources.
    Выход: dict {recognized_share, suspicious_terms, needs_human_review, verdicts}.
    M5 over every bank term (needs the key + anthropic). In: terms, model, sources.
    Out: dict {recognized_share, suspicious_terms, needs_human_review, verdicts}."""
    client = _make_client()
    verdicts = [judge_term(client, model, t["name"], t["description"], sources) for t in terms]
    recognized = [v for v in verdicts if v["recognized"]]
    suspicious = [v["term"] for v in verdicts if not v["recognized"]]
    needs_human = [v["term"] for v in verdicts if v["needs_human"]]
    return {
        "recognized_share": len(recognized) / len(terms) if terms else 0.0,
        "suspicious_terms": suspicious,
        "needs_human_review": needs_human,
        "verdicts": verdicts,
    }


# --------------------------------------------------------------------------- #
# Full combinatorial enumeration of puzzles from a config (no seed sweeps)
# --------------------------------------------------------------------------- #
def enumerate_solutions(terms: list[dict], mode: str = "normal", with_difficulty: bool = False):
    """ПОЛНЫЙ перебор различных пазлов-решений конфига, engine-независимо (без сидов).
    «Решение» = выбранные категории + их четвёрки терминов (advanced: 3 категории,
    ловушки игнорируются); порядок плиток/терминов — косметика, не учитывается.
    Вход: terms, mode, with_difficulty (упорядочить группы по сложностям, ×k!).
    Выход: set решений.
    FULL enumeration of distinct puzzle solutions of a config, engine-independent
    (no seed sweeps). A "solution" = the chosen categories + their 4-term groups
    (advanced: 3 categories, board decoys ignored); tile/term order is cosmetic and
    ignored. In: terms, mode, with_difficulty (order groups by difficulty, ×k!).
    Out: set of solutions."""
    from itertools import combinations, product, permutations

    k = 4 if mode == "normal" else 3
    idx = build_tag_index(terms)
    candidates = {tag: names for tag, names in idx.items() if len(names) >= MIN_GENERATABLE}
    tags = sorted(candidates)

    solutions = set()
    for tag_combo in combinations(tags, k):
        # for every tag — all the ways to pick 4 of its terms
        per_tag_choices = [
            [frozenset(c) for c in combinations(sorted(candidates[t]), 4)]
            for t in tag_combo
        ]
        for groups in product(*per_tag_choices):
            if with_difficulty:
                # every assignment of groups to difficulty slots = a distinct puzzle
                for perm in permutations(groups):
                    solutions.add(tuple(perm))
            else:
                solutions.add(frozenset(groups))
    return solutions


def enumeration_report(terms: list[dict]) -> dict:
    """Размеры пространства пазлов во всех представлениях. Вход: terms. Выход: dict.
    Puzzle-space sizes across representations. In: terms. Out: dict."""
    from math import comb, perm as nperm
    idx = build_tag_index(terms)
    cand_sizes = sorted([len(v) for v in idx.values() if len(v) >= MIN_GENERATABLE], reverse=True)
    out = {"candidate_tags": len(cand_sizes), "candidate_sizes": cand_sizes}
    for mode in ("normal", "advanced"):
        sols = enumerate_solutions(terms, mode, with_difficulty=False)
        out[mode] = {
            "groupings": len(sols),                              # semantically unique tests
            "with_difficulty": len(sols) * (24 if mode == "normal" else 6),
        }
    return out


# --------------------------------------------------------------------------- #
# Summary report
# --------------------------------------------------------------------------- #
def run_report(config_path, lang="en", run_metric5=False, model="claude-sonnet-4-6",
               sources=DEFAULT_SOURCES, update_golden=False) -> dict:
    """Сводный отчёт M1–M5 по банку (печатает и возвращает). Вход: config_path + опции.
    Выход: dict {config, terms, assembly, structural, recognizability}.
    Summary M1–M5 report over the bank (prints and returns). In: config_path + options.
    Out: dict {config, terms, assembly, structural, recognizability}."""
    config_path = str(config_path)
    terms = load_terms(config_path, lang=lang)

    print(f"=== {Path(config_path).name}  (lang={lang}) ===")
    # M1
    print("\n[M1] Сборка + воспроизводимость:")
    asm = assembly_metrics(config_path, update_golden=update_golden)
    for a in asm:
        gm = {True: "эталон ✓", False: "ЭТАЛОН РАЗОШЁЛСЯ ✗", None: "эталон зафиксирован"}[a.golden_match]
        print(f"  {a.mode:9s}: собрался={a.assembles} воспроизводим={a.reproducible} "
              f"доска16={a.board_complete} 1термин-1кат={a.one_concept_one_category} "
              f"| hash={a.golden_hash} {gm}")

    # M2-M4
    st = structural_metrics(terms)
    print("\n[M2–M4] Структура банка:")
    print("  " + st.summary().replace("\n", "\n  "))

    # M5
    m5 = None
    print("\n[M5] Распространённость терминов:")
    if run_metric5:
        try:
            m5 = recognizability_metrics(terms, model=model, sources=sources)
            print(f"  опознано: {m5['recognized_share']:.0%}; "
                  f"подозрительные: {m5['suspicious_terms'] or '—'}; "
                  f"на ручную проверку: {m5['needs_human_review'] or '—'}")
        except RuntimeError as e:
            print(f"  пропущено: {e}")
    else:
        print("  пропущено (run_metric5=False)")

    return {"config": config_path, "terms": terms,
            "assembly": asm, "structural": st, "recognizability": m5}


if __name__ == "__main__":
    import sys
    cfg = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "configs" / "category-templates-new.json")
    lang = sys.argv[2] if len(sys.argv) > 2 else "en"
    run_report(cfg, lang=lang, run_metric5=bool(os.environ.get("ANTHROPIC_API_KEY")))
