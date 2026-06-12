"""R7 · Воспроизводимость / модульность / автоматизация — мета-требование к самому
валидатору: декларация детерминизма каждой метрики, provenance, repro-чек двух прогонов.

R7 · Reproducibility / modularity / automation — a meta requirement about the
validator itself: per-metric determinism declarations, provenance, a two-run repro check.
"""
from __future__ import annotations
import json


def determinism_flags(requirements: dict) -> list[dict]:
    """Декларация по каждой метрике: deterministic + requires (изоляция недетерминированного).
    Вход: requirements (R-id → результат модуля). Выход: list[dict].
    Per-metric declaration: deterministic + requires (isolates non-deterministic parts).
    In: requirements (R-id → module result). Out: list[dict]."""
    out = []
    for rid, r in requirements.items():
        for mname, m in r.get("metrics", {}).items():
            out.append({"requirement": rid, "metric": mname,
                        "deterministic": m.get("deterministic"),
                        "requires": m.get("requires")})
    return out


def provenance(acceptance: dict, config: dict) -> dict:
    """Паспорт прогона: версии конфига/порогов, seed, эмбеддер, снапшот OpenAlex.
    Вход: acceptance (пороги), config. Выход: dict.
    Run provenance: config/threshold versions, seed, embedder, OpenAlex snapshot.
    In: acceptance (thresholds), config. Out: dict."""
    return {
        "config_id": config.get("config_id"),
        "config_version": config.get("config_version"),
        "acceptance_version": acceptance.get("version"),
        "seed": acceptance.get("seed"),
        "embedder": acceptance.get("embedder"),
        "openalex_snapshot": acceptance.get("openalex_snapshot"),
    }


def _det_values(report: dict) -> dict:
    """Только детерминированные посчитанные значения (для diff воспроизводимости).
    Вход: report. Выход: dict {R.metric → value}.
    Deterministic computed values only (for the reproducibility diff).
    In: report. Out: dict {R.metric → value}."""
    vals = {}
    for rid, r in report.get("requirements", {}).items():
        for mname, m in r.get("metrics", {}).items():
            if m.get("deterministic") and m.get("requires") is None and m.get("value") is not None:
                vals[f"{rid}.{mname}"] = m["value"]
    return vals


def reproducibility_check(validate_fn, config_path, acceptance) -> dict:
    """Два независимых прогона при фиксированном сиде → diff детерминированных метрик == 0.
    Вход: validate_fn(path, acceptance), config_path, acceptance. Выход: dict-метрика.
    Two independent runs under a fixed seed → the diff of deterministic metrics must be 0.
    In: validate_fn(path, acceptance), config_path, acceptance. Out: metric dict."""
    a = _det_values(validate_fn(config_path, acceptance))
    b = _det_values(validate_fn(config_path, acceptance))
    same = json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    return {"reproducibility_check": {"value": same, "pass": same,
                                      "deterministic": True, "requires": None, "gameable": False}}
