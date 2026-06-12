"""Юнит-тесты пакета validation: textutil, solver (mode-1/2/3), loader (v1+v2-пул),
R3 (лики/чистые), R4 (сигналы+агрегация на фейковых клиентах), R5, офлайн-деградация.

Запуск: pytest tests/test_validation_unit.py -q
Без сети, без ortools-долгих прогонов (малые n_samples).
"""
import json

import pytest

from validation import loader
from validation import solver as S
from validation import textutil as TU
from validation import r2_solvability, r3_semanticity, r4_source_quality, r5_specialization
from validation.enrich import Enrichment, arxiv_id_from_url

# ─── фикстуры: синтетический конфиг ──────────────────────────────────────
AX = {
    "Alpha": ["Gradient Descent", "Backpropagation", "Momentum Update", "Weight Decay"],
    "Beta": ["Knowledge Graph", "Entity Linking", "Ontology Merge", "Triple Store"],
    "Gamma": ["Beam Search", "Viterbi Decoding", "Pruning Cutoff", "Heuristic Frontier"],
    "Delta": ["Reward Hacking", "Goal Misgeneralization", "Spec Gaming", "Proxy Objective"],
    "Epsilon": ["Token Budget", "Context Window", "Cache Eviction", "Latency Floor"],
}


def _mk_cfg():
    nid = [1000]

    def src(k=3):
        out = []
        for _ in range(k):
            nid[0] += 1
            out.append({"url": f"https://arxiv.org/abs/2401.{nid[0]:05d}"})
        return out

    terms = [{"name": nm, "tags": [ax], "sources": src()}
             for ax, names in AX.items() for nm in names]
    terms.append({"name": "Stochastic Restart", "tags": ["Alpha"],
                  "decoy_for_tags": ["Gamma"], "sources": src()})
    terms.append({"name": "Phantom Method", "tags": ["Beta"],
                  "sources": [{"url": "https://arxiv.org/abs/2401.99999"}]})
    return {"config_id": "unit", "config_version": "0",
            "specialty": {"field": "Computer Science", "subfield": "AI", "area": "ML"},
            "tags": [{"name": a} for a in AX], "terms": terms}


@pytest.fixture(scope="module")
def cfg():
    return _mk_cfg()


@pytest.fixture(scope="module")
def graph(cfg):
    return loader.build_membership(cfg)


# ─── textutil ────────────────────────────────────────────────────────────
def test_tokenize_stem_and_suffix():
    toks = TU.tokenize("Deceptive Alignments")
    assert any(t.startswith("decept") for t in toks)      # стем
    assert any(t.startswith("~") for t in toks)           # суффикс-токен


def test_idf_rare_token_heavier_than_common():
    names = [f"Common Model {i}" for i in range(9)] + ["Rare Esoterica"]
    idf, mx = TU.build_idf(names)
    common = TU.tokenize("Common Model")[0]
    rare = TU.tokenize("Rare Esoterica")[0]
    assert TU.token_weight(rare, idf, mx) > TU.token_weight(common, idf, mx)


def test_term_in_text_faithfulness():
    assert TU.term_in_text("Reward Hacking", "We study reward hacking in RL agents.")
    assert not TU.term_in_text("Reward Hacking", "This paper is about image diffusion.")


# ─── loader: v1 (axes/tags) и v2-пул ─────────────────────────────────────
def test_loader_axes_and_tags_equivalent():
    a = {"terms": [{"name": "X", "axes": ["A"]}]}
    b = {"terms": [{"name": "X", "tags": ["A"]}]}
    assert loader.build_membership(a) == loader.build_membership(b)


def test_loader_pool_v2_normalization(tmp_path):
    pool = {
        "schema_version": "connections_v2_config_pool",
        "topic": "Stable Diffusion", "area": "text-to-image", "specialty": "diffusion",
        "whitelist_sources": ["arxiv.org"],
        "categories": [{"id": "c1", "label": "Sampling Methods"},
                       {"id": "c2", "label": "Guidance"}],
        "terms": [{"id": "t1", "label": "DDIM", "description": "d",
                   "sources": ["2311.06752v1"], "home_category": "c1",
                   "candidate_categories": ["c2"], "membership_scores": {"c1": 0.9}}],
        "validation": {"publishable": True},
    }
    p = tmp_path / "pool.json"
    p.write_text(json.dumps(pool), encoding="utf-8")
    cfg = loader.load_config(str(p))
    assert cfg["config_id"] == "stable-diffusion"
    assert cfg["specialty"]["area"] == "text-to-image"
    t = cfg["terms"][0]
    assert t["tags"] == ["Sampling Methods", "Guidance"]           # home первым
    assert t["sources"][0]["url"] == "https://arxiv.org/abs/2311.06752v1"
    assert cfg["_pool_validation"]["publishable"] is True


