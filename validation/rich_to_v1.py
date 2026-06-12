"""rich_to_v1.py — адаптер старого rich-формата генерилки (seeds/*.json,
serialize_pool) в схему v1. Для нативного v2-пула адаптер не нужен —
его нормализует loader.pool_v2_to_config.

rich_to_v1.py — adapter of the generator's legacy rich format (seeds/*.json,
serialize_pool) into the v1 schema. The native v2 pool needs no adapter —
loader.pool_v2_to_config normalizes it.

Rich pool shape:
    {
      "config_id":   str,
      "domain":      str,          // field
      "language":    str,
      "meta":        {"subdomain": str, "purpose": str, ...},
      "categories":  [{"id", "tag", "kind", "difficulty", "rationale"?}, ...],
      "terms":       [{"term", "description",
                       "home_category":  cat_id,
                       "candidate_categories": [cat_id, ...],
                       "sources": [arxiv_id_or_string, ...]}, ...]
    }

v1 mapping:
    rich.config_id         -> v1.config_id
    rich.domain            -> v1.specialty.field
    rich.meta.subdomain    -> v1.specialty.subfield, v1.specialty.area
    categories[].tag       -> axes[].name (rationale -> description_en)
    terms[].term           -> terms[].name (description -> description_en)
    candidate_categories   -> terms[].axes (cat_id -> tag, home first)
    sources                -> terms[].evidence (arXiv URLs)
    whitelist_sources      -> ["arxiv.org"]  (corpus is arxiv-anchored)

Decoys (decoy_for_axes) are NOT present in the pool format and stay empty,
which means mode-3 will fail in default acceptance. Relax the gate set if
that's the configuration the generator was supposed to produce.
"""
import json
import re
from pathlib import Path


ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5})\b")


def _arxiv_url(source):
    """Строка источника генерилки → arXiv-URL; None для плейсхолдеров/без id.
    Вход: source (str). Выход: str | None.
    Generator source string → arXiv URL; None for placeholders / unrecognized ids.
    In: source (str). Out: str | None."""
    if not source or not isinstance(source, str):
        return None
    s = source.strip()
    if s.lower().startswith("todo"):
        return None
    m = ARXIV_ID_RE.search(s)
    if not m:
        return None
    return f"https://arxiv.org/abs/{m.group(1)}"


def adapt(rich, *, whitelist_sources=None):
    """Rich-пул → v1-конфиг (категории→axes, home первым, источники→evidence-URL).
    Вход: rich (dict), whitelist_sources. Выход: dict v1-конфига.
    Rich pool → v1 config (categories→axes, home first, sources→evidence URLs).
    In: rich (dict), whitelist_sources. Out: v1 config dict."""
    categories = rich.get("categories") or []
    cat_tag_by_id = {c["id"]: c.get("tag") or c["id"] for c in categories if c.get("id")}

    seen_tags = set()
    axes = []
    for c in categories:
        tag = c.get("tag") or c.get("id")
        if not tag or tag in seen_tags:
            continue
        seen_tags.add(tag)
        ax = {"name": tag}
        if c.get("rationale"):
            ax["description_en"] = c["rationale"]
        axes.append(ax)

    terms_out = []
    for t in rich.get("terms") or []:
        name = t.get("term") or t.get("canonical_id")
        if not name:
            continue

        cand_ids = list(t.get("candidate_categories") or [])
        if not cand_ids and t.get("home_category"):
            cand_ids = [t["home_category"]]

        # cat_id -> tag, drop unknown / unmapped axes
        tags = []
        for cid in cand_ids:
            tag = cat_tag_by_id.get(cid)
            if tag and tag in seen_tags and tag not in tags:
                tags.append(tag)
        if not tags:
            continue

        # Put home_category first so puzzle generators that respect order
        # see the primary axis up front.
        home = cat_tag_by_id.get(t.get("home_category"))
        if home and home in tags:
            tags = [home] + [a for a in tags if a != home]

        term = {"name": str(name), "axes": tags}
        if t.get("description"):
            term["description_en"] = t["description"]

        urls = [u for u in (_arxiv_url(s) for s in (t.get("sources") or [])) if u]
        # Dedup by URL while preserving order.
        seen = set()
        unique_urls = []
        for u in urls:
            if u not in seen:
                seen.add(u); unique_urls.append(u)
        if unique_urls:
            term["evidence"] = [{"url": u} for u in unique_urls]

        terms_out.append(term)

    domain = rich.get("domain") or "unknown"
    subdomain = (rich.get("meta") or {}).get("subdomain") or domain
    area = (rich.get("meta") or {}).get("subdomain") or rich.get("config_id") or domain

    config_id = rich.get("config_id") or "from-generator"
    title = config_id.replace("_", " ").replace("-", " ").strip().title() or "Generated config"

    return {
        "config_id": config_id,
        "title": title,
        "specialty": {"field": domain, "subfield": subdomain, "area": area},
        "whitelist_sources": list(whitelist_sources or ["arxiv.org"]),
        "axes": axes,
        "terms": terms_out,
    }


def adapt_file(rich_path, dst_path=None, **kwargs):
    """Читает rich-JSON, адаптирует, опционально пишет в dst_path. Вход: пути.
    Выход: dict v1-конфига.
    Loads a rich JSON, adapts it, optionally writes to dst_path. In: paths.
    Out: v1 config dict."""
    rich = json.loads(Path(rich_path).read_text(encoding="utf-8"))
    v1 = adapt(rich, **kwargs)
    if dst_path is not None:
        Path(dst_path).write_text(json.dumps(v1, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return v1


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("src", help="path to rich generator JSON (pool / seed shape)")
    p.add_argument("dst", help="path to write v1 config JSON")
    args = p.parse_args()
    v1 = adapt_file(args.src, args.dst)
    print(f"Wrote {args.dst}: {len(v1['terms'])} terms, {len(v1['axes'])} axes")
