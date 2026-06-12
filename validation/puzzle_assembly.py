"""puzzle_assembly.py — поведенческая проверка: гоняет НАСТОЯЩИЙ JS-генератор пазлов
(js/puzzle-generator.js) на v1-конфиге через Node-обвязки repro_check.mjs / enumerate.mjs.

puzzle_assembly.py — behavioral check: drives the REAL JS puzzle generator
(js/puzzle-generator.js) on a v1 config via the repro_check.mjs / enumerate.mjs harnesses.

The generator (js/puzzle-generator.js) is driven by Node harnesses in this
folder: repro_check.mjs does one assembly + reproducibility check, enumerate.mjs
runs N seeds and counts distinct puzzles. Both read the LEGACY config shape
({name, description, tags:[]}). To use a v1 config we flatten it via the
v1->legacy projection below, write to a temp file, then shell out.

Modes:
    normal   - 4 categories × 4 terms (16-tile board)
    advanced - 3 categories × 4 terms + 4 decoys

Requires `node` on PATH (v18+).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if __package__ in (None, ""):  # script mode: python validation/puzzle_assembly.py
    sys.path.insert(0, str(ROOT))
    from validation.legacy_to_v1 import v1_to_legacy as _v1_to_legacy  # noqa: E402
else:
    from .legacy_to_v1 import v1_to_legacy as _v1_to_legacy


def _legacy_tempfile(v1, lang="en"):
    """Временный файл legacy-формата для Node-обвязки. Вход: v1, lang. Выход: Path.
    Temp file in the legacy shape for the Node harness. In: v1, lang. Out: Path."""
    legacy = _v1_to_legacy(v1, lang=lang)
    f = tempfile.NamedTemporaryFile(prefix="v1_as_legacy_", suffix=".json",
                                    delete=False, mode="w", encoding="utf-8")
    json.dump(legacy, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


def _run_node(script, args):
    """Запускает Node-скрипт и парсит его JSON-вывод. Вход: script, args. Выход: dict.
    Runs a Node script and parses its JSON output. In: script, args. Out: dict."""
    res = subprocess.run(
        ["node", str(HERE / script), *args],
        capture_output=True, text=True, cwd=ROOT,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(
            f"node {script} failed: {res.stderr.strip() or res.stdout.strip()}"
        )
    return json.loads(res.stdout)


def assemble(v1_path, mode="normal", seed="golden-seed-1", lang="en"):
    """Одна сборка через repro_check.mjs. Вход: v1_path, mode, seed, lang.
    Выход: dict (ключевые поля: assembles, reproducible, puzzle).
    One assembly run via repro_check.mjs. In: v1_path, mode, seed, lang.
    Out: dict (key fields: assembles, reproducible, puzzle)."""
    cfg = json.loads(Path(v1_path).read_text(encoding="utf-8"))
    tmp = _legacy_tempfile(cfg, lang=lang)
    try:
        return _run_node("repro_check.mjs", [str(tmp), mode, seed])
    finally:
        tmp.unlink(missing_ok=True)


def enumerate_puzzles(v1_path, mode="normal", n_seeds=200, lang="en"):
    """Прогон N сидов через enumerate.mjs (разнообразие пазлов). Вход: v1_path, mode,
    n_seeds, lang. Выход: dict (distinct-счётчики, использованные категории).
    N-seed sweep via enumerate.mjs (puzzle variety). In: v1_path, mode, n_seeds, lang.
    Out: dict (distinct counts, categories ever used)."""
    cfg = json.loads(Path(v1_path).read_text(encoding="utf-8"))
    tmp = _legacy_tempfile(cfg, lang=lang)
    try:
        return _run_node("enumerate.mjs", [str(tmp), mode, str(n_seeds)])
    finally:
        tmp.unlink(missing_ok=True)


def assembly_summary(v1_path, *, n_seeds_for_variety=200, lang="en"):
    """Сводка для приёмки: собрался ли пазл в каждой моде и какое разнообразие на N сидах.
    Вход: v1_path, n_seeds_for_variety, lang. Выход: dict (форма ниже).
    Acceptance-style summary: did the puzzle assemble in each mode and how much variety
    across N seeds. In: v1_path, n_seeds_for_variety, lang. Out: dict shaped like:

        {"config": str,
         "modes": {
             "normal":   {assembles, reproducible, boardComplete,
                          oneConceptOneCategory, numCategories,
                          golden_hash, puzzle, variety},
             "advanced": {...},
         }}
    """
    summary = {"config": str(v1_path), "modes": {}}

    for mode in ("normal", "advanced"):
        try:
            one = assemble(v1_path, mode=mode, seed="golden-seed-1", lang=lang)
        except Exception as e:
            summary["modes"][mode] = {"assembles": False, "harnessError": str(e)}
            continue

        block = {
            "assembles": bool(one.get("assembles")),
            "assembleError": one.get("assembleError"),
            "reproducible": one.get("reproducible"),
            "boardComplete": one.get("boardComplete"),
            "numCategories": one.get("numCategories"),
            "oneConceptOneCategory": one.get("oneConceptOneCategory"),
            "golden_hash": one.get("golden_hash"),
            "puzzle": one.get("puzzle"),
        }

        if one.get("assembles"):
            try:
                v = enumerate_puzzles(v1_path, mode=mode,
                                      n_seeds=n_seeds_for_variety, lang=lang)
                block["variety"] = {
                    k: v[k] for k in (
                        "distinct_content", "distinct_labeled", "distinct_exact",
                        "categories_ever_used", "last_new_content_at_seed",
                        "last_new_labeled_at_seed",
                    ) if k in v
                }
            except Exception as e:
                block["variety_error"] = str(e)

        summary["modes"][mode] = block

    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("v1_config")
    p.add_argument("--mode", choices=["normal", "advanced", "both"], default="both")
    p.add_argument("--seed", default="golden-seed-1")
    p.add_argument("--n-seeds", type=int, default=200,
                   help="seeds to enumerate for variety stats")
    args = p.parse_args()

    if args.mode == "both":
        out = assembly_summary(args.v1_config, n_seeds_for_variety=args.n_seeds)
    else:
        out = {"single": assemble(args.v1_config, mode=args.mode, seed=args.seed)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
