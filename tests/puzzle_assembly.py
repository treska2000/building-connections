"""Drive the JS puzzle generator from a v1 config.

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
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _v1_to_legacy(v1, lang="en"):
    """v1 config -> flat term list the JS generator expects:
        [{"name": str, "description": str, "tags": [str, ...]}, ...]
    """
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


def _legacy_tempfile(v1, lang="en"):
    legacy = _v1_to_legacy(v1, lang=lang)
    f = tempfile.NamedTemporaryFile(prefix="v1_as_legacy_", suffix=".json",
                                    delete=False, mode="w", encoding="utf-8")
    json.dump(legacy, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


def _run_node(script, args):
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
    """One assembly run. Returns whatever repro_check.mjs prints, see that
    file for the shape, but `assembles`, `reproducible`, `puzzle` are the
    interesting keys."""
    cfg = json.loads(Path(v1_path).read_text(encoding="utf-8"))
    tmp = _legacy_tempfile(cfg, lang=lang)
    try:
        return _run_node("repro_check.mjs", [str(tmp), mode, seed])
    finally:
        tmp.unlink(missing_ok=True)


def enumerate_puzzles(v1_path, mode="normal", n_seeds=200, lang="en"):
    """N-seed sweep. Returns enumerate.mjs output (distinct content/labeled/exact
    counts, categories ever used, last-new-seed indices)."""
    cfg = json.loads(Path(v1_path).read_text(encoding="utf-8"))
    tmp = _legacy_tempfile(cfg, lang=lang)
    try:
        return _run_node("enumerate.mjs", [str(tmp), mode, str(n_seeds)])
    finally:
        tmp.unlink(missing_ok=True)


def assembly_summary(v1_path, *, n_seeds_for_variety=200, lang="en"):
    """Acceptance-style block: did the puzzle assemble in each mode and how
    much variety we get across N seeds. Shape:

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
