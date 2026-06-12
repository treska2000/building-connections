"""run.py — оркестратор пайплайна валидации + CLI. Активные критерии: R1, R2, R4, R5
(R3 и R6 выведены из пайплайна 2026-06-12 до решений владельца — модули сохранены,
но не подключены). Принимает v1-конфиг (tags/axes) и нативный v2-пул.
exit: 0 accept · 1 reject · 2 pending.

run.py — validation pipeline orchestrator + CLI. Active criteria: R1, R2, R4, R5
(R3 and R6 were taken out of the pipeline on 2026-06-12 pending owner decisions —
the modules are kept but not wired in). Accepts a v1 config (tags/axes) and the
native v2 pool. exit: 0 accept · 1 reject · 2 pending.

Usage:
    python validation/run.py <config.json|pool.json> [--acceptance thresholds.yaml]
                             [--out reports/] [--enrich off|live|cache] [--only R2,R4]
                             [--repro]
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__ in (None, ""):  # script mode: python validation/run.py
    sys.path.insert(0, str(HERE.parent))
    from validation import loader, enrich  # type: ignore
    from validation import report as rep  # type: ignore
    from validation import (r1_volume, r2_solvability,  # type: ignore
                            r4_source_quality, r5_specialization,
                            r7_reproducibility)
else:  # package mode: from validation import validate
    from . import loader, enrich
    from . import report as rep
    from . import (r1_volume, r2_solvability,
                   r4_source_quality, r5_specialization,
                   r7_reproducibility)
# r3_semanticity and r6_link_strength are intentionally not imported: both criteria
# were taken out of the pipeline (2026-06-12) pending owner decisions
# (R3 — the goldens fork; R6 — the source-space redesign)

DEFAULT_ACCEPTANCE = {
    "version": "2.3.0", "seed": 42,
    "embedder": {"model": "gemini-embedding-2", "dim": 768},
    "openalex_snapshot": None,
    # R7 is not a criterion anymore (2026-06-12): the validator passport
    # (provenance, repro self-test) lives outside the gates.
    # R3 and R6 are out of the pipeline (2026-06-12) pending owner decisions;
    # their threshold keys return together with the modules.
    "required_gates": ["R1", "R2", "R4", "R5"],
    "thresholds": {
        "R1": {"min_terms": 16, "min_base_tags": 4, "min_tag_size": 4,
               "min_share_3sources": 0.90},
        "R2": {"min_vpy": 0.70, "min_vpy_mode2": None, "min_vpy_mode3": None,
               "n_samples": 400, "seed": 42},
        "R4": {"min_sane_sources_per_term": 1,
               "min_share_sane": 0.90, "min_share_attested": 0.90,
               "min_independent_groups": 3, "citations_hi": 100},
        "R5": {"area_consistency_tau": 0.70},
    },
}


def load_acceptance(path: str | None) -> dict:
    """Загружает пороги/required_gates: дефолты + оверрайды из YAML (если задан).
    Вход: path (yaml | None). Выход: dict acceptance-критериев.
    Loads thresholds/required_gates: defaults + YAML overrides (when given).
    In: path (yaml | None). Out: acceptance-criteria dict."""
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
    """Пороги одного требования + унаследованный seed. Вход: acc, rid. Выход: dict.
    One requirement's thresholds + the inherited seed. In: acc, rid. Out: dict."""
    t = dict(acc["thresholds"].get(rid, {}))
    t.setdefault("seed", acc.get("seed", 42))
    return t


def validate(config_path: str, acceptance: dict, en=enrich.DISABLED, repro=None,
             only=None) -> dict:
    """Один прогон R1–R7 по конфигу. only — режим отладки: запускаются только
    перечисленные требования (например {"R2"}), остальные блоки кода не выполняются,
    вердикт в этом режиме информативен только по выбранным. Вход: config_path,
    acceptance (пороги), en (Enrichment), repro (repro-чек | None), only (set | None).
    Выход: dict отчёта.
    A single R1–R7 run over a config. only is a debugging mode: only the listed
    requirements run (e.g. {"R2"}), the remaining code blocks are not executed, and
    the verdict is meaningful only for the selected ones. In: config_path, acceptance
    (thresholds), en (Enrichment), repro (repro check | None), only (set | None).
    Out: report dict."""
    cfg = loader.load_config(config_path)
    members, term_tags = loader.build_membership(cfg)

    runners = {
        "R1": lambda: r1_volume.run(cfg, members, term_tags, _thr(acceptance, "R1")),
        "R2": lambda: r2_solvability.run(cfg, members, term_tags, _thr(acceptance, "R2")),
        "R4": lambda: r4_source_quality.run(cfg, members, term_tags, _thr(acceptance, "R4"), en),
        "R5": lambda: r5_specialization.run(cfg, members, term_tags, _thr(acceptance, "R5"), en),
        # R3/R6 are parked (2026-06-12): wire them back here once the owner decides
    }
    selected = set(only) if only else set(runners)
    requirements = {rid: fn() for rid, fn in runners.items() if rid in selected}
    prov = r7_reproducibility.provenance(acceptance, cfg)
    return rep.build_report(cfg, requirements, acceptance, prov, repro=repro)


def validate_full(config_path: str, acceptance: dict, en=enrich.DISABLED,
                  only=None) -> dict:
    """Прогон + reproducibility_check — self-test валидатора (два внутренних прогона).
    Не дефолт: вызывается тестовым контрактом и CLI-флагом --repro; обычная валидация
    конфига — validate(). Вход: config_path, acceptance, en, only (режим отладки).
    Выход: dict отчёта с repro-блоком.
    Run + reproducibility_check — the validator's self-test (two inner runs).
    Not the default: invoked by the test contract and the --repro CLI flag; regular
    config validation is validate(). In: config_path, acceptance, en, only (debug mode).
    Out: report dict with the repro block."""
    rc = r7_reproducibility.reproducibility_check(
        lambda p, a: validate(p, a, en, only=only), config_path, acceptance)
    return validate(config_path, acceptance, en, repro=rc, only=only)


def main() -> int:
    """CLI: вердикт + markdown-отчёт (+ артефакты при --out). Вход: argv. Выход: exit-код.
    CLI: verdict + markdown report (+ artifacts with --out). In: argv. Out: exit code."""
    ap = argparse.ArgumentParser()
    ap.add_argument("config", type=Path)
    ap.add_argument("--acceptance", type=Path, default=HERE / "thresholds.yaml")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--enrich", choices=["off", "live", "cache"], default="off",
                    help="off=детерм. ядро · live=arXiv+OpenAlex (кэш пишется) · cache=только кэш (офлайн)")
    ap.add_argument("--only", default=None,
                    help="debug isolation: run only the listed requirements, e.g. --only R2,R4")
    ap.add_argument("--repro", action="store_true",
                    help="validator self-test: two inner runs, diff of deterministic metrics must be 0")
    args = ap.parse_args()
    acc = load_acceptance(str(args.acceptance) if args.acceptance and args.acceptance.exists() else None)
    en = enrich.DISABLED
    if args.enrich in ("live", "cache"):
        en = enrich.Enrichment.live(cache_dir=str(HERE / "enrich_cache"),
                                    offline=(args.enrich == "cache"))
    only = {x.strip().upper() for x in args.only.split(",")} if args.only else None
    # default = a single run; the repro self-test (3x cost) is opt-in via --repro
    run_fn = validate_full if args.repro else validate
    report = run_fn(str(args.config), acc, en=en, only=only)
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
