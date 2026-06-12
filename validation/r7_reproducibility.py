"""Паспорт валидатора (бывший R7) — НЕ критерий валидации конфига, а логирование
самого валидатора (решение 2026-06-12): версия валидатора и provenance прогона.
reproducibility_check — self-test валидатора: гоняется в тестовом контракте и по
флагу `--repro`, не в каждом прогоне (детерминизм проверяется при настройке метрик,
дальше его незачем перепроверять). Сводка determinism_flags удалена из отчёта —
это статическая характеристика метрик, не зависящая от прогона; per-metric флаг
`deterministic` остаётся внутри каждой метрики (его читает self-test).

Validator passport (former R7) — NOT a config-validation criterion but the
validator's own logging (2026-06-12 decision): validator version and run provenance.
reproducibility_check is the validator's self-test: it runs in the test contract and
under the `--repro` flag, not on every run (determinism is verified when metrics are
tuned; re-checking each run is waste). The determinism_flags summary was removed from
the report — it is a static property of the metrics, independent of the run; the
per-metric `deterministic` flag stays inside each metric (the self-test reads it).
"""
from __future__ import annotations
import json

# Validator code version: bump on any change to metric logic or thresholds defaults,
# so reports can be compared across versions ("this version is good, that one is not").
VALIDATOR_VERSION = "1.0"


def provenance(acceptance: dict, config: dict) -> dict:
    """Паспорт прогона: версии конфига/порогов, seed, эмбеддер, снапшот OpenAlex.
    Вход: acceptance (пороги), config. Выход: dict.
    Run provenance: config/threshold versions, seed, embedder, OpenAlex snapshot.
    In: acceptance (thresholds), config. Out: dict."""
    return {
        "validator_version": VALIDATOR_VERSION,
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
