"""Validation pipeline orchestrator and CLI. Active criteria (source of truth: registry.gates()):
R1 Volume · R3 Sources · R5 Specialization. Accepts a v1 config (tags/axes) and the native
v2 pool. Exit codes: 0 accept · 1 reject · 2 pending.

Usage:
    python validation/run.py <config.json|pool.json> [--acceptance thresholds.yaml]
                             [--out reports/] [--enrich off|live|cache] [--only R2,R4]
                             [--repro]

Оркестратор пайплайна валидации и CLI. Активные критерии (источник истины: registry.gates()):
R1 Объём · R3 Источники · R5 Специализация. Принимает v1-конфиг (tags/axes) и нативный v2-пул.
Коды выхода: 0 accept · 1 reject · 2 pending.
"""
from __future__ import annotations
import argparse
import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__ in (None, ""):  # script mode: python validation/run.py
    sys.path.insert(0, str(HERE.parent))
    from validation.core import loader  # type: ignore
    from validation import enrich, reproducibility, registry  # type: ignore
    from validation import report as rep  # type: ignore
else:  # package mode: from validation import validate
    from .core import loader
    from . import enrich, reproducibility, registry
    from . import report as rep
# Requirement modules are imported dynamically per registry.pipeline() (see _run_requirement).
# Модули требований импортируются динамически по registry.pipeline().

DEFAULT_ACCEPTANCE = {
    "version": "2.3.0", "seed": 42,
    "embedder": {"model": "gemini-embedding-2", "dim": 768},
    "openalex_snapshot": None,
    # Спек-нумерация (registry.py): R1 Объём · R3 Источники · R5 Специализация — активны.
    # R4 Решаемость прикопан (2026-06-26); R2 Неочевидность(morph)/R6 выведены (2026-06-12);
    # R6 Когерентность — pending до эмбеддера; R7 — паспорт, не гейт.
    # required_gates — единый источник истины: registry.gates().
    "required_gates": list(registry.gates()),
    "thresholds": {
        "R1": {"min_terms": 16, "min_base_tags": 4, "min_tag_size": 4,
               "min_share_3sources": 0.90},
        # R3 Источники (бывш. пакетный R4): качество источников через обогащение
        "R3": {"min_sane_sources_per_term": 1,
               "min_share_sane": 0.90, "min_share_attested": 0.90,
               "min_independent_groups": 3, "citations_hi": 100},
        "R5": {"area_consistency_tau": 0.70},
        # R4 Решаемость прикопан: пороги — референс, в пайплайне не используются
        "R4": {"min_vpy": 0.70, "min_vpy_mode2": None, "min_vpy_mode3": None,
               "n_samples": 400, "seed": 42},
    },
}


def load_acceptance(path: str | None) -> dict:
    """Load thresholds and required_gates: defaults plus YAML overrides when a path is given.

    In: path (yaml | None). Out: acceptance-criteria dict.

    Загружает пороги и required_gates: дефолты плюс оверрайды из YAML, если путь задан.
    In: path (yaml | None). Out: dict acceptance-критериев."""
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
    """Return one requirement's thresholds merged with the inherited seed. In: acc, rid. Out: dict.

    Пороги одного требования плюс унаследованный seed. In: acc, rid. Out: dict."""
    t = dict(acc["thresholds"].get(rid, {}))
    t.setdefault("seed", acc.get("seed", 42))
    return t


def _has_arxiv_sources(cfg) -> bool:
    """Return True if any term cites an arXiv source (handles sources/evidence/top_sources). In: cfg. Out: bool.

    True, если хоть у одного термина источник — arXiv (учитывает sources/evidence/top_sources).
    In: cfg. Out: bool."""
    for t in cfg.get("terms", []):
        for s in loader.get_sources(t):
            if "arxiv.org" in str(s.get("url", "")).lower():
                return True
    return False


def _run_requirement(req, cfg, members, term_tags, acceptance, en, embedder):
    """Call a requirement module with the arguments prescribed by its registry passport (needs field).

    In: req (registry entry), cfg, members, term_tags, acceptance, en, embedder. Out: requirement result dict.

    Вызывает модуль требования с нужными аргументами по паспорту реестра (поле needs).
    In: req, cfg, members, term_tags, acceptance, en, embedder. Out: dict результата требования."""
    mod = importlib.import_module(f"{__package__ or 'validation'}.requirements.{req.module}")
    thr = _thr(acceptance, req.code)
    if req.needs == "enrich":
        return mod.run(cfg, members, term_tags, thr, en)
    if req.needs == "embedder":
        return mod.run(cfg, members, term_tags, thr, embedder)
    return mod.run(cfg, members, term_tags, thr)


