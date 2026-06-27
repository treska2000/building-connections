"""Behavioral check: drives the real JS puzzle generator on a v1 config.

The generator (js/puzzle-generator.js) is driven by Node harnesses: repro_check.mjs performs
one assembly plus a reproducibility check; enumerate.mjs runs N seeds and counts distinct
puzzles. Both expect the LEGACY config shape ({name, description, tags:[]}). To use a v1
config the module projects it to the legacy format, writes a temp file, and shells out to
Node (v18+ required). Two board modes are supported: normal (4 categories x 4 terms, 16-tile
board) and advanced (3 categories x 4 terms + 4 decoys).

Поведенческая проверка: гоняет настоящий JS-генератор пазлов на v1-конфиге.
Генератор (js/puzzle-generator.js) управляется Node-обвязками: repro_check.mjs выполняет
одну сборку с проверкой воспроизводимости; enumerate.mjs прогоняет N сидов и считает
различные пазлы. Обе обвязки ожидают legacy-формат ({name, description, tags:[]}). Для
v1-конфига модуль проецирует его в legacy, пишет во временный файл и вызывает Node (v18+).
Поддерживаются режимы: normal (4 категории x 4 термина, 16-клеточная доска) и advanced
(3 категории x 4 термина + 4 decoy-термина).
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


def _v1_to_legacy(v1, lang="en"):
    """Project a v1 config to the flat list the JS puzzle generator expects.

    Output shape: [{name, description, tags}]. Reads either "axes" or "tags" field.
    In: v1 (config dict), lang. Out: list of dicts.

    Проекция v1-конфига в плоский список для JS-генератора пазлов.
    In: v1 (словарь конфига), lang. Out: список словарей [{name, description, tags}]."""
    out = []
    for t in v1.get("terms", []):
        name = t.get("name")
        if not name:
            continue
        desc = (t.get(f"description_{lang}") or t.get("description_en")
                or t.get("description_ru") or "")
        tags = [str(a) for a in (t.get("axes") or t.get("tags") or [])]
        out.append({"name": str(name), "description": str(desc), "tags": tags})
    return out


def _legacy_tempfile(v1, lang="en"):
    """Write a v1 config as a legacy-format temp file for the Node harness.

    In: v1 (config dict), lang. Out: Path to the temp file.

    Записывает v1-конфиг в временный файл legacy-формата для Node-обвязки.
    In: v1, lang. Out: Path к временному файлу."""
    legacy = _v1_to_legacy(v1, lang=lang)
    f = tempfile.NamedTemporaryFile(prefix="v1_as_legacy_", suffix=".json",
                                    delete=False, mode="w", encoding="utf-8")
    json.dump(legacy, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


def _run_node(script, args):
    """Run a Node script and parse its JSON stdout output.

    In: script (filename relative to HERE), args (list of str). Out: dict.

    Запускает Node-скрипт и парсит его JSON-вывод.
    In: script (имя файла относительно HERE), args (список str). Out: dict."""
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
    """Run one puzzle assembly via repro_check.mjs and return the result.

    In: v1_path, mode ('normal'|'advanced'), seed, lang.
    Out: dict with key fields assembles, reproducible, puzzle.

    Одна сборка пазла через repro_check.mjs.
    In: v1_path, mode, seed, lang.
    Out: dict (ключевые поля: assembles, reproducible, puzzle)."""
    cfg = json.loads(Path(v1_path).read_text(encoding="utf-8"))
    tmp = _legacy_tempfile(cfg, lang=lang)
    try:
        return _run_node("repro_check.mjs", [str(tmp), mode, seed])
    finally:
        tmp.unlink(missing_ok=True)


def enumerate_puzzles(v1_path, mode="normal", n_seeds=200, lang="en"):
    """Run an N-seed sweep via enumerate.mjs to measure puzzle variety.

    In: v1_path, mode, n_seeds, lang.
    Out: dict with distinct counts and categories ever used.

    Прогон N сидов через enumerate.mjs для оценки разнообразия пазлов.
    In: v1_path, mode, n_seeds, lang.
    Out: dict (distinct-счётчики, использованные категории)."""
    cfg = json.loads(Path(v1_path).read_text(encoding="utf-8"))
    tmp = _legacy_tempfile(cfg, lang=lang)
    try:
        return _run_node("enumerate.mjs", [str(tmp), mode, str(n_seeds)])
    finally:
        tmp.unlink(missing_ok=True)


def assembly_summary(v1_path, *, n_seeds_for_variety=200, lang="en"):
    """Return an acceptance-style summary: assembly result per mode and variety over N seeds.

    In: v1_path, n_seeds_for_variety, lang.
    Out: dict shaped like::

        {"config": str,
         "modes": {
             "normal":   {assembles, reproducible, boardComplete,
                          oneConceptOneCategory, numCategories,
                          golden_hash, puzzle, variety},
             "advanced": {...},
         }}

    Сводка для приёмки: собрался ли пазл в каждой моде и разнообразие на N сидах.
    In: v1_path, n_seeds_for_variety, lang. Out: dict указанной выше формы.
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
