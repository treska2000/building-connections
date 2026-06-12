"""loader.py — загрузка конфига, нормализация tags/axes, дедуп, граф принадлежности.
Принимает три формата: v1 input-schema (`tags`), старый v1 (`axes`) и нативный
v2-пул генерилки (`schema_version: connections_v2_config_pool`) через pool_v2_to_config().

loader.py — config loading, tags/axes normalization, dedup, membership graph.
Accepts three formats: v1 input-schema (`tags`), legacy v1 (`axes`), and the
generator's native v2 pool (`schema_version: connections_v2_config_pool`)
via pool_v2_to_config().
"""
from __future__ import annotations
import json
import re
from collections import Counter, defaultdict

POOL_V2_SCHEMA = "connections_v2_config_pool"


def _slug(text: str) -> str:
    """Kebab-case slug из строки. Вход: text. Выход: str (не пустой).
    Kebab-case slug from a string. In: text. Out: str (never empty)."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "config"


def _src_url(s: str) -> str:
    """arXiv-id → URL; готовые URL — без изменений. Вход: s. Выход: str.
    arXiv id → URL; ready URLs pass through unchanged. In: s. Out: str."""
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"https://arxiv.org/abs/{s}"  # arXiv id, e.g. 2311.06752v1


def pool_v2_to_config(pool: dict) -> dict:
    """Нормализует нативный v2-пул в v1-подобный конфиг (как connections_v2.export_input:
    tags термина = home_category первым + candidate_categories, label'ы, дедуп).
    Вход: pool (dict v2-пула). Выход: dict конфига (+ _pool_validation справочно).
    Normalizes the native v2 pool into a v1-like config (mirroring
    connections_v2.export_input: term tags = home_category first + candidate_categories,
    labels, dedup). In: pool (v2 pool dict). Out: config dict (+ _pool_validation as reference)."""
    id2label = {c.get("id"): (c.get("label") or c.get("id"))
                for c in pool.get("categories", [])}
    terms = []
    for t in pool.get("terms", []):
        ordered = []
        for cid in [t.get("home_category"), *t.get("candidate_categories", [])]:
            if not cid:
                continue
            lab = id2label.get(cid, cid)
            if lab not in ordered:
                ordered.append(lab)
        terms.append({
            "name": t.get("label") or t.get("id"),
            "tags": ordered,
            "description": t.get("description", ""),
            "sources": [{"url": _src_url(s)} for s in t.get("sources", [])],
        })
    return {
        "config_id": _slug(pool.get("topic", "")),
        "config_version": "pool-v2",
        # the pool carries area/specialty as free strings; field stays empty —
        # R5 consistency then relies on the OpenAlex field distribution
        "specialty": {"field": "", "subfield": pool.get("specialty", ""),
                      "area": pool.get("area", "")},
        "whitelist_sources": pool.get("whitelist_sources", []),
        "tags": [{"name": id2label[c["id"]]} for c in pool.get("categories", [])],
        "terms": terms,
        "_pool_validation": pool.get("validation"),  # generator self-check, reference only
    }


def load_config(path: str) -> dict:
    """Читает JSON-конфиг; v2-пул авто-детектится по schema_version и нормализуется.
    Вход: path. Выход: dict конфига.
    Reads a JSON config; a v2 pool is auto-detected by schema_version and normalized.
    In: path. Out: config dict."""
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if isinstance(cfg, dict) and cfg.get("schema_version") == POOL_V2_SCHEMA:
        return pool_v2_to_config(cfg)
    return cfg


def _norm(s: str) -> str:
    """Нормализация имени: lower + схлопывание пробелов. Вход: s. Выход: str.
    Name normalization: lowercase + whitespace collapsing. In: s. Out: str."""
    return " ".join((s or "").lower().strip().split())


def get_term_tags(term: dict) -> list[str]:
    """Категории термина (tags, fallback axes). Вход: term. Выход: list[str].
    A term's categories (tags, falling back to axes). In: term. Out: list[str]."""
    return list(term.get("tags") or term.get("axes") or [])


def get_decoys(term: dict) -> list[str]:
    """Decoy-цели термина (decoy_for_tags, fallback decoy_for_axes). Вход: term. Выход: list[str].
    A term's decoy targets (decoy_for_tags, falling back to decoy_for_axes). In: term. Out: list[str]."""
    return list(term.get("decoy_for_tags") or term.get("decoy_for_axes") or [])


def get_category_names(cfg: dict) -> list[str]:
    """Имена категорий конфига (объекты или строки). Вход: cfg. Выход: list[str].
    The config's category names (objects or bare strings). In: cfg. Out: list[str]."""
    raw = cfg.get("tags") or cfg.get("axes") or []
    out = []
    for a in raw:
        out.append(a["name"] if isinstance(a, dict) else a)
    return out


def get_area(cfg: dict) -> str | None:
    """Область конфига: specialty.area (объект) или сама строка specialty.
    Вход: cfg. Выход: str | None.
    The config's area: specialty.area (object form) or the specialty string itself.
    In: cfg. Out: str | None."""
    sp = cfg.get("specialty")
    if isinstance(sp, str):
        return sp.strip() or None
    return (sp or {}).get("area")


def get_sources(term: dict) -> list[dict]:
    """Источники термина (sources / evidence / top_sources), только записи с url.
    Вход: term. Выход: list[{url, title}].
    A term's sources (sources / evidence / top_sources), url-bearing entries only.
    In: term. Out: list[{url, title}]."""
    src = term.get("sources") or term.get("evidence") or term.get("top_sources") or []
    out = []
    for s in src:
        if isinstance(s, dict) and s.get("url"):
            out.append({"url": s["url"], "title": s.get("title")})
    return out


def dedup_report(cfg: dict) -> dict:
    """Считает дубли: терминов (по нормализованному имени) и рёбер (термин, категория).
    Вход: cfg. Выход: dict {dup_terms, dup_edges}.
    Counts duplicates: of terms (by normalized name) and of (term, category) edges.
    In: cfg. Out: dict {dup_terms, dup_edges}."""
    seen_terms = set()
    dup_terms = 0
    dup_edges = 0
    for t in cfg.get("terms", []):
        name = _norm(t.get("name", ""))
        if not name:
            continue
        if name in seen_terms:
            dup_terms += 1
        seen_terms.add(name)
        seen_edges = set()
        for c in get_term_tags(t):
            e = (name, _norm(c))
            if e in seen_edges:
                dup_edges += 1
            seen_edges.add(e)
    return {"dup_terms": dup_terms, "dup_edges": dup_edges}


def build_membership(cfg: dict):
    """Строит граф принадлежности. Вход: cfg. Выход: (members: tag→set(термины),
    term_tags: термин→set(теги)) — после дедупа по множествам.
    Builds the membership graph. In: cfg. Out: (members: tag→set(terms),
    term_tags: term→set(tags)) — set semantics deduplicate edges."""
    members: dict[str, set[str]] = defaultdict(set)
    term_tags: dict[str, set[str]] = defaultdict(set)
    for t in cfg.get("terms", []):
        name = t.get("name")
        if not name:
            continue
        for c in get_term_tags(t):
            members[c].add(name)
            term_tags[name].add(c)
    return dict(members), dict(term_tags)


def tag_sizes(members: dict[str, set[str]]) -> Counter:
    """Размеры категорий. Вход: members. Выход: Counter tag→размер.
    Category sizes. In: members. Out: Counter tag→size."""
    return Counter({a: len(v) for a, v in members.items()})
