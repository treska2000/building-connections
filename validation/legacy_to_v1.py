"""legacy_to_v1.py — конвертер между legacy-мультиязычным конфигом игры и схемой v1
(в обе стороны: adapt и v1_to_legacy для JS-генератора).

legacy_to_v1.py — converter between the game's legacy multilang config and the v1
schema (both ways: adapt, and v1_to_legacy for the JS generator).

Legacy shape (configs/*.json):
    [{"name": {"en", "ru"},
      "description": {"en", "ru"},
      "tags": {"en": [...], "ru": [...]}}, ...]

v1 shape (configs/v1/schema.json):
    {"config_id", "title", "specialty", "whitelist_sources", "axes", "terms"}

Legacy configs have no evidence and no decoys, so the corresponding v1 fields
stay empty after conversion. That's fine, the metrics tool will then report
R3 and mode-3 as failing, which is the truth: those gates aren't met yet.

CLI:
    python validation/legacy_to_v1.py SRC.json DST.json \\
        --config-id ai-safety-v1 --title "AI Safety" \\
        --field "computer science" --subfield ai --area "ai safety"
"""
import argparse
import json
import sys
from pathlib import Path


DEFAULT_WHITELIST = [
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


def _pick(value, lang):
    """Мультиязычное поле → строка нужного языка (с фолбэком). Вход: value, lang. Выход: str.
    Multilang field → string for the requested language (with fallback). In: value, lang. Out: str."""
    if isinstance(value, dict):
        return value.get(lang) or next(iter(value.values()), "")
    return value or ""


def adapt(legacy, *, config_id, title, field, subfield, area,
          whitelist_sources=None, lang="en"):
    """Legacy-список терминов → v1-конфиг; tags выбранного языка = имена осей.
    Вход: legacy (list) + именованные метаданные. Выход: dict v1-конфига.
    Legacy term list → v1 config; the chosen language's tags become axis names.
    In: legacy (list) + keyword metadata. Out: v1 config dict."""
    other = "ru" if lang == "en" else "en"

    axes_seen = {}
    terms_out = []

    for item in legacy:
        name = str(_pick(item.get("name"), lang)).strip()
        if not name:
            continue

        raw_tags = item.get("tags") or []
        tags = raw_tags.get(lang) if isinstance(raw_tags, dict) else raw_tags
        tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
        if not tags:
            continue

        for tag in tags:
            axes_seen.setdefault(tag, {"name": tag})

        term = {"name": name, "axes": tags}

        primary = str(_pick(item.get("description"), lang)).strip()
        secondary = str(_pick(item.get("description"), other)).strip()
        if primary:
            term[f"description_{lang}"] = primary
        if secondary and secondary != primary:
            term[f"description_{other}"] = secondary

        terms_out.append(term)

    return {
        "config_id": config_id,
        "title": title,
        "specialty": {"field": field, "subfield": subfield, "area": area},
        "whitelist_sources": whitelist_sources or DEFAULT_WHITELIST,
        "axes": list(axes_seen.values()),
        "terms": terms_out,
    }


def v1_to_legacy(v1, lang="en"):
    """v1 → плоский список для JS-генератора: [{name, description, tags}].
    Используется puzzle_assembly.py. Вход: v1 (dict), lang. Выход: list[dict].
    v1 → the flat list the JS puzzle generator expects: [{name, description, tags}].
    Used by puzzle_assembly.py. In: v1 (dict), lang. Out: list[dict]."""
    out = []
    for t in v1.get("terms", []):
        name = t.get("name")
        if not name:
            continue
        desc = (t.get(f"description_{lang}")
                or t.get("description_en")
                or t.get("description_ru")
                or "")
        tags = [str(a) for a in t.get("axes") or []]
        out.append({"name": str(name), "description": str(desc), "tags": tags})
    return out


def main():
    """CLI: legacy-JSON → v1-JSON. Вход: argv. Выход: exit-код.
    CLI: legacy JSON → v1 JSON. In: argv. Out: exit code."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("src", help="path to legacy JSON")
    p.add_argument("dst", help="path to write v1 JSON")
    p.add_argument("--config-id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--subfield", required=True)
    p.add_argument("--area", required=True)
    p.add_argument("--lang", default="en", choices=["en", "ru"])
    args = p.parse_args()

    legacy = json.loads(Path(args.src).read_text(encoding="utf-8"))
    if not isinstance(legacy, list):
        print("legacy config must be a JSON array of term objects", file=sys.stderr)
        return 1

    v1 = adapt(legacy,
               config_id=args.config_id, title=args.title,
               field=args.field, subfield=args.subfield, area=args.area,
               lang=args.lang)
    Path(args.dst).write_text(json.dumps(v1, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"Wrote {args.dst}: {len(v1['terms'])} terms, {len(v1['axes'])} axes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
