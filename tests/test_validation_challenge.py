"""E2E-челлендж валидатора — исполняемая гарантия двух обещаний:
1) на эталонном (честном) конфиге валидатор обязан давать accept;
2) на негативах, каждый из которых ломает РОВНО ОДИН критерий, валидатор обязан
   давать reject с этим критерием в blocked_gates.

Негативы синтетические (по одному на критерий): R1 — объём / дубль / источники /
specialty; R2 — нет solo-терминов (mode-1 не собирается); R4 — фейк-ссылка и
неверные источники (термина нет в абстрактах); R5 — все источники из чужого поля.
Обогащение — фейковые клиенты (без сети), поэтому R4/R5 проверяются по-настоящему,
а не остаются PENDING. R3/R6 выведены из пайплайна (2026-06-12) — их негативы
вернутся вместе с критериями.

E2E challenge of the validator — an executable guarantee of two promises:
1) on the etalon (honest) config the validator must accept;
2) on negatives, each breaking EXACTLY ONE criterion, the validator must reject
   with that criterion in blocked_gates.

The negatives are synthetic (one per criterion): R1 — volume / duplicate / sources /
specialty; R2 — no solo terms (mode-1 unbuildable); R4 — a fake link and wrong
sources (the term absent from the abstracts); R5 — all sources off-field.
Enrichment uses fake clients (no network), so R4/R5 are genuinely exercised instead
of staying PENDING. R3/R6 are out of the pipeline (2026-06-12) — their negatives
return together with the criteria.

Запуск: pytest tests/test_validation_challenge.py -q  (без сети; нужен ortools)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from validation import validate, load_acceptance  # noqa: E402
from validation import solver as S  # noqa: E402
from validation.enrich import Enrichment, arxiv_id_from_url  # noqa: E402

import test_acceptance as TA  # noqa: E402  (CLEAN_AX, _term, _cfg — shared fixtures)
import test_validation_unit as TU  # noqa: E402  (FakeArxiv, FakeOpenAlex)

needs_ortools = pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

def honest_cfg() -> dict:
    """Эталон: 16 терминов, 4 категории по 4 solo, по 3 arXiv-источника, без ликов,
    specialty заполнена. Etalon: 16 terms, 4 categories of 4 solo terms each,
    3 arXiv sources per term, leak-free names, specialty filled in."""
    terms = []
    for i, (ax, names) in enumerate(TA.CLEAN_AX.items()):
        for j, nm in enumerate(names):
            terms.append(TA._term(
                nm, [ax],
                sources=[f"https://arxiv.org/abs/24{i}{j}.1{k}00{i}" for k in range(3)]))
    cfg = TA._cfg(list(TA.CLEAN_AX), terms)
    cfg["specialty"] = {"field": "computer science", "subfield": "ai",
                        "area": "machine learning methods"}
    return cfg


def fake_enrichment(cfg, faithful=True, field=None) -> Enrichment:
    """Фейковое обогащение: каждый источник резолвится; faithful=True кладёт имя
    термина в абстракты его источников; field перекрашивает поле всех источников.
    Fake enrichment: every source resolves; faithful=True puts the term name into
    its sources' abstracts; field recolors every source's field."""
    abstracts = {}
    if faithful:
        for t in cfg["terms"]:
            for s in t.get("sources", []):
                i = arxiv_id_from_url(s["url"])
                if i:
                    abstracts[i] = f"We study {t['name']} in detail."
    en = Enrichment(arxiv=TU.FakeArxiv(abstracts), openalex=TU.FakeOpenAlex())
    if field is not None:
        en.openalex.works_by_arxiv = lambda ids: {
            i: {"found": True, "is_retracted": False, "cited_by_count": 10,
                "authors": [f"A{k}"], "institutions": [], "field": field, "subfield": ""}
            for k, i in enumerate(sorted(ids))}
    return en


def run_pipeline(tmp_path, cfg, en):
    """Полный прогон: JSON-файл -> validate() (как в проде, но n_samples=40).
    Full run: JSON file -> validate() (as in production, but with n_samples=40)."""
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    acc = load_acceptance(None)
    acc["thresholds"]["R2"]["n_samples"] = 40
    return validate(str(p), acc, en=en)


# --------------------------------------------------------------------------- #
# Promise 1: the etalon is accepted
# --------------------------------------------------------------------------- #

@needs_ortools
def test_etalon_accepted(tmp_path):
    cfg = honest_cfg()
    rep = run_pipeline(tmp_path, cfg, fake_enrichment(cfg))
    assert rep["blocked_gates"] == []
    assert rep["pending_gates"] == []
    assert rep["verdict"] == "accept"


# --------------------------------------------------------------------------- #
# Promise 2: each criterion-breaking negative is rejected on that criterion
# --------------------------------------------------------------------------- #

def _neg_r1_too_few_terms(cfg):
    cfg["terms"] = cfg["terms"][:10]
    return cfg


def _neg_r1_duplicate_term(cfg):
    cfg["terms"].append(json.loads(json.dumps(cfg["terms"][0])))
    return cfg


def _neg_r1_missing_sources(cfg):
    for t in cfg["terms"][:8]:          # share with >=3 sources drops to 0.5 < 0.9
        t["sources"] = t["sources"][:2]
    return cfg


def _neg_r1_empty_specialty(cfg):
    cfg["specialty"] = {"field": "", "subfield": "", "area": ""}
    return cfg


def _neg_r2_no_solo_terms(cfg):
    tags = list(TA.CLEAN_AX)
    for t in cfg["terms"]:              # every term in 2 categories -> no clean board
        own = t["tags"][0]
        t["tags"] = [own, tags[(tags.index(own) + 1) % 4]]
    return cfg


def _neg_r4_fake_link(cfg):
    cfg["terms"][0]["sources"] = [{"url": "https://arxiv.org/abs/2401.99999"}]
    return cfg


NEGATIVES = [
    ("R1", _neg_r1_too_few_terms),
    ("R1", _neg_r1_duplicate_term),
    ("R1", _neg_r1_missing_sources),
    ("R1", _neg_r1_empty_specialty),
    ("R2", _neg_r2_no_solo_terms),
    ("R4", _neg_r4_fake_link),
]


@needs_ortools
@pytest.mark.parametrize("rid,breaker", NEGATIVES,
                         ids=[f"{r}-{f.__name__}" for r, f in NEGATIVES])
def test_negative_blocks_its_criterion(tmp_path, rid, breaker):
    cfg = breaker(honest_cfg())
    rep = run_pipeline(tmp_path, cfg, fake_enrichment(cfg))
    assert rep["verdict"] == "reject", rep["blocked_gates"]
    assert rid in rep["blocked_gates"], rep["blocked_gates"]


@needs_ortools
def test_negative_r4_unfaithful_sources(tmp_path):
    """Источники резолвятся, но термина в их абстрактах нет -> sanity блокирует R4.
    Sources resolve, yet the term is absent from their abstracts -> sanity blocks R4."""
    cfg = honest_cfg()
    rep = run_pipeline(tmp_path, cfg, fake_enrichment(cfg, faithful=False))
    assert rep["verdict"] == "reject"
    assert "R4" in rep["blocked_gates"]


@needs_ortools
def test_negative_r5_off_field_sources(tmp_path):
    """Все источники из чужого поля (Biology при specialty=computer science) -> R5.
    Every source is off-field (Biology vs specialty=computer science) -> R5."""
    cfg = honest_cfg()
    rep = run_pipeline(tmp_path, cfg, fake_enrichment(cfg, field="Biology"))
    assert rep["verdict"] == "reject"
    assert "R5" in rep["blocked_gates"]
