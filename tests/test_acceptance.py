"""pytest suite для acceptance.py на едином движке validation/.

Наследник tests/test_config_metrics.py (движок config_metrics удалён 2026-06-11,
см. shim в config_metrics.py). Сценарии T1–T6 из `config_spec_agent_2026-06-05.md`
сохранены и переадресованы новому движку:
- T1: минимальный валидный конфиг → структурные гейты проходят
- T2: 10 терминов → R1 False
- T3: термин ссылается на несуществующую категорию → schema_errors
- T5: <3 источников → R1_volume False (счётчик источников в R1 с 2026-06-12)
- T6: одна категория на 50 терминов → R1 False

Run from repo root:
    python -m pytest tests/test_acceptance.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from validation import acceptance as ac  # noqa: E402
from validation import legacy_to_v1 as lv  # noqa: E402
from validation.schema_check import validate_schema  # noqa: E402
from validation import solver as S  # noqa: E402

needs_ortools = pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")

# --------------------------------------------------------------------------- #
# Fixtures: имена без общих корней (morph_leak-чистые)
# --------------------------------------------------------------------------- #
CLEAN_AX = {
    "Alpha Concepts": ["Gradient Descent", "Backprop Update", "Momentum Step", "Weight Decay"],
    "Beta Concepts": ["Knowledge Graph", "Entity Linking", "Ontology Merge", "Triple Store"],
    "Gamma Concepts": ["Beam Search", "Viterbi Decoding", "Pruning Cutoff", "Heuristic Frontier"],
    "Delta Concepts": ["Reward Hacking", "Goal Drift", "Spec Gaming", "Proxy Objective"],
}


def _term(name, tags, sources=None, decoys=None):
    t = {"name": name, "tags": tags}
    if sources is not None:
        t["sources"] = [{"url": u} for u in sources]
    if decoys is not None:
        t["decoy_for_tags"] = decoys
    return t


def _cfg(tags, terms, **overrides):
    base = {
        "config_id": "test-cfg",
        "title": "Test",
        "specialty": {"field": "cs", "subfield": "ai", "area": "test"},
        "whitelist_sources": ["arxiv.org"],
        "tags": [{"name": n} for n in tags],
        "terms": terms,
    }
    base.update(overrides)
    return base


@pytest.fixture
def minimal_valid_cfg():
    """T1: 16 терминов, 4 категории по 4, по 3 arXiv-источника, без ликов."""
    terms = []
    for i, (ax, names) in enumerate(CLEAN_AX.items()):
        for j, nm in enumerate(names):
            terms.append(_term(
                nm, [ax],
                sources=[f"https://arxiv.org/abs/24{i}{j}.1{k}00{i}" for k in range(3)],
                decoys=[list(CLEAN_AX)[(i + 1) % 4]] if j == 0 else None,
            ))
    return _cfg(list(CLEAN_AX), terms)


def _write(tmp_path, cfg, name="cfg.json"):
    p = tmp_path / name
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Schema (validation.schema_check; понимает tags и axes)
# --------------------------------------------------------------------------- #

def test_validate_schema_ok(minimal_valid_cfg):
    assert validate_schema(minimal_valid_cfg) == []


def test_validate_schema_missing_top():
    errs = validate_schema({"terms": [{"name": "x", "tags": ["A"]}]})
    assert any("config_id" in e for e in errs)
    assert any("tags" in e for e in errs)


def test_validate_schema_term_references_unknown_category(minimal_valid_cfg):
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    cfg["terms"][0]["tags"] = ["No Such Axis"]
    assert any("unknown category" in e for e in validate_schema(cfg))


def test_validate_schema_duplicate_category(minimal_valid_cfg):
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    cfg["tags"].append({"name": "Alpha Concepts"})
    assert any("duplicate category" in e for e in validate_schema(cfg))


def test_validate_schema_duplicate_term(minimal_valid_cfg):
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    cfg["terms"].append(dict(cfg["terms"][0]))
    assert any("duplicate term" in e for e in validate_schema(cfg))


def test_validate_schema_axes_format_still_valid():
    cfg = {"config_id": "x", "specialty": "ml",
           "axes": [{"name": "A"}],
           "terms": [{"name": f"t{i}", "axes": ["A"]} for i in range(4)]}
    assert validate_schema(cfg) == []


def test_validate_schema_specialty_string_ok(minimal_valid_cfg):
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    cfg["specialty"] = "ai safety"
    assert validate_schema(cfg) == []


# --------------------------------------------------------------------------- #
# acceptance(): metric gates на новом движке (T1, T2, T5, T6)
# --------------------------------------------------------------------------- #

@needs_ortools
def test_acceptance_minimal_passes_structural_core(tmp_path, minimal_valid_cfg):
    p = _write(tmp_path, minimal_valid_cfg)
    v = ac.acceptance(p, n_seeds_for_variety=10, n_samples=60)
    assert v["schema_valid"]
    assert v["metric_gates"]["R1_volume"] is True
    assert v["metric_gates"]["R2_mode1"] is True
    assert v["metric_gates"]["R3_morph_leak"] is True
    assert v["metric_gates"]["R5_specialty"] is True
    # R4 — чисто качественный: без обогащения честно PENDING (None)
    assert v["metric_gates"]["R4_sources"] is None


@needs_ortools
def test_acceptance_few_terms_blocks_r1(tmp_path, minimal_valid_cfg):
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    cfg["terms"] = cfg["terms"][:10]                       # T2
    v = ac.acceptance(_write(tmp_path, cfg), n_seeds_for_variety=10, n_samples=40)
    assert v["metric_gates"]["R1_volume"] is False
    assert "R1_volume" in v["blocked_gates"]


@needs_ortools
def test_acceptance_single_fat_category_blocks_r1(tmp_path):
    terms = [_term(f"Solo Item {i} {chr(65+i)}x", ["Mono"]) for i in range(50)]  # T6
    v = ac.acceptance(_write(tmp_path, _cfg(["Mono"], terms)),
                      n_seeds_for_variety=10, n_samples=40)
    assert v["metric_gates"]["R1_volume"] is False


@needs_ortools
def test_acceptance_missing_sources_blocks_r1(tmp_path, minimal_valid_cfg):
    """T5: счётчик источников перенесён в R1 (2026-06-12) — без источников блокирует R1."""
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    for t in cfg["terms"]:
        t.pop("sources", None)                             # T5
    v = ac.acceptance(_write(tmp_path, cfg), n_seeds_for_variety=10, n_samples=40)
    assert v["metric_gates"]["R1_volume"] is False
    assert "R1_volume" in v["blocked_gates"]


@needs_ortools
def test_acceptance_morph_leak_blocks_r3(tmp_path, minimal_valid_cfg):
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    cfg["tags"].append({"name": "Zeta"})
    # лик-пара с редким общим стемом (df=2 из 20 → высокий IDF-вес ≥ τ);
    # остальные члены Zeta чистые
    cfg["terms"] += [_term(n, ["Zeta"]) for n in
                     ["Deceptive Alignment", "Deceptive Behaviors",
                      "Honest Reporting", "Sandbagging Probe"]]
    v = ac.acceptance(_write(tmp_path, cfg), n_seeds_for_variety=10, n_samples=40)
    assert v["metric_gates"]["R3_morph_leak"] is False
    assert "R3_morph_leak" in v["blocked_gates"]


@needs_ortools
def test_acceptance_relaxed_gates_let_blocked_config_through(tmp_path, minimal_valid_cfg):
    cfg = json.loads(json.dumps(minimal_valid_cfg))
    for t in cfg["terms"]:
        t.pop("sources", None)
    v = ac.acceptance(_write(tmp_path, cfg), n_seeds_for_variety=10, n_samples=60,
                      required_gates=("R2_mode1", "R5_specialty"))
    assert v["accepted"], f"blocked: {v['blocked_gates']}"


def test_acceptance_invalid_schema_short_circuits(tmp_path):
    v = ac.acceptance(_write(tmp_path, {"config_id": "broken"}),
                      n_seeds_for_variety=10)
    assert not v["schema_valid"]
    assert not v["accepted"]
    assert v["metrics"] is None
    assert all(g is None for g in v["metric_gates"].values())


# --------------------------------------------------------------------------- #
# Реальные конфиги (smoke; фиксируем известное поведение нового движка)
# --------------------------------------------------------------------------- #

@needs_ortools
def test_real_reasoning_blocks_on_morph_leak():
    v = ac.acceptance(ROOT / "configs" / "v1" / "reasoning-v1.json",
                      n_seeds_for_variety=20, n_samples=60)
    assert v["schema_valid"]
    # известные form-лики (Chain-of-Thought ↔ Tree of Thoughts и т.п.)
    assert v["metric_gates"]["R3_morph_leak"] is False


@needs_ortools
def test_real_ai_safety_verdict_structure():
    v = ac.acceptance(ROOT / "configs" / "v1" / "ai-safety-v1.json",
                      n_seeds_for_variety=20, n_samples=60)
    assert v["schema_valid"]
    assert set(v["metric_gates"]) == set(ac.METRIC_GATES)
    assert isinstance(v["blocked_gates"], list)
    assert v["metrics"]["verdict"] in ("accept", "reject", "pending")


# --------------------------------------------------------------------------- #
# legacy adapter (без изменений)
# --------------------------------------------------------------------------- #

def test_legacy_adapter_extracts_axes_from_tags():
    legacy = [
        {"name": "A", "tags": ["X", "Y"], "description": "d"},
        {"name": "B", "tags": ["X"], "description": "d"},
    ]
    v1 = lv.adapt(legacy, config_id="t", title="T",
                  field="f", subfield="s", area="a")
    assert {a["name"] for a in v1["axes"]} == {"X", "Y"}
    assert v1["terms"][0]["axes"] == ["X", "Y"]


def test_legacy_adapter_skips_termless_or_tagless():
    legacy = [{"name": "", "tags": ["X"]}, {"name": "C", "tags": []},
              {"name": "D", "tags": ["X"]}]
    v1 = lv.adapt(legacy, config_id="t", title="T",
                  field="f", subfield="s", area="a")
    assert [t["name"] for t in v1["terms"]] == ["D"]
