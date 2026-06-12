"""Config acceptance gate (единая точка вердикта).

Takes ONE config (v1 tags/axes, input-schema или нативный v2-пул) and returns a
structured verdict: which gates passed, which failed, whether the config is
ready to ship as a puzzle pack.

Two layers of gates:

    metric_gates   - R1..R5 из пакета validation/ (спека «Валидация требований
                     конфигов»; движок: solver CP-SAT, morph_leak ресёрч-конфиг,
                     обогащение arXiv/OpenAlex опционально)
    assembly_gates - puzzle actually assembles in each mode (Node, поведенчески)

История: метрики раньше считал config_metrics.py (R-нумерация 2026-06-03);
он заменён пакетом validation/ — это его развитие, не параллельная версия.
Маппинг старых имён гейтов: R2_lexical_leak→R3_morph_leak, R3_evidence→R4_sources,
R4_modeN→R2_modeN.

A config is "accepted" when all required gates pass. Some gates legitimately
fail when prerequisite data is missing (e.g. R4_sources before enrichment);
callers choose which gates to require via `required_gates`.

CLI:
    python validation/acceptance.py configs/v1/your-config.json
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__ in (None, ""):  # запуск как скрипт: python validation/acceptance.py
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
    "R3_morph_leak",
    "R4_sources",
    "R5_specialty",
)
ASSEMBLY_GATES = ("normal", "advanced")
# R4_sources требует обогащения/выгрузки источников, R2_mode2/3 — углубления:
# по умолчанию требуем структурный минимум + сборку (как раньше требовался core).
DEFAULT_REQUIRED = ("R1_volume", "R2_mode1", "R3_morph_leak", "R5_specialty") + tuple(
    f"assembly_{m}" for m in ASSEMBLY_GATES)


def _b(x):
    """None (pending/нет данных) остаётся None; иначе bool."""
    return None if x is None else bool(x)


def _metric_gates_from_report(rep):
    """Плоские гейты из отчёта validation (R-нумерация спеки)."""
    m = {rid: r["metrics"] for rid, r in rep["requirements"].items()}

    def mode_gate(n):
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
        "R3_morph_leak": _b(m["R3"]["morph_leak_rate"]["pass"]),
        "R4_sources": _b(rep["summary"].get("R4")),
        "R5_specialty": _b(rep["summary"].get("R5")),
    }


def _assembly_gate(block):
    """One mode passes when assembly produces a reproducible, complete,
    non-overlapping board."""
    return bool(
        block.get("assembles")
        and block.get("reproducible")
        and block.get("boardComplete")
        and block.get("oneConceptOneCategory")
    )


def acceptance(config_path, *, n_seeds_for_variety=200, required_gates=None,
               out_dir=None, make_plot=False, seed=42, n_samples=400, enrich=None):
    """Run the full acceptance pipeline on a single config.

    Returns a dict shaped like:
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
    """Human-readable one-paragraph summary."""
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
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("config", help="path to a config (v1 / input-schema / v2-pool)")
    p.add_argument("--n-seeds", type=int, default=200,
                   help="seeds for variety stats (assembly)")
    p.add_argument("--require", nargs="*", default=None,
                   help=f"gate names to require (default: structural core + assembly). "
                        f"Choices: {list(METRIC_GATES) + [f'assembly_{m}' for m in ASSEMBLY_GATES]}")
    p.add_argument("--json", action="store_true",
                   help="emit the full verdict dict as JSON")
    args = p.parse_args()

    v = acceptance(
        args.config,
        n_seeds_for_variety=args.n_seeds,
        required_gates=args.require,
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
