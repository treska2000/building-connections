"""textutil.py — deterministic text utilities (shared by R3/R4): tokenizer of the
morph_leak research config (Snowball stem + 3-char suffix token), IDF, term_in_text.
Without nltk it degrades to "no stemming" (suffix tokens remain).

textutil.py — детерминированные текст-утилиты (общие для R3/R4): токенизация
ресёрч-конфига morph_leak (snowball-стем + 3-символьный суффикс), IDF, term_in_text.
Без nltk деградирует до «без стемминга» (суффикс-токены остаются).
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
    """Cached tokenization: lowercase, cleanup, stem + optional suffix tokens.

    In: name, L (min word length), suffix (flag). Out: tuple[str].

    Кэшируемая токенизация: lower, чистка, стем + опц. суффикс-токены.
    In: name, L (мин. длина слова), suffix (флаг). Out: tuple[str]."""
    s = (name or "").lower()
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if len(t) >= L and t not in FUNCTION_WORDS]
    out = []
    for t in toks:
        out.append(_STEM(t))
        if suffix and len(t) > 3:
            out.append("~" + t[-3:])  # marked suffix token, e.g. "~ase"
    return tuple(out)


def tokenize(name: str, L: int = 3, suffix: bool = True) -> list[str]:
    """Return stem tokens + suffix tokens (for morph_leak).

    In: name, L, suffix. Out: list[str].

    Стем-токены + суффикс-токены (для morph_leak).
    In: name, L, suffix. Out: list[str]."""
    return list(_tok(name, L, suffix))


def stem_tokens(name: str, L: int = 3) -> set[str]:
    """Return stem tokens only (for the faithfulness check).

    In: name, L. Out: set[str].

    Только стем-токены (для faithfulness-проверки).
    In: name, L. Out: set[str]."""
    return {t for t in _tok(name, L, False)}


def build_idf(term_names: list[str], L: int = 3) -> tuple[dict, float]:
    """Term-level IDF over this config's term names (config-relative):
    df = number of term names containing the token.

    In: term_names, L. Out: (idf dict, idf_max).

    IDF уровня term по именам терминов ЭТОГО конфига (config-relative, по ресёрчу):
    df = в скольких именах встречается токен.
    In: term_names, L. Out: (idf dict, idf_max)."""
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
    """Return the normalized IDF weight of a token in [0, ~1].

    In: tok, idf, idf_max. Out: float.

    Нормированный IDF-вес токена в [0, ~1].
    In: tok, idf, idf_max. Out: float."""
    return idf.get(tok, math.log(2.0)) / idf_max


def term_in_text(term: str, text: str, L: int = 3) -> bool:
    """Faithfulness check: every content stem token of the term occurs in the text.

    In: term, text, L. Out: bool.

    Faithfulness: все содержательные стем-токены термина присутствуют в тексте.
    In: term, text, L. Out: bool."""
    need = stem_tokens(term, L)
    if not need:
        return False
    have = stem_tokens(text, L)
    return need <= have
