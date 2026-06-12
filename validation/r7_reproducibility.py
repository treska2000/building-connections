"""R7 · Воспроизводимость / модульность / автоматизация — мета (на сам валидатор)."""
from __future__ import annotations
import json


def determinism_flags(requirements: dict) -> list[dict]:
    """Каждая метрика декларирует deterministic + requires (изоляция недетерминированного)."""
    out = []
    for rid, r in requirements.items():
        for mname, m in r.get("metrics", {}).items():
            out.append({"requirement": rid, "metric": mname,
                        "deterministic": m.get("deterministic"),
                        "requires": m.get("requires")})
    return out


def provenance(acceptance: dict, config: dict) -> dict:
    return {
        "config_id": config.get("config_id"),
        "config_version": config.get("config_version"),
        "acceptance_version": acceptance.get("version"),
        "seed": acceptance.get("seed"),
        "embedder": acceptance.get("embedder"),
        "openalex_snapshot": acceptance.get("openalex_snapshot"),
    }


def _det_values(report: dict) -> dict:
    """Только детерминированные посчитанные значения (для diff воспроизводимости)."""
    vals = {}
    for rid, r in report.get("requirements", {}).items():
        for mname, m in r.get("metrics", {}).items():
            if m.get("deterministic") and m.get("requires") is None and m.get("value") is not None:
                vals[f"{rid}.{mname}"] = m["value"]
    return vals


def reproducibility_check(validate_fn, config_path, acceptance) -> dict:
    """Два прогона при фикс. сиде/версиях → diff детерминированных метрик == 0."""
    a = _det_values(validate_fn(config_path, acceptance))
    b = _det_values(validate_fn(config_path, acceptance))
    same = json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    return {"reproducibility_check": {"value": same, "pass": same,
                                      "deterministic": True, "requires": None, "gameable": False}}
