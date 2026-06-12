"""Интеграционные тесты пакета validation: validate()/validate_full() на реальных
конфигах репозитория (v1 axes + from-generator), структура отчёта, гейты,
детерминизм, acceptance-оверрайды.

Запуск: pytest tests/test_validation_integration.py -q  (без сети; нужен ortools)
"""
import json
from pathlib import Path

import pytest

from validation import validate, validate_full, load_acceptance
from validation import solver as S

ROOT = Path(__file__).resolve().parent.parent
V1 = ROOT / "configs" / "v1"
GEN = V1 / "from-generator"

needs_ortools = pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")


@pytest.fixture(scope="module")
def acc():
    """Быстрый acceptance: меньше сэмплов solver'а, те же гейты."""
    a = load_acceptance(None)
    a["thresholds"]["R2"]["n_samples"] = 60
    return a


@needs_ortools
def test_report_structure_on_repo_config(acc):
    rep = validate(str(V1 / "ai-safety-v1.json"), acc)
    assert rep["verdict"] in ("accept", "reject", "pending")
    assert set(rep["requirements"]) == {"R1", "R2", "R3", "R4", "R5", "R6"}
    for rid in ("R1", "R2", "R3"):
        for m in rep["requirements"][rid]["metrics"].values():
            assert {"value", "pass", "deterministic", "requires"} <= set(m)
    assert rep["provenance"]["config_id"] == "ai-safety-v1"
    # determinism_flags декларируют каждую метрику (R7)
    flagged = {(d["requirement"], d["metric"]) for d in rep["determinism_flags"]}
    assert ("R3", "morph_leak_rate") in flagged


@needs_ortools
def test_axes_format_supported(acc):
    """Старый v1 (axes, description_en) валидируется без конвертации."""
    rep = validate(str(V1 / "reasoning-v1.json"), acc)
    assert rep["requirements"]["R1"]["pass"] is not None


@needs_ortools
def test_reproducibility_gate(acc):
    rep = validate_full(str(V1 / "ai-safety-v1.json"), acc)
    assert rep["repro"]["reproducibility_check"]["pass"] is True
    assert rep["summary"]["R7"] is True


@needs_ortools
def test_two_runs_identical(acc):
    a = validate(str(V1 / "ai-safety-v1.json"), acc)
    b = validate(str(V1 / "ai-safety-v1.json"), acc)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@needs_ortools
def test_required_gates_drive_verdict(acc):
    """R4 без обогащения частично неизвестен; если потребовать только R1 —
    вердикт зависит лишь от R1."""
    only_r1 = json.loads(json.dumps(acc))
    only_r1["required_gates"] = ["R1"]
    rep = validate(str(V1 / "ai-safety-v1.json"), only_r1)
    assert rep["verdict"] == ("accept" if rep["summary"]["R1"] else "reject")
    assert rep["pending_gates"] == []


@needs_ortools
def test_enrichment_metrics_pending_without_clients(acc):
    rep = validate(str(V1 / "ai-safety-v1.json"), acc)
    r4 = rep["requirements"]["R4"]["metrics"]
    assert r4["source_attestation"]["pass"] is None
    assert r4["source_attestation"]["requires"]
    r6 = rep["requirements"]["R6"]["metrics"]
    assert all(m["pass"] is None for m in r6.values())


@needs_ortools
@pytest.mark.parametrize("cfg_path", sorted(GEN.glob("*.v1.json"))[:4],
                         ids=lambda p: p.stem)
def test_from_generator_configs_validate(acc, cfg_path):
    """Выход генерации конфигов (адаптированный в v1) проходит пайплайн без ошибок."""
    rep = validate(str(cfg_path), acc)
    assert rep["verdict"] in ("accept", "reject", "pending")
    assert rep["requirements"]["R2"]["metrics"]["is_eligible_mode1"]["value"] >= 0
    # R3 считается всегда (детерминированное ядро)
    assert rep["requirements"]["R3"]["metrics"]["morph_leak_rate"]["value"] is not None


def test_acceptance_yaml_overrides(tmp_path):
    y = tmp_path / "acc.yaml"
    y.write_text("required_gates: [R1]\nversion: '9.9.9'\n", encoding="utf-8")
    acc = load_acceptance(str(y))
    assert acc["required_gates"] == ["R1"]
    assert acc["version"] == "9.9.9"
    assert "R3" in acc["thresholds"]          # дефолтные пороги сохранились
