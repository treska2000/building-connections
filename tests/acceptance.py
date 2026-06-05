"""Config acceptance gate for the v1 generator.

Takes ONE config (any v1 config, not just the demo ones) and returns a
structured verdict: which gates passed, which failed, whether the config is
ready to ship as a puzzle pack.

Two layers of gates:

    metric_gates  - R1..R6 from config_metrics
    assembly_gates - puzzle actually assembles in each mode

A config is "accepted" when all metric_gates and all assembly_gates pass.
Some gates legitimately fail when prerequisite data is missing (e.g. R3 fails
when evidence URLs haven't been collected yet). Callers can decide which
gates to require via `required_gates`; everything else is reported but not
counted against acceptance.

CLI:
    python tests/acceptance.py configs/v1/your-config.json
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config_metrics as cm  # noqa: E402
import puzzle_assembly as pa  # noqa: E402


METRIC_GATES = (
    "R1_volume",
    "R2_lexical_leak",
    "R3_evidence",
    "R4_mode1",
    "R4_mode2",
    "R4_mode3",
    "R5_specialty",
)
ASSEMBLY_GATES = ("normal", "advanced")
DEFAULT_REQUIRED = METRIC_GATES + tuple(f"assembly_{m}" for m in ASSEMBLY_GATES)


def _metric_gates_from_summary(summary):
    """Flatten config_metrics.summary into the gate names we use here."""
    return {
        "R1_volume": summary.get("pass_r1"),
        "R2_lexical_leak": summary.get("pass_r2_lexical"),
        "R3_evidence": summary.get("pass_r3"),
        "R4_mode1": summary.get("pass_mode1"),
        "R4_mode2": summary.get("pass_mode2"),
        "R4_mode3": summary.get("pass_mode3"),
        "R5_specialty": summary.get("pass_r5"),
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
               out_dir=None, make_plot=False, seed=42):
    """Run the full acceptance pipeline on a single v1 config.

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
          "metrics":       <full config_metrics output>,
          "assembly":      <full puzzle_assembly output>,
        }

    `out_dir` is forwarded to config_metrics.run; pass None to skip writing
    artefacts.
    """
    required_gates = tuple(required_gates) if required_gates else DEFAULT_REQUIRED

    metrics = cm.run(
        str(config_path),
        out_dir or (Path("/tmp/acceptance") / Path(config_path).stem),
        make_plot=make_plot,
        seed=seed,
    )

    assembly = None
    asm_gates = {m: False for m in ASSEMBLY_GATES}
    if not metrics["schema_errors"]:
        assembly = pa.assembly_summary(
            str(config_path), n_seeds_for_variety=n_seeds_for_variety,
        )
        for mode, block in assembly["modes"].items():
            asm_gates[mode] = _assembly_gate(block)

    metric_gates = _metric_gates_from_summary(metrics.get("summary", {}))

    all_gates = {**metric_gates,
                 **{f"assembly_{m}": v for m, v in asm_gates.items()}}
    blocked = [g for g in required_gates if not all_gates.get(g)]

    return {
        "config_id": metrics.get("config_id"),
        "config_path": str(config_path),
        "schema_valid": not metrics["schema_errors"],
        "schema_errors": metrics["schema_errors"],
        "metric_gates": metric_gates,
        "assembly_gates": asm_gates,
        "required_gates": required_gates,
        "accepted": not metrics["schema_errors"] and not blocked,
        "blocked_gates": blocked,
        "metrics": metrics,
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
    p.add_argument("config", help="path to a v1 config")
    p.add_argument("--n-seeds", type=int, default=200,
                   help="seeds for variety stats (assembly)")
    p.add_argument("--require", nargs="*", default=None,
                   help=f"gate names to require (default: all). "
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
