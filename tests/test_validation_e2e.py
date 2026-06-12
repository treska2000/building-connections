"""E2E-тесты: актуальная генерация (connections-gen / connections_v2) → валидация.

Цепочки:
  1. Нативный v2-пул (pool.json) → validate_full() напрямую (авто-детект схемы);
     кросс-чек с self-валидацией генерилки (validation.publishable).
  2. v2-пул → connections_v2.export_input.pool_to_input_schema() → input-схема v1
     → validate(); граф принадлежности эквивалентен прямому прогону пула.
  3. CLI: python validation/run.py <config> — exit-код соответствует вердикту.

Запуск: pytest tests/test_validation_e2e.py -q  (без сети; нужен ortools)
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from validation import validate, validate_full, load_acceptance
from validation import loader
from validation import solver as S

ROOT = Path(__file__).resolve().parent.parent
GEN_REPO = (ROOT / "connections-gen" if (ROOT / "connections-gen").exists()
            else ROOT.parent / "connections-gen")
POOL = GEN_REPO / "pool.json"

needs_ortools = pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")
needs_pool = pytest.mark.skipif(not POOL.exists(), reason="нет connections-gen/pool.json")


@pytest.fixture(scope="module")
def acc():
    a = load_acceptance(None)
    a["thresholds"]["R2"]["n_samples"] = 40
    return a


# ─── 1. Нативный v2-пул напрямую ─────────────────────────────────────────
@needs_ortools
@needs_pool
def test_pool_v2_validates_end_to_end(acc, tmp_path):
    rep = validate_full(str(POOL), acc)
    assert rep["verdict"] in ("accept", "reject", "pending")
    assert rep["repro"]["reproducibility_check"]["pass"] is True
    # структура пула дошла до метрик
    assert rep["requirements"]["R1"]["metrics"]["is_terms_count_ge_16"]["value"] is not None


@needs_pool
def test_pool_self_validation_cross_check():
    """Если генерилка считает пул publishable, наш R1 (объём) тоже должен
    проходить — расхождение в структуре сигналит о рассинхроне форматов."""
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    cfg = loader.pool_v2_to_config(pool)
    members, term_tags = loader.build_membership(cfg)
    if (pool.get("validation") or {}).get("publishable"):
        assert len(cfg["terms"]) >= 16
        base = sum(1 for v in members.values() if len(v) >= 4)
        assert base >= 4


# ─── 2. Экспорт через connections_v2.export_input ────────────────────────
@needs_ortools
@needs_pool
def test_export_input_equivalent_to_direct_pool(acc, tmp_path):
    sys.path.insert(0, str(GEN_REPO))
    try:
        from connections_v2.export_input import pool_to_input_schema
        from connections_v2.models import ConfigPoolV2
    except ImportError:
        pytest.skip("connections_v2 не импортируется")
    finally:
        sys.path.remove(str(GEN_REPO))

    raw = json.loads(POOL.read_text(encoding="utf-8"))
    exported = pool_to_input_schema(ConfigPoolV2.from_dict(raw))
    p = tmp_path / "exported.json"
    p.write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")

    rep_exp = validate(str(p), acc)
    rep_direct = validate(str(POOL), acc)

    # один и тот же граф принадлежности → одинаковые структурные метрики
    for rid in ("R1", "R2"):
        for mname, m in rep_direct["requirements"][rid]["metrics"].items():
            if m["requires"] is None and m["value"] is not None:
                assert rep_exp["requirements"][rid]["metrics"][mname]["value"] == m["value"], \
                    f"{rid}.{mname} расходится между пулом и экспортом"
    # и одинаковый morph_leak (имена терминов/категорий совпадают)
    assert (rep_exp["requirements"]["R3"]["metrics"]["morph_leak_rate"]["value"]
            == rep_direct["requirements"]["R3"]["metrics"]["morph_leak_rate"]["value"])


# ─── 3. CLI: exit-коды и артефакты ──────────────────────────────────────
@needs_ortools
def test_cli_exit_code_matches_verdict(tmp_path):
    cfg = ROOT / "configs" / "v1" / "ai-safety-v1.json"
    out = tmp_path / "reports"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "validation" / "run.py"),
         str(cfg), "--out", str(out)],
        capture_output=True, text=True, timeout=600)
    assert proc.returncode in (0, 1, 2), proc.stderr
    written = list(out.glob("*/metrics.json"))
    assert written, "metrics.json не записан"
    rep = json.loads(written[0].read_text(encoding="utf-8"))
    assert proc.returncode == {"accept": 0, "reject": 1, "pending": 2}[rep["verdict"]]
    md = written[0].parent / "report.md"
    assert md.exists() and "Вердикт" in md.read_text(encoding="utf-8")


# ─── 4. Батч по свежим выходам генератора ────────────────────────────────
@needs_ortools
def test_batch_from_generator_no_crashes(acc):
    gen_dir = ROOT / "configs" / "v1" / "from-generator"
    paths = sorted(gen_dir.glob("*.v1.json"))
    if not paths:
        pytest.skip("нет from-generator конфигов")
    verdicts = {}
    for p in paths[:6]:
        rep = validate(str(p), acc)
        verdicts[p.stem] = rep["verdict"]
    assert all(v in ("accept", "reject", "pending") for v in verdicts.values()), verdicts
