"""schema_check.py — structural and referential config validation (categories in `tags`
OR `axes`; specialty as an object OR a string). The native v2 pool never reaches this
check — loader.load_config normalizes it first.

schema_check.py — структурная и ссылочная валидация конфига (категории в `tags`
ИЛИ `axes`; specialty объектом ИЛИ строкой). Нативный v2-пул сюда не попадает —
loader.load_config нормализует его раньше.
"""
from __future__ import annotations


def validate_schema(cfg) -> list[str]:
    """Validate the config's structure and referential integrity.

    In: cfg (dict). Out: list[str] of errors (empty list = valid).

    Проверяет структуру и ссылочную целостность конфига.
    In: cfg (dict). Out: list[str] ошибок (пустой список = валидно)."""
    if not isinstance(cfg, dict):
        return ["config root is not an object"]

    errors = []
    for k in ("config_id", "specialty", "terms"):
        if k not in cfg:
            errors.append(f"missing top-level key: {k}")

    tag_field = "tags" if "tags" in cfg else ("axes" if "axes" in cfg else None)
    if tag_field is None:
        errors.append("missing top-level key: tags (or legacy axes)")

    sp = cfg.get("specialty")
    if isinstance(sp, dict):
        for k in ("field", "subfield", "area"):
            if not sp.get(k):
                errors.append(f"specialty.{k} is empty or missing")
    elif isinstance(sp, str):
        if not sp.strip():
            errors.append("specialty is an empty string")
    elif sp is not None:
        errors.append("specialty is neither object nor string")

    cats = cfg.get(tag_field) or [] if tag_field else []
    if tag_field and (not isinstance(cats, list) or not cats):
        errors.append(f"{tag_field} must be a non-empty array")

    cat_names = set()
    for i, c in enumerate(cats if isinstance(cats, list) else []):
        name = c.get("name") if isinstance(c, dict) else c
        if not name:
            errors.append(f"{tag_field}[{i}].name is empty or missing")
            continue
        if name in cat_names:
            errors.append(f"duplicate category name: {name!r}")
        cat_names.add(name)

    terms = cfg.get("terms") or []
    if not isinstance(terms, list) or not terms:
        errors.append("terms must be a non-empty array")

    seen_terms = set()
    for i, t in enumerate(terms if isinstance(terms, list) else []):
        if not isinstance(t, dict):
            errors.append(f"terms[{i}] is not an object")
            continue
        name = t.get("name")
        if not name:
            errors.append(f"terms[{i}].name is empty or missing")
        elif name in seen_terms:
            errors.append(f"duplicate term name: {name!r}")
        else:
            seen_terms.add(name)
        memb = t.get("tags") or t.get("axes")
        if not memb:
            errors.append(f"terms[{i}].tags is empty or missing")
        for ref in memb or []:
            if cat_names and ref not in cat_names:
                errors.append(f"terms[{i}] references unknown category: {ref!r}")
        for ref in (t.get("decoy_for_tags") or t.get("decoy_for_axes") or []):
            if cat_names and ref not in cat_names:
                errors.append(f"terms[{i}].decoy_for references unknown category: {ref!r}")
        for j, e in enumerate((t.get("sources") or t.get("evidence") or [])):
            if not isinstance(e, dict) or not e.get("url"):
                errors.append(f"terms[{i}].sources[{j}] missing url")

    return errors
