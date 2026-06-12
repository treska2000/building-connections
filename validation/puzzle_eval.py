"""
Качество собранных пазлов для AI Safety Connections.

Один модуль = одна точка правды. Считает 5 метрик по конфигу банка терминов
(configs/*.json) и по реальному генератору (js/puzzle-generator.js):

  1. Пазл собрался + воспроизводимость (через настоящий генератор в Node).
  2. Доля и кол-во категорий с 20+ терминами (слишком широкие — плохо).
  3. Средняя "глубина" (= число терминов) категории.
  4. Доля терминов, попавших хоть в одну широкую категорию (20+).
  5. Термин хоть сколько-то распространён (LLM-as-judge + web search по списку
     источников, с двойной проверкой и калибровкой).

Метрики 1–4 детерминированы и считаются всегда. Метрика 5 требует ANTHROPIC_API_KEY
и пакета anthropic; без них она пропускается с понятным сообщением.

Запуск из CLI:   python validation/puzzle_eval.py configs/category-templates-new.json
Из ноутбука:     from puzzle_eval import run_report; run_report(path)
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

# Категория считается "широкой" начиная с этого числа терминов.
BROAD_THRESHOLD = 20
# Категория попадает в пул генерации пазлов только с >= 4 терминами (см. генератор).
MIN_GENERATABLE = 4

# Список источников для метрики 5: по ним LLM проверяет распространённость термина.
# Это canon AI-safety + энциклопедии. Правится под задачу.
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
# Загрузка и нормализация конфига
# --------------------------------------------------------------------------- #
def _pick_lang(value, lang: str):
    """Достаёт значение из multilang-поля {en:..., ru:...} или возвращает как есть."""
    if isinstance(value, dict):
        return value.get(lang, next(iter(value.values())))
    return value


def load_terms(path: str | Path, lang: str = "en") -> list[dict]:
    """
    Приводит любой из трёх форматов конфига к плоскому списку терминов:
      {name: str, description: str, tags: [str, ...]}

      A. flat        : {name, description, tags:[...]}            (category-templates-new.json)
      B. multilang   : {name:{en,ru}, description:{...}, tags:{en:[...],ru:[...]}}  (evals-*.json)
      C. legacy group: {name, members:[...]}                      (category-templates.json)
         -> каждый member становится термином с тегом = name группы.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    terms: list[dict] = []
    for e in raw:
        if "members" in e and "tags" not in e:  # формат C
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
    """tag -> список имён терминов с этим тегом."""
    idx: dict[str, list[str]] = defaultdict(list)
    for t in terms:
        for tag in t["tags"]:
            idx[tag].append(t["name"])
    return dict(idx)


# --------------------------------------------------------------------------- #
# Метрики 2, 3, 4 — структура банка терминов
# --------------------------------------------------------------------------- #
@dataclass
class StructuralResult:
    n_terms: int
    n_tags: int
    category_sizes: dict[str, int]
    generatable_tags: int                 # тегов с >= MIN_GENERATABLE терминов
    broad_categories: list[str]           # метрика 2: список широких (>= BROAD_THRESHOLD)
    broad_count: int                      # метрика 2: количество
    broad_share: float                    # метрика 2: доля от всех тегов
    avg_category_depth: float             # метрика 3: средняя глубина (терминов на категорию)
    median_category_depth: float
    terms_in_broad: list[str]             # метрика 4: термины в >=1 широкой категории
    terms_in_broad_share: float           # метрика 4: их доля

    def summary(self) -> str:
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
# Метрика 1 — пазл собрался + воспроизводимость (настоящий генератор в Node)
# --------------------------------------------------------------------------- #
@dataclass
class AssemblyResult:
    mode: str
    seed: str
    assembles: bool
    reproducible: bool
    board_complete: bool          # 16 плиток на доске
    one_concept_one_category: bool
    golden_hash: str | None
    golden_match: bool | None     # совпадает ли с зафиксированным эталоном
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.assembles and self.reproducible and self.board_complete
                    and self.one_concept_one_category)


