"""from_generator.py — E2E-приёмка выхода генерации конфигов: rich-JSON → rich_to_v1 →
v1-конфиг на диск → acceptance() → вердикт (единично или батчем по папке).

from_generator.py — E2E acceptance of the generator output: rich JSON → rich_to_v1 →
v1 config on disk → acceptance() → verdict (single file or a folder batch).

Takes a rich generator JSON (pool / seed format), converts it to v1 via
rich_to_v1.adapt(), writes the v1 config to a known location, and runs the
acceptance pipeline. Returns the verdict.

CLI:
    # Single rich config:
    python validation/from_generator.py path/to/rich.json

    # Batch over a folder (recurses for *.json non-list files):
    python validation/from_generator.py path/to/seeds_dir --batch
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if __package__ in (None, ""):  # script mode: python validation/from_generator.py
    sys.path.insert(0, str(ROOT))
    from validation import acceptance as ac  # noqa: E402
    from validation import rich_to_v1 as r2v  # noqa: E402
else:
    from . import acceptance as ac
    from . import rich_to_v1 as r2v


V1_DIR = ROOT / "configs" / "v1" / "from-generator"


def _looks_like_rich_pool(path):
    """Похож ли файл на rich-пул (dict с categories+terms). Вход: path. Выход: bool.
    Does the file look like a rich pool (a dict with categories+terms). In: path. Out: bool."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and "categories" in data and "terms" in data


def verdict_for_rich(rich_path, *, required_gates=None, n_seeds=100,
                     v1_dir=V1_DIR, make_plot=False):
    """Адаптировать → сохранить → принять. Вход: rich_path + опции. Выход: dict вердикта
    acceptance() + указатель v1_path.
    Adapt → persist → accept. In: rich_path + options. Out: acceptance() verdict dict
    plus the v1_path pointer."""
    v1 = r2v.adapt_file(rich_path)
    v1_dir = Path(v1_dir)
    v1_dir.mkdir(parents=True, exist_ok=True)
    v1_path = v1_dir / f"{v1['config_id']}.v1.json"
    v1_path.write_text(json.dumps(v1, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    verdict = ac.acceptance(
        v1_path,
        required_gates=required_gates,
        n_seeds_for_variety=n_seeds,
        out_dir=v1_dir / "reports" / v1["config_id"],
        make_plot=make_plot,
    )
    verdict["rich_path"] = str(rich_path)
    verdict["v1_path"] = str(v1_path)
    return verdict


def batch(folder, *, required_gates=None, n_seeds=100, v1_dir=V1_DIR,
          make_plot=False, skip_pattern=None):
    """Приёмка всех rich-пулов папки (без рекурсии); не-пулы пропускаются молча.
    Вход: folder + опции. Выход: list[dict] вердиктов (с error-записями при сбоях).
    Acceptance over every rich pool in a folder (non-recursive); non-pools are skipped
    silently. In: folder + options. Out: list[dict] of verdicts (error entries on failures)."""
    folder = Path(folder)
    out = []
    for p in sorted(folder.glob("*.json")):
        if skip_pattern and skip_pattern in p.name:
            continue
        if not _looks_like_rich_pool(p):
            continue
        try:
            out.append(verdict_for_rich(
                p,
                required_gates=required_gates,
                n_seeds=n_seeds,
                v1_dir=v1_dir,
                make_plot=make_plot,
            ))
        except Exception as e:
            out.append({"rich_path": str(p), "error": str(e), "accepted": False})
    return out


def main():
    """CLI: вердикт по rich-файлу или батч по папке. Вход: argv. Выход: exit-код.
    CLI: verdict for one rich file or a folder batch. In: argv. Out: exit code."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", help="rich JSON path OR folder (with --batch)")
    p.add_argument("--batch", action="store_true",
                   help="treat `path` as folder and process every *.json")
    p.add_argument("--n-seeds", type=int, default=100,
                   help="seeds for variety stats (assembly)")
    p.add_argument("--require", nargs="*", default=None,
                   help=f"gates to require (default: all). "
                        f"Choices: {list(ac.METRIC_GATES) + [f'assembly_{m}' for m in ac.ASSEMBLY_GATES]}")
    p.add_argument("--v1-dir", default=str(V1_DIR),
                   help=f"where to write the adapted v1 configs (default: {V1_DIR})")
    args = p.parse_args()

    if args.batch:
        results = batch(args.path, required_gates=args.require,
                        n_seeds=args.n_seeds, v1_dir=args.v1_dir)
        for v in results:
            if "error" in v:
                print(f"{Path(v['rich_path']).name:<40}  ERROR: {v['error']}")
                continue
            print(f"{v['config_id']:<40}  accepted={v['accepted']!s:<5}  "
                  f"blocked={v['blocked_gates']}")
        return 0 if all(v.get("accepted") for v in results) else 1

    v = verdict_for_rich(args.path, required_gates=args.require,
                         n_seeds=args.n_seeds, v1_dir=args.v1_dir)
    print(ac.format_verdict(v))
    print(f"  v1 config written to {v['v1_path']}")
    return 0 if v["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
