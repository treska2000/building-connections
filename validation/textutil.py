"""textutil.py — детерминированные текст-утилиты (общие для R3/R4).

Токенизация по ресёрч-конфигу morph_leak (2026-06-08):
L=3, snowball-стемминг + помеченный 3-символьный суффикс-токен ("~ase").
Без nltk деградирует до "без стемминга" (suffix-токены остаются) — пишется в note.
"""
from __future__ import annotations
import math
import re
from collections import Counter
from functools import lru_cache

try:
    from nltk.stem.snowball import SnowballStemmer
    _STEM = SnowballStemmer("english").stem
    HAVE_STEMMER = True
except Exception:  # pragma: no cover
    _STEM = lambda t: t  # noqa: E731
    HAVE_STEMMER = False

FUNCTION_WORDS = {"the", "a", "an", "of", "for", "and", "to", "in", "on", "via",
                  "with", "by", "as", "at", "or", "from"}


@lru_cache(maxsize=200000)
def _tok(name: str, L: int, suffix: bool) -> tuple:
    s = (name or "").lower()
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if len(t) >= L and t not in FUNCTION_WORDS]
    out = []
    for t in toks:
        out.append(_STEM(t))
        if suffix and len(t) > 3:
            out.append("~" + t[-3:])
    return tuple(out)


def tokenize(name: str, L: int = 3, suffix: bool = True) -> list[str]:
    """Стем-токены + суффикс-токены (для morph_leak)."""
    return list(_tok(name, L, suffix))


def stem_tokens(name: str, L: int = 3) -> set[str]:
    """Только стем-токены (для faithfulness: термин в тексте)."""
    return {t for t in _tok(name, L, False)}


def build_idf(term_names: list[str], L: int = 3) -> tuple[dict, float]:
    """IDF уровня term: df = в скольких именах терминов конфига встречается токен.
    Считается на ТОМ ЖЕ конфиге, который скорим (config-relative, по ресёрчу)."""
    df: Counter = Counter()
    N = 0
    for name in term_names:
        N += 1
        for t in set(tokenize(name, L)):
            df[t] += 1
    idf = {t: math.log((N + 1) / (d + 1)) + 1.0 for t, d in df.items()}
    idf_max = max(idf.values()) if idf else 1.0
    return idf, idf_max


def token_weight(tok: str, idf: dict, idf_max: float) -> float:
    return idf.get(tok, math.log(2.0)) / idf_max


def term_in_text(term: str, text: str, L: int = 3) -> bool:
    """Faithfulness-проверка: все содержательные стем-токены термина есть в тексте."""
    need = stem_tokens(term, L)
    if not need:
        return False
    have = stem_tokens(text, L)
    return need <= have
