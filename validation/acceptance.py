"""acceptance.py — единая точка вердикта по конфигу: metric gates (R1–R7 из движка
пакета) + assembly gates (реальная JS-сборка через Node). Конфиг принят, когда все
required-гейты прошли; набор требуемых гейтов задаёт вызывающий. История: предок —
config_metrics.py (2026-06-03); маппинг старых имён гейтов: R2_lexical_leak→R3_morph_leak,
R3_evidence→R4_sources, R4_modeN→R2_modeN.

acceptance.py — the single config-verdict entry point: metric gates (R1–R7 from the
package engine) + assembly gates (the real JS assembly via Node). A config is accepted
when every required gate passes; the caller chooses the required set. Lineage: the
predecessor is config_metrics.py (2026-06-03); old gate-name mapping:
R2_lexical_leak→R3_morph_leak, R3_evidence→R4_sources, R4_modeN→R2_modeN.

CLI (the single validation entry point, see README):
    python validation/acceptance.py configs/v1/your-config.json --enrich live
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__ in (None, ""):  # script mode: python validation/acceptance.py
    sys.path.insert(0, str(HERE.parent))
    from validation.run import validate as v_validate, load_acceptance  # noqa: E402
    from validation import loader as v_loader  # noqa: E402
    from validation.schema_check import validate_schema  # noqa: E402
    from validation import puzzle_assembly as pa  # noqa: E402
else:
    from .run import validate as v_validate, load_acceptance
    from . import loader as v_loader
    from .schema_check import validate_schema
    from . import puzzle_assembly as pa


METRIC_GATES = (
    "R1_volume",
    "R2_mode1",
    "R2_mode2",
    "R2_mode3",
    "R4_sources",
    "R5_specialty",
)
ASSEMBLY_GATES = ("normal", "advanced")
# R4_sources is blocking (2026-06-12 decision): without enrichment it is None ->
# the config is not accepted; run the single entry point with --enrich live.
# R2_mode2/3 remain deepenings (thresholds uncalibrated).
# R3_morph_leak and R6 are out of the pipeline (2026-06-12) pending owner decisions.
DEFAULT_REQUIRED = ("R1_volume", "R2_mode1", "R4_sources",
                    "R5_specialty") + tuple(f"assembly_{m}" for m in ASSEMBLY_GATES)


def _b(x):
    """None (pending/нет данных) остаётся None; иначе bool. Вход: любое. Выход: bool | None.
    None (pending/no data) stays None; otherwise bool. In: anything. Out: bool | None."""
    return None if x is None else bool(x)


def _metric_gates_from_report(rep):
    """Плоские гейты из отчёта validation (R-нумерация спеки). Вход: rep (отчёт validate()).
    Выход: dict имя_гейта → bool | None.
    Flat gates from the validation report (spec R-numbering). In: rep (validate() report).
    Out: dict gate_name → bool | None."""
    m = {rid: r["metrics"] for rid, r in rep["requirements"].items()}

    def mode_gate(n):
        """Гейт моды n: предусловие ∧ exists; None при недостатке данных.
        Mode-n gate: precondition ∧ exists; None when data is missing."""
        pre = m["R2"].get(f"is_eligible_mode{n}", {}).get("pass")
        sol = m["R2"].get(f"exists_valid_puzzle_mode{n}", {}).get("pass")
        if pre is None or sol is None:
            return None
        return bool(pre and sol)

    return {
        "R1_volume": _b(rep["summary"].get("R1")),
        "R2_mode1": mode_gate(1),
        "R2_mode2": mode_gate(2),
        "R2_mode3": mode_gate(3),
        "R4_sources": _b(rep["summary"].get("R4")),
        "R5_specialty": _b(rep["summary"].get("R5")),
    }


def _assembly_gate(block):
    """Мода проходит, когда сборка дала воспроизводимую полную доску без пересечений.
    Вход: block (узел assembly_summary). Выход: bool.
    A mode passes when assembly yields a reproducible, complete, non-overlapping board.
    In: block (assembly_summary node). Out: bool."""
    return bool(
        block.get("assembles")
        and block.get("reproducible")
        and block.get("boardComplete")
        and block.get("oneConceptOneCategory")
    )


def acceptance(config_path, *, n_seeds_for_variety=200, required_gates=None,
               out_dir=None, make_plot=False, seed=42, n_samples=400, enrich=None):
    """Полный приёмочный прогон одного конфига: схема → метрики R1–R7 → сборка → вердикт.
    Вход: config_path + именованные параметры (required_gates, n_samples, enrich, …).
    Выход: dict вердикта (см. форму ниже).
    Runs the full acceptance pipeline on a single config: schema → R1–R7 metrics →
    assembly → verdict. In: config_path + keyword options (required_gates, n_samples,
    enrich, …). Out: verdict dict shaped like:
        {
          "config_id":     str,
          "config_path":   str,
          "schema_valid":  bool,
          "schema_errors": [str, ...],
          "metric_gates":  {gate_name -> bool|None},
          "assembly_gates": {mode -> bool},
          "required_gates": (str, ...),
          "accepted":      bool,
          "blocked_gates": [str, ...],  # required gates that failed
          "metrics":       <полный отчёт validation (R1–R7, provenance)>,
          "assembly":      <full puzzle_assembly output>,
        }
    `make_plot` оставлен в сигнатуре для совместимости (плоты строит ноутбук).
    """
    required_gates = tuple(required_gates) if required_gates else DEFAULT_REQUIRED

    cfg = v_loader.load_config(str(config_path))
    schema_errors = validate_schema(cfg)

    acc = load_acceptance(None)
    acc["seed"] = seed
    acc["thresholds"]["R2"]["n_samples"] = n_samples

    rep = None
    metric_gates = {g: None for g in METRIC_GATES}
    if not schema_errors:
        rep = v_validate(str(config_path), acc, **({"en": enrich} if enrich else {}))
        metric_gates = _metric_gates_from_report(rep)
        if out_dir:
            d = Path(out_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / "metrics.json").write_text(
                json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    assembly = None
    asm_gates = {m: False for m in ASSEMBLY_GATES}
    if not schema_errors:
        assembly = pa.assembly_summary(
            str(config_path), n_seeds_for_variety=n_seeds_for_variety,
        )
        for mode, block in assembly["modes"].items():
            asm_gates[mode] = _assembly_gate(block)

    all_gates = {**metric_gates,
                 **{f"assembly_{m}": v for m, v in asm_gates.items()}}
    blocked = [g for g in required_gates if not all_gates.get(g)]

    return {
        "config_id": (rep or {}).get("config_id") or cfg.get("config_id"),
        "config_path": str(config_path),
        "schema_valid": not schema_errors,
        "schema_errors": schema_errors,
        "metric_gates": metric_gates,
        "assembly_gates": asm_gates,
        "required_gates": required_gates,
        "accepted": not schema_errors and not blocked,
        "blocked_gates": blocked,
        "metrics": rep,
        "assembly": assembly,
    }


def _badge(v):
    if v is None:
        return "n/a"
    return "PASS" if v else "FAIL"


def format_verdict(verdict):
    """Человекочитаемая сводка вердикта. Вход: verdict (из acceptance()). Выход: str.
    Human-readable verdict summary. In: verdict (from acceptance()). Out: str."""
    lines = [f"# {verdict['config_id']}  ({verdict['config_path']})"]
    if not verdict["schema_valid"]:
        lines.append(f"  SCHEMA INVALID: {verdict['schema_errors']}")
        return "\n".join(lines)
    lines.append("  metric gates:")
    for g, v in verdict["metric_gates"].items():
        lines.append(f"    {g:<18} {_badge(v)}")
    lines.append("  assembly gates:")
    for m, v in verdict["assembly_gates"].items():
        lines.append(f"    {m:<18} {_badge(v)}")
    lines.append(f"  ACCEPTED: {verdict['accepted']}")
    if verdict["blocked_gates"]:
        lines.append(f"  blocked on: {verdict['blocked_gates']}")
    return "\n".join(lines)


def main():
    """CLI: вердикт по конфигу; exit 0 = accepted, 1 = нет. Вход: argv. Выход: int.
    CLI: config verdict; exit 0 = accepted, 1 = not. In: argv. Out: int."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("config", help="path to a config (v1 / input-schema / v2-pool)")
    p.add_argument("--n-seeds", type=int, default=200,
                   help="seeds for variety stats (assembly)")
    p.add_argument("--require", nargs="*", default=None,
                   help=f"gate names to require (default: structural core + assembly). "
                        f"Choices: {list(METRIC_GATES) + [f'assembly_{m}' for m in ASSEMBLY_GATES]}")
    p.add_argument("--enrich", choices=["off", "live", "cache"], default="off",
                   help="off=deterministic core · live=arXiv+OpenAlex (writes cache) · cache=cache only")
    p.add_argument("--json", action="store_true",
                   help="emit the full verdict dict as JSON")
    args = p.parse_args()

    en = None
    if args.enrich in ("live", "cache"):
        if __package__ in (None, ""):
            from validation.enrich import Enrichment
        else:
            from .enrich import Enrichment
        en = Enrichment.live(cache_dir=str(HERE / "enrich_cache"),
                             offline=(args.enrich == "cache"))

    v = acceptance(
        args.config,
        n_seeds_for_variety=args.n_seeds,
        required_gates=args.require,
        enrich=en,
    )
    if args.json:
        print(json.dumps({k: v[k] for k in
                          ("config_id", "config_path", "schema_valid", "schema_errors",
                           "metric_gates", "assembly_gates", "required_gates",
                           "accepted", "blocked_gates")},
                         ensure_ascii=False, indent=2))
    else:
        print(format_verdict(v))
    return 0 if v["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