def validate(config_path: str, acceptance: dict, en=enrich.DISABLED, repro=None,
             only=None, embedder=None) -> dict:
    """Run a single validation pass over the config. The requirement set comes from
    registry.pipeline() (run=True), not a hardcode — enable a requirement by flipping
    run/gate in registry.py. only is a debug filter that restricts execution to the
    listed requirement codes.

    In: config_path, acceptance (thresholds), en (Enrichment), repro, only (set|None),
    embedder (for needs='embedder', e.g. R6). Out: report dict.

    Один прогон по конфигу. Состав требований берётся из registry.pipeline() (run=True) —
    не хардкод; подключить требование = выставить run/gate в registry.py. only — фильтр
    отладки по кодам требований.
    In: config_path, acceptance (пороги), en (Enrichment), repro, only (set|None),
    embedder (для needs='embedder', напр. R6). Out: dict отчёта."""
    cfg = loader.load_config(config_path)
    members, term_tags = loader.build_membership(cfg)

    # Source-grounded gates (needs="enrich": R3 Sources, R5 Specialization) rely on
    # arXiv/OpenAlex; they apply only when the config cites arXiv papers. For non-arXiv
    # sources they are optional — skipped and dropped from the required set.
    # Источник-зависимые гейты (R3/R5) применяются только к arXiv-источникам; иначе
    # опциональны — пропускаются и не блокируют вердикт.
    require_arxiv = acceptance.get("source_gates_require_arxiv", True)
    arxiv = _has_arxiv_sources(cfg)
    effective_required = [g for g in acceptance.get("required_gates", registry.gates())]

    pipe = registry.pipeline()
    # Lazily build the default embedder (Qwen3) if any active requirement needs it (R6).
    # Ленивая сборка дефолтного эмбеддера, если он нужен активному требованию (R6).
    if embedder is None and any(r.needs == "embedder" for r in pipe):
        try:
            from .core import embedding as _emb
        except ImportError:  # script mode (python validation/run.py): no parent package
            from validation.core import embedding as _emb
        embedder = _emb.get_default(acceptance.get("embedder"))

    selected = set(only) if only else None
    requirements = {}
    for req in pipe:
        if selected is not None and req.code not in selected:
            continue
        if req.needs == "enrich" and require_arxiv and not arxiv:
            if req.code in effective_required:
                effective_required.remove(req.code)
            continue
        requirements[req.code] = _run_requirement(
            req, cfg, members, term_tags, acceptance, en, embedder)
    prov = reproducibility.provenance(acceptance, cfg)
    acc = dict(acceptance, required_gates=effective_required)
    return rep.build_report(cfg, requirements, acc, prov, repro=repro)


def validate_full(config_path: str, acceptance: dict, en=enrich.DISABLED,
                  only=None, embedder=None) -> dict:
    """Run validate() plus the reproducibility self-test (two inner runs). Not the default
    path: invoked by the test contract and the --repro CLI flag; regular config validation
    uses validate().

    In: config_path, acceptance, en, only (debug mode). Out: report dict with the repro block.

    Прогон validate() плюс self-test воспроизводимости (два внутренних прогона). Не дефолт:
    вызывается тестовым контрактом и флагом --repro; обычная валидация — validate().
    In: config_path, acceptance, en, only (режим отладки). Out: dict отчёта с repro-блоком."""
    rc = reproducibility.reproducibility_check(
        lambda p, a: validate(p, a, en, only=only, embedder=embedder), config_path, acceptance)
    return validate(config_path, acceptance, en, repro=rc, only=only, embedder=embedder)


def main() -> int:
    """Run the CLI: print verdict and markdown report; write artifacts when --out is given.

    In: argv (via argparse). Out: exit code (int).

    CLI: вердикт и markdown-отчёт; при --out записывает артефакты на диск.
    In: argv. Out: код выхода (int)."""
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
