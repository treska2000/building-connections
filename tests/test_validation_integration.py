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
    # R3/R6 выведены из пайплайна (2026-06-12) — активные критерии R1/R2/R4/R5
    assert set(rep["requirements"]) == {"R1", "R2", "R4", "R5"}
    for rid in ("R1", "R2"):
        for m in rep["requirements"][rid]["metrics"].values():
            assert {"value", "pass", "deterministic", "requires"} <= set(m)
    assert rep["provenance"]["config_id"] == "ai-safety-v1"
    # validator passport: version in provenance; determinism_flags summary removed
    # (static metric property, 2026-06-12 decision) — the per-metric flag stays
    assert rep["provenance"]["validator_version"]
    assert "determinism_flags" not in rep
    assert rep["requirements"]["R1"]["metrics"]["is_dedup"]["deterministic"] is True


@needs_ortools
def test_axes_format_supported(acc):
    """Старый v1 (axes, description_en) валидируется без конвертации."""
    rep = validate(str(V1 / "reasoning-v1.json"), acc)
    assert rep["requirements"]["R1"]["pass"] is not None


@needs_ortools
def test_reproducibility_self_test(acc):
    """Self-test валидатора: гоняется здесь (тестовый контракт) и по --repro,
    не в каждом прогоне; в критерии (summary) не входит."""
    rep = validate_full(str(V1 / "ai-safety-v1.json"), acc)
    assert rep["repro"]["reproducibility_check"]["pass"] is True
    assert "R7" not in rep["summary"]
    assert "R7" not in rep["required_gates"]


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
    assert "source_count" not in r4          # счётчик переехал в R1 (2026-06-12)
    assert rep["requirements"]["R4"]["pass"] is None   # без обогащения R4 целиком PENDING
    # R4 в required (2026-06-12): без обогащения accept недостижим
    assert rep["verdict"] != "accept"
    assert r4["source_attestation"]["pass"] is None
    assert r4["source_attestation"]["requires"]


@needs_ortools
@pytest.mark.parametrize("cfg_path", sorted(GEN.glob("*.v1.json"))[:4],
                         ids=lambda p: p.stem)
def test_from_generator_configs_validate(acc, cfg_path):
    """Выход генерации конфигов (адаптированный в v1) проходит пайплайн без ошибок."""
    rep = validate(str(cfg_path), acc)
    assert rep["verdict"] in ("accept", "reject", "pending")
    assert rep["requirements"]["R2"]["metrics"]["is_eligible_mode1"]["value"] >= 0


@needs_ortools
def test_only_isolation_runs_selected_requirements(acc):
    """Режим отладки --only: выполняются только выбранные блоки."""
    from validation import validate as v_validate
    rep = v_validate(str(V1 / "ai-safety-v1.json"), acc, only={"R1"})
    assert set(rep["requirements"]) == {"R1"}
    # невыбранные требования не считались и в required дают pending, не false
    assert "R2" in rep["pending_gates"] or rep["verdict"] in ("reject", "pending")


def test_acceptance_yaml_overrides(tmp_path):
    y = tmp_path / "acc.yaml"
    y.write_text("required_gates: [R1]\nversion: '9.9.9'\n", encoding="utf-8")
    acc = load_acceptance(str(y))
    assert acc["required_gates"] == ["R1"]
    assert acc["version"] == "9.9.9"
    assert "R2" in acc["thresholds"]          # дефолтные пороги сохранились