def test_arxiv_id_from_url():
    assert arxiv_id_from_url("https://arxiv.org/abs/2201.11903") == "2201.11903"
    assert arxiv_id_from_url("https://arxiv.org/pdf/2201.11903v2") == "2201.11903"
    assert arxiv_id_from_url("https://example.com/x") is None


# ─── solver: примитив и режимы ───────────────────────────────────────────
pytestmark_solver = pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")


@pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")
def test_count_partitions_unique_and_ambiguous(graph):
    members, _ = graph
    words = sum((sorted(members[a])[:4] for a in ["Alpha", "Beta", "Gamma", "Delta"]), [])
    assert S.count_partitions(words, members) == 1
    # склейка двух осей в одну категорию → разбивок > 1
    fused = dict(members)
    fused["AlphaBeta"] = members["Alpha"] | members["Beta"]
    assert S.count_partitions(words, fused, cap=5) > 1


@pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")
def test_mode2_sampler_draws_and_validates():
    cfg2 = _mk_cfg()
    for t in cfg2["terms"]:
        if t["name"] in ("Gradient Descent", "Backpropagation"):
            t["tags"] = ["Alpha", "Beta"]
    cfg2["terms"] += [{"name": f"Filler A{i}", "tags": ["Alpha"]} for i in range(3)]
    cfg2["terms"] += [{"name": f"Filler B{i}", "tags": ["Beta"]} for i in range(3)]
    m, tt = loader.build_membership(cfg2)
    samp = S.sample_valid_puzzles_mode2(m, tt, n_samples=60, seed=42)
    assert samp["samples_drawn"] > 0          # кандидаты с ловушкой извлекаются
    assert samp["exists"] is True             # и среди них есть однозначные


@pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")
def test_mode2_dense_overlap_infeasible():
    """Находка на golden: overlap=4 при осях 5/5 → кандидатов нет (drawn=0)."""
    cfg = {"terms": (
        [{"name": f"S{i}", "tags": ["A", "B"]} for i in range(4)]
        + [{"name": "OnlyA", "tags": ["A"]}, {"name": "OnlyB", "tags": ["B"]}]
        + [{"name": f"C{i}", "tags": ["C"]} for i in range(4)]
        + [{"name": f"D{i}", "tags": ["D"]} for i in range(4)]
        + [{"name": f"E{i}", "tags": ["E"]} for i in range(4)])}
    m, tt = loader.build_membership(cfg)
    samp = S.sample_valid_puzzles_mode2(m, tt, n_samples=40, seed=1)
    assert samp["samples_drawn"] == 0


@pytest.mark.skipif(not S.HAVE_ORTOOLS, reason="нет ortools")
def test_mode3_trap_quality_and_sampler(cfg, graph):
    members, term_tags = graph
    members_r3 = {a: set(v) for a, v in members.items()}
    members_r3["Gamma"] |= {"Stochastic Restart"}
    w = (sorted(members["Gamma"])[:3] + ["Stochastic Restart"]
         + sorted(members["Alpha"])[:4] + sorted(members["Beta"])[:4]
         + sorted(members["Delta"])[:4])
    assert S.trap_quality(w, members, members_r3) >= 1
    samp = S.sample_valid_puzzles_mode3(
        members, term_tags, {"Stochastic Restart": {"Gamma"}}, n_samples=60, seed=42)
    assert samp["exists"] is True


# ─── R3: ресёрч-конфиг morph_leak ────────────────────────────────────────
THR_R3 = {"morph_leak_link_tau": 0.74, "max_morph_leak": 0.05}


def test_r3_flags_shared_root_leak(cfg):
    c = json.loads(json.dumps(cfg))
    c["terms"] += [{"name": n, "tags": ["Zeta"]} for n in
                   ["Deceptive Alignment", "Deceptive Behaviors",
                    "Deception Probes", "Honest Reporting"]]
    m, tt = loader.build_membership(c)
    r = r3_semanticity.run(c, m, tt, THR_R3)
    flagged = {(e["term"], e["category"]) for e in r["metrics"]["morph_leak_rate"]["examples"]}
    assert ("Deceptive Alignment", "Zeta") in flagged
    assert ("Deception Probes", "Zeta") in flagged     # стем ловит decept-/decepti-


def test_r3_clean_axes_not_flagged(cfg, graph):
    m, tt = graph
    r = r3_semanticity.run(cfg, m, tt, THR_R3)
    assert r["metrics"]["morph_leak_rate"]["value"] == 0.0
    assert r["pass"] is True


