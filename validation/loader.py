"""loader.py — загрузка конфига, нормализация tags/axes, дедуп, граф принадлежности.

Принимает три формата:
  · v1 input-schema (`tags`) и старый v1 (`axes`, `decoy_for_axes`);
  · нативный v2-пул генерилки (`schema_version: connections_v2_config_pool`) —
    нормализуется через pool_v2_to_config(): label-категории, tags терминов =
    home_category + candidate_categories, sources = arXiv id → URL.
"""
from __future__ import annotations
import json
import re
from collections import Counter, defaultdict

POOL_V2_SCHEMA = "connections_v2_config_pool"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "config"


def _src_url(s: str) -> str:
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"https://arxiv.org/abs/{s}"  # arXiv id (напр. 2311.06752v1)


def pool_v2_to_config(pool: dict) -> dict:
    """connections_v2_config_pool → v1-подобный конфиг для валидатора.

    Маппинг как в connections_v2.export_input: tags термина = home_category
    первым + candidate_categories (label'ы, дедуп). specialty: у пула это
    строки area/specialty → кладём в {field: "", subfield: specialty, area: area}
    (field пуст: R5 consistency сверяется по area/полю OpenAlex)."""
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
        "specialty": {"field": "", "subfield": pool.get("specialty", ""),
                      "area": pool.get("area", "")},
        "whitelist_sources": pool.get("whitelist_sources", []),
        "tags": [{"name": id2label[c["id"]]} for c in pool.get("categories", [])],
        "terms": terms,
        "_pool_validation": pool.get("validation"),  # self-check генерилки, справочно
    }


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if isinstance(cfg, dict) and cfg.get("schema_version") == POOL_V2_SCHEMA:
        return pool_v2_to_config(cfg)
    return cfg


def _norm(s: str) -> str:
    return " ".join((s or "").lower().strip().split())


def get_term_tags(term: dict) -> list[str]:
    return list(term.get("tags") or term.get("axes") or [])


def get_decoys(term: dict) -> list[str]:
    return list(term.get("decoy_for_tags") or term.get("decoy_for_axes") or [])


def get_category_names(cfg: dict) -> list[str]:
    raw = cfg.get("tags") or cfg.get("axes") or []
    out = []
    for a in raw:
        out.append(a["name"] if isinstance(a, dict) else a)
    return out


def get_area(cfg: dict) -> str | None:
    """specialty бывает объектом {field, subfield, area} (v1) или строкой
    (input-schema из export_input)."""
    sp = cfg.get("specialty")
    if isinstance(sp, str):
        return sp.strip() or None
    return (sp or {}).get("area")


def get_sources(term: dict) -> list[dict]:
    src = term.get("sources") or term.get("evidence") or term.get("top_sources") or []
    out = []
    for s in src:
        if isinstance(s, dict) and s.get("url"):
            out.append({"url": s["url"], "title": s.get("title")})
    return out


def dedup_report(cfg: dict) -> dict:
    """Дубли терминов (по нормализованному имени) и дубли (термин, категория)."""
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
    """members: tag -> set(term_name); term_tags: term_name -> set(tag). После дедупа."""
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
    return Counter({a: len(v) for a, v in members.items()})