def _run_node(config_path: str | Path, mode: str, seed: str) -> dict:
    res = subprocess.run(
        ["node", str(HERE / "repro_check.mjs"), str(config_path), mode, seed],
        capture_output=True, text=True, cwd=ROOT,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(f"node harness failed: {res.stderr or res.stdout}")
    return json.loads(res.stdout)


def _golden_path() -> Path:
    return HERE / "golden.json"


def assembly_metrics(
    config_path: str | Path,
    modes: tuple[str, ...] = ("normal", "advanced"),
    seed: str = "golden-seed-1",
    update_golden: bool = False,
) -> list[AssemblyResult]:
    """
    Гоняет реальный генератор. Воспроизводимость = два прогона с одним сидом дали
    идентичный пазл. Дополнительно сверяет с эталоном (golden.json): это и есть
    гарантия "все ученики получат ровно этот тест" — если хэш изменился, конфиг
    или код поменялись и пазл стал другим.
    """
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
# Метрика 5 — распространённость термина (LLM-as-judge + web search)
# --------------------------------------------------------------------------- #
# Калибровочный набор: на нём ПЕРЕД доверием судье проверяем, что он различает
# реальные термины и выдуманные ("дважды проверить, что LLM найдёт и насудит").
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
    """Один проход судьи с web search. stance меняет установку для двойной проверки."""
    if stance == "researcher":
        role = ("Determine whether the term is a genuinely established concept in "
                "AI safety / alignment / ML. Search the web before deciding.")
    else:  # skeptic: пытается опровергнуть
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
    # повторяем, пока модель не вызовет verdict (после web_search ходов)
    convo = [{"role": "user", "content": prompt}]
    for _ in range(4):
        verdict = next((b for b in msg.content
                        if getattr(b, "type", "") == "tool_use" and b.name == "verdict"), None)
        if verdict:
            return verdict.input
        if msg.stop_reason != "tool_use":
            break
        convo.append({"role": "assistant", "content": msg.content})
        # web_search — серверный инструмент, его результат уже в msg; просто продолжаем
        msg = client.messages.create(
            model=model, max_tokens=1500, tools=tools,
            tool_choice={"type": "auto"}, messages=convo,
        )
    return {"recognized": False, "confidence": 0.0, "reason": "no verdict returned",
            "evidence_urls": []}


def judge_term(client, model, term, description, sources=DEFAULT_SOURCES):
    """
    Двойная проверка: independent 'researcher' + 'skeptic'.
    recognized=True только если ОБА согласны. Расхождение -> на ручную проверку.
    """
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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("нет ANTHROPIC_API_KEY — метрика 5 пропущена")
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("нет пакета anthropic (pip install anthropic) — метрика 5 пропущена") from e
    return anthropic.Anthropic()


def calibrate_judge(model="claude-sonnet-4-6", sources=DEFAULT_SOURCES) -> dict:
    """
    Прогоняет судью по заведомо реальным и заведомо выдуманным терминам.
    Возвращает точность — это и есть "дважды проверить, что LLM не врёт".
    Доверять метрике 5 только если real-recall и fake-rejection близки к 1.0.
    """
    client = _make_client()
    real = [judge_term(client, model, t, "", sources) for t in CALIBRATION_REAL]
    fake = [judge_term(client, model, t, "", sources) for t in CALIBRATION_FAKE]
    real_recall = sum(x["recognized"] for x in real) / len(real)
    fake_reject = sum(not x["recognized"] for x in fake) / len(fake)
    return {
        "real_recall": real_recall,          # доля реальных, опознанных как реальные
        "fake_rejection": fake_reject,       # доля фейков, отбитых как фейки
        "trustworthy": real_recall >= 0.83 and fake_reject >= 0.75,
        "real_detail": real, "fake_detail": fake,
    }


def recognizability_metrics(terms, model="claude-sonnet-4-6", sources=DEFAULT_SOURCES) -> dict:
    """Метрика 5 по всем терминам конфига. Требует ключ + anthropic."""
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
# Полный перебор пазлов из конфига (комбинаторно, без перебора сидов)
# --------------------------------------------------------------------------- #
def enumerate_solutions(terms: list[dict], mode: str = "normal", with_difficulty: bool = False):
    """
    Возвращает ПОЛНЫЙ список различных пазлов-решений из конфига — точно и
    engine-независимо, без спама сидами.

    "Решение" = какие категории и какие их 4 термина (для advanced — только 3
    категории; ловушки на доске игнорируются, они лишь шум). Это то, что препод
    реально считает "разными тестами".

    with_difficulty=False -> группировка (множество категорий), порядок неважен.
    with_difficulty=True  -> ещё и привязка сложности (упорядоченный кортеж).

    Игнорируется: порядок плиток на доске и порядок терминов внутри группы
    (косметика, раздувает пространство до ~бесконечности, для теста бессмысленна).

    Формула числа группировок:
        sum по всем k-подмножествам S категорий-кандидатов (size>=4)
            из произведения C(size_t, 4) по t in S,
        где k=4 (normal) / 3 (advanced).
    С учётом сложности: каждое умножается на k! (число раскладок по сложностям).
    """
    from itertools import combinations, product, permutations

    k = 4 if mode == "normal" else 3
    idx = build_tag_index(terms)
    candidates = {tag: names for tag, names in idx.items() if len(names) >= MIN_GENERATABLE}
    tags = sorted(candidates)

    solutions = set()
    for tag_combo in combinations(tags, k):
        # для каждого тега — все способы выбрать 4 термина
        per_tag_choices = [
            [frozenset(c) for c in combinations(sorted(candidates[t]), 4)]
            for t in tag_combo
        ]
        for groups in product(*per_tag_choices):
            if with_difficulty:
                # каждая раскладка групп по сложностям = отдельный пазл
                for perm in permutations(groups):
                    solutions.add(tuple(perm))
            else:
                solutions.add(frozenset(groups))
    return solutions


def enumeration_report(terms: list[dict]) -> dict:
    """Считает размеры пространства пазлов во всех представлениях."""
    from math import comb, perm as nperm
    idx = build_tag_index(terms)
    cand_sizes = sorted([len(v) for v in idx.values() if len(v) >= MIN_GENERATABLE], reverse=True)
    out = {"candidate_tags": len(cand_sizes), "candidate_sizes": cand_sizes}
    for mode in ("normal", "advanced"):
        sols = enumerate_solutions(terms, mode, with_difficulty=False)
        out[mode] = {
            "groupings": len(sols),                              # уникальные тесты по смыслу
            "with_difficulty": len(sols) * (24 if mode == "normal" else 6),
        }
    return out


# --------------------------------------------------------------------------- #
# Сводный отчёт
# --------------------------------------------------------------------------- #
def run_report(config_path, lang="en", run_metric5=False, model="claude-sonnet-4-6",
               sources=DEFAULT_SOURCES, update_golden=False) -> dict:
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