def test_r3_deterministic(cfg, graph):
    m, tt = graph
    a = r3_semanticity.run(cfg, m, tt, THR_R3)["metrics"]["morph_leak_rate"]["value"]
    b = r3_semanticity.run(cfg, m, tt, THR_R3)["metrics"]["morph_leak_rate"]["value"]
    assert a == b


# ─── R4/R5 на фейковых клиентах ─────────────────────────────────────────
class FakeArxiv:
    errors = []

    def __init__(self, abstracts):
        self.abstracts = abstracts

    def metas(self, ids):
        out = {}
        for i in ids:
            if i == "2401.99999":
                out[i] = {"exists": False}
            else:
                out[i] = {"exists": True, "title": "Paper", "primary_category": "cs.LG",
                          "journal_ref": "NeurIPS 2024", "doi": "", "comment": "",
                          "abstract": self.abstracts.get(i, "generic")}
        return out


class FakeOpenAlex:
    errors = []

    def __init__(self, one_lab=False, fields=None):
        self.one_lab = one_lab
        self.fields = fields or {}

    def works_by_arxiv(self, ids):
        return {i: {"found": True, "is_retracted": False, "cited_by_count": 120,
                    "authors": ["A1" if self.one_lab else f"A{k}"], "institutions": [],
                    "field": self.fields.get(i, "Computer Science"), "subfield": "AI"}
                for k, i in enumerate(sorted(ids))}

    def term_count(self, term):
        return 250


THR_R4 = {"min_share_sane": 0.9,
          "min_share_attested": 0.9, "min_independent_groups": 3, "citations_hi": 100}


@pytest.fixture(scope="module")
def fake_en(cfg):
    abstracts = {}
    for t in cfg["terms"]:
        for s in t.get("sources", []):
            i = arxiv_id_from_url(s["url"])
            if i:
                abstracts[i] = f"We study {t['name']} in depth."
    return Enrichment(arxiv=FakeArxiv(abstracts), openalex=FakeOpenAlex())


def test_r4_faithfulness_and_reds(cfg, graph, fake_en):
    m, tt = graph
    r = r4_source_quality.run(cfg, m, tt, THR_R4, fake_en)
    pt = r["metrics"]["per_term"]["value"]
    assert pt["Gradient Descent"]["sane"] is True
    assert pt["Phantom Method"]["verdict"] == "red"            # S1: фейк-ссылка
    assert pt["Gradient Descent"]["groups"] == 3               # S6: 3 независимые группы
    assert r["metrics"]["source_attestation"]["pass"] is False  # один red блокирует гейт
    assert r["metrics"]["source_attestation"]["deterministic"] is False  # live помечен


def test_r4_one_lab_is_yellow_not_three_sources(cfg, graph):
    m, tt = graph
    abstracts = {arxiv_id_from_url(s["url"]): f"We study {t['name']}."
                 for t in cfg["terms"] for s in t.get("sources", [])}
    en = Enrichment(arxiv=FakeArxiv(abstracts), openalex=FakeOpenAlex(one_lab=True))
    pt = r4_source_quality.run(cfg, m, tt, THR_R4, en)["metrics"]["per_term"]["value"]
    assert pt["Gradient Descent"]["groups"] == 1
    assert pt["Gradient Descent"]["verdict"] == "yellow"       # 3 статьи одной лабы ≠ 3 источника


def test_r5_consistency_and_alien_field(cfg, graph, fake_en):
    m, tt = graph
    r = r5_specialization.run(cfg, m, tt, {"area_consistency_tau": 0.7}, fake_en)
    assert r["metrics"]["area_term_consistency"]["pass"] is True
    alien = Enrichment(arxiv=fake_en.arxiv,
                       openalex=FakeOpenAlex(fields={}))
    alien.openalex.works_by_arxiv = lambda ids: {
        i: {"found": True, "is_retracted": False, "cited_by_count": 1,
            "authors": [], "institutions": [], "field": "Biology", "subfield": ""}
        for i in ids}
    r2 = r5_specialization.run(cfg, m, tt, {"area_consistency_tau": 0.7}, alien)
    assert r2["metrics"]["area_term_consistency"]["pass"] is False


def test_r5_pending_without_enrichment(cfg, graph):
    r = r5_specialization.run(cfg, *graph, {"area_consistency_tau": 0.7}, None)
    assert "specialty_filled" not in r["metrics"]   # перенесена в R1 (2026-06-12)
    assert r["pass"] is None


def test_offline_cache_degrades_to_pending(cfg, graph, tmp_path):
    m, tt = graph
    en = Enrichment.live(cache_dir=str(tmp_path / "cache"), offline=True)
    r = r4_source_quality.run(cfg, m, tt, THR_R4, en)
    assert r["metrics"]["source_sanity_check"]["pass"] is None   # unknown, не крэш
