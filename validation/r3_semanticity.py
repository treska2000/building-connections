"""R3 · Семантичность. morph_leak_rate — ресёрч-конфиг 2026-06-08 (детерм.); is_semantic — requires embedder.

Метрика формы (morph_leak): per-link скор «угадывается ли категория по форме слова».
Рекомендация ресёрча (morph_leak_report_2026-06-08, held-out F1=0.858):
  variant=term↔term · L=3 · S=snowball+3-char suffix · w=IDF/idf_max · M=maxweight · τ_link≈0.74.
Старый Jaccard-вариант (τ=0.34) заменён 2026-06-11 по решению Алёны.
"""
from __future__ import annotations
from .loader import get_term_tags
from . import textutil as TU


def leak_score_link(idx: int, member_names: list[str], idf: dict, idf_max: float,
                    L: int = 3) -> float:
    """term↔term · maxweight: максимальный IDF-вес токена термина, разделённого
    хотя бы с одним ДРУГИМ членом той же категории. Неиграбельно переименованием категории."""
    A = TU.tokenize(member_names[idx], L)
    if not A:
        return 0.0
    other = set()
    for j, m in enumerate(member_names):
        if j != idx:
            other |= set(TU.tokenize(m, L))
    shared = [t for t in set(A) if t in other]
    return max((TU.token_weight(t, idf, idf_max) for t in shared), default=0.0)


def run(cfg, members, term_tags, thr) -> dict:
    tau = thr.get("morph_leak_link_tau", 0.74)
    L = thr.get("token_min_len", 3)
    term_names = [t.get("name", "") for t in cfg.get("terms", []) if t.get("name")]
    idf, idf_max = TU.build_idf(term_names, L)

    links = 0
    leaked = 0
    examples = []
    for cat, mset in members.items():
        member_names = sorted(mset)  # детерминированный порядок
        for i, name in enumerate(member_names):
            links += 1
            s = leak_score_link(i, member_names, idf, idf_max, L)
            if s >= tau:
                leaked += 1
                if len(examples) < 8:
                    examples.append({"term": name, "category": cat, "score": round(s, 3)})
    rate = round(leaked / links, 4) if links else 0.0

    note = (f"links={links} leaked={leaked} · term↔term, snowball+suf3, IDF, maxweight, "
            f"τ_link={tau} (ресёрч 2026-06-08, калибруемо)")
    if not TU.HAVE_STEMMER:
        note += " · ⚠️ nltk не установлен — без стемминга (recall morph-ликов ниже)"

    metrics = {
        "morph_leak_rate": {"value": rate, "pass": rate <= thr.get("max_morph_leak", 0.05),
                            "deterministic": True, "requires": None, "gameable": True,
                            "counter": "не покрывает синонимы → подпирает is_semantic (эмбеддер)",
                            "note": note, "examples": examples},
        "is_semantic": {"value": None, "pass": None, "deterministic": False,
                        "requires": "embedder (sem vs morph research)", "gameable": False,
                        "note": "доля связей категория↔термин, семантически близких; на уровне пазла (good_puzzle_yield)"},
    }
    passed = metrics["morph_leak_rate"]["pass"]  # is_semantic — после ресёрча эмбеддера
    return {"requirement": "R3 · Семантичность", "metrics": metrics, "pass": passed}
