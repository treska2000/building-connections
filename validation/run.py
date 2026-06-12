"""run.py — оркестратор пайплайна валидации (R1–R7) + CLI.

Usage:
    python validation/run.py <config.json|pool.json> [--acceptance acceptance.yaml]
                             [--out reports/] [--enrich off|live|cache]
exit: 0 accept · 1 reject · 2 pending
Принимает v1-конфиг (tags/axes) и нативный v2-пул (schema_version=connections_v2_config_pool).
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__ in (None, ""):  # запуск как скрипт: python validation/run.py
    sys.path.insert(0, str(HERE.parent))
    from validation import loader, enrich  # type: ignore
    from validation import report as rep  # type: ignore
    from validation import (r1_volume, r2_solvability, r3_semanticity,  # type: ignore
                            r4_source_quality, r5_specialization,
                            r6_link_strength, r7_reproducibility)
else:  # импорт как пакет: from validation import validate
    from . import loader, enrich
    from . import report as rep
    from . import (r1_volume, r2_solvability, r3_semanticity,
                   r4_source_quality, r5_specialization,
                   r6_link_strength, r7_reproducibility)

DEFAULT_ACCEPTANCE = {
    "version": "2.1.0", "seed": 42,
    "embedder": {"model": "gemini-embedding-2", "dim": 768},
    "openalex_snapshot": None,
    "required_gates": ["R1", "R2", "R3", "R5", "R7"],
    "thresholds": {
        "R1": {"min_terms": 16, "min_base_tags": 4, "min_tag_size": 4},
        "R2": {"min_vpy": 0.70, "min_vpy_mode2": None, "min_vpy_mode3": None,
               "n_samples": 400, "seed": 42},
        "R3": {"morph_leak_link_tau": 0.74, "max_morph_leak": 0.05, "token_min_len": 3},
        "R4": {"min_share_3sources": 0.90, "min_share_sane": 0.90,
               "min_share_attested": 0.90, "min_independent_groups": 3,
               "citations_hi": 100},
        "R5": {"area_consistency_tau": 0.70},
        "R6": {"margin_tau": 0.10},
    },
}


def load_acceptance(path: str | None) -> dict:
    acc = json.loads(json.dumps(DEFAULT_ACCEPTANCE))
    if path:
        try:
            import yaml  # type: ignore
            with open(path) as f:
                user = yaml.safe_load(f)
            if user:
                acc.update(user)
        except Exception as e:  # pragma: no cover
            print(f"[acceptance] не загрузил {path} ({e}) — дефолты", file=sys.stderr)
    return acc


def _thr(acc, rid):
    t = dict(acc["thresholds"].get(rid, {}))
    t.setdefault("seed", acc.get("seed", 42))
    return t


def validate(config_path: str, acceptance: dict, en=enrich.DISABLED, repro=None) -> dict:
    cfg = loader.load_config(config_path)
    members, term_tags = loader.build_membership(cfg)

    requirements = {
        "R1": r1_volume.run(cfg, members, term_tags, _thr(acceptance, "R1")),
        "R2": r2_solvability.run(cfg, members, term_tags, _thr(acceptance, "R2")),
        "R3": r3_semanticity.run(cfg, members, term_tags, _thr(acceptance, "R3")),
        "R4": r4_source_quality.run(cfg, members, term_tags, _thr(acceptance, "R4"), en),
        "R5": r5_specialization.run(cfg, members, term_tags, _thr(acceptance, "R5"), en),
        "R6": r6_link_strength.run(cfg, members, term_tags, _thr(acceptance, "R6"), en),
    }
    prov = r7_reproducibility.provenance(acceptance, cfg)
    det = r7_reproducibility.determinism_flags(requirements)
    return rep.build_report(cfg, requirements, acceptance, prov, det, repro=repro)


def validate_full(config_path: str, acceptance: dict, en=enrich.DISABLED) -> dict:
    """Полный прогон + R7 reproducibility_check (два внутренних прогона)."""
    rc = r7_reproducibility.reproducibility_check(
        lambda p, a: validate(p, a, en), config_path, acceptance)
    return validate(config_path, acceptance, en, repro=rc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", type=Path)
    ap.add_argument("--acceptance", type=Path, default=HERE / "acceptance.yaml")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--enrich", choices=["off", "live", "cache"], default="off",
                    help="off=детерм. ядро · live=arXiv+OpenAlex (кэш пишется) · cache=только кэш (офлайн)")
    args = ap.parse_args()
    acc = load_acceptance(str(args.acceptance) if args.acceptance and args.acceptance.exists() else None)
    en = enrich.DISABLED
    if args.enrich in ("live", "cache"):
        en = enrich.Enrichment.live(cache_dir=str(HERE / "enrich_cache"),
                                    offline=(args.enrich == "cache"))
    report = validate_full(str(args.config), acc, en=en)
    print(rep.to_markdown(report))
    if args.out:
        d = args.out / (report.get("config_id") or args.config.stem)
        d.mkdir(parents=True, exist_ok=True)
        (d / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "report.md").write_text(rep.to_markdown(report), encoding="utf-8")
        print(f"\n→ {d}/metrics.json")
    return {"accept": 0, "reject": 1, "pending": 2}.get(report["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
