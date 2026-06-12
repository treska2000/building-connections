"""pytest suite for the connections-gen integration (rich_to_v1 + from_generator)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from validation import acceptance as ac  # noqa: E402
from validation import rich_to_v1 as r2v  # noqa: E402
from validation import from_generator as fg  # noqa: E402


# --------------------------------------------------------------------------- #
# rich_to_v1 adapter (connections-gen output -> v1)
# --------------------------------------------------------------------------- #

def _rich_pool(n_terms_per_axis=4, multi_axis_share=0.0, sources_per_term=None):
    """Build a synthetic rich pool in the shape connections-gen emits."""
    axes = [
        ("cat_a", "Brittle target formation"),
        ("cat_b", "Concealed capability expression"),
        ("cat_c", "Thin feedback channels"),
        ("cat_d", "Out-of-distribution fragility"),
    ]
    sources = sources_per_term or ["2406.10162", "2403.03185", "2508.17511"]

    terms = []
    for i, (cid, _) in enumerate(axes):
        for j in range(n_terms_per_axis):
            cands = [cid]
            if multi_axis_share and j == 0:
                cands.append(axes[(i + 1) % len(axes)][0])
            terms.append({
                "id": f"t_{i}_{j}",
                "canonical_id": f"term_{i}_{j}",
                "term": f"Concept-{i}-{j}",
                "description": "A test term.",
                "home_category": cid,
                "candidate_categories": cands,
                "sources": sources,
            })
    return {
        "config_id": "synthetic_rich",
        "domain": "ai",
        "language": "en",
        "meta": {"subdomain": "ai_safety"},
        "categories": [
            {"id": cid, "tag": tag, "kind": "conceptual",
             "difficulty": "medium", "rationale": f"axis {tag}"}
            for cid, tag in axes
        ],
        "terms": terms,
    }


def test_rich_to_v1_basic_mapping():
    v1 = r2v.adapt(_rich_pool())
    assert v1["config_id"] == "synthetic_rich"
    assert v1["specialty"] == {"field": "ai", "subfield": "ai_safety", "area": "ai_safety"}
    assert v1["whitelist_sources"] == ["arxiv.org"]
    assert {a["name"] for a in v1["axes"]} == {
        "Brittle target formation", "Concealed capability expression",
        "Thin feedback channels", "Out-of-distribution fragility",
    }
    assert all(t["axes"] for t in v1["terms"])
    # Each term inherits 3 arxiv sources via evidence URLs.
    assert all(len(t.get("evidence", [])) == 3 for t in v1["terms"])
    assert all(t["evidence"][0]["url"].startswith("https://arxiv.org/abs/")
               for t in v1["terms"])


def test_rich_to_v1_home_category_lands_first():
    v1 = r2v.adapt(_rich_pool(multi_axis_share=1.0))
    multi = [t for t in v1["terms"] if len(t["axes"]) > 1]
    assert multi, "expected at least one multi-axis term"
    # The home category should be axes[0] in each case.
    for t in multi:
        # We hand-tied home -> first axis in the fixture.
        assert t["axes"][0] in {"Brittle target formation", "Concealed capability expression",
                                "Thin feedback channels", "Out-of-distribution fragility"}


def test_rich_to_v1_skips_todo_and_unparseable_sources():
    rich = _rich_pool(sources_per_term=["2406.10162", "TODO:grounding", "random-string"])
    v1 = r2v.adapt(rich)
    # Only the well-formed arxiv ID survives.
    assert all(len(t.get("evidence", [])) == 1 for t in v1["terms"])
    assert v1["terms"][0]["evidence"][0]["url"] == "https://arxiv.org/abs/2406.10162"


def test_arxiv_url_parses_various_formats():
    assert r2v._arxiv_url("2406.10162") == "https://arxiv.org/abs/2406.10162"
    assert r2v._arxiv_url("arXiv:1405.4604 Some Title") == "https://arxiv.org/abs/1405.4604"
    assert r2v._arxiv_url("TODO:grounding") is None
    assert r2v._arxiv_url("https://example.com") is None
    assert r2v._arxiv_url("") is None


# --------------------------------------------------------------------------- #
# from_generator.verdict_for_rich - end-to-end
# --------------------------------------------------------------------------- #

def test_from_generator_end_to_end(tmp_path):
    """Synthetic rich pool -> v1 -> acceptance. We don't expect strict accept
    (no decoys, single source domain), but we do expect a valid schema,
    populated metric gates, and assembly to succeed."""
    rich_path = tmp_path / "synthetic.json"
    rich_path.write_text(json.dumps(_rich_pool()), encoding="utf-8")
    v1_dir = tmp_path / "v1"

    v = fg.verdict_for_rich(rich_path, n_seeds=10, v1_dir=v1_dir, make_plot=False)
    assert v["schema_valid"]
    assert Path(v["v1_path"]).exists()
    # R1, R5 always pass for this fixture.
    assert v["metric_gates"]["R1_volume"]
    assert v["metric_gates"]["R5_specialty"] is None   # качественный, без обогащения PENDING
    # счётчик источников теперь в R1 (3 arXiv-источника на термин → проходит);
    # R4 без обогащения — честно PENDING
    assert v["metric_gates"]["R1_volume"] is True
    assert v["metric_gates"]["R4_sources"] is None
    # Single-tag synthetic with 4 axes of 4 -> assembly passes.
    assert v["assembly_gates"]["normal"]
    assert v["assembly_gates"]["advanced"]


def test_from_generator_relaxed_gates_accept_synthetic(tmp_path):
    rich_path = tmp_path / "synthetic.json"
    rich_path.write_text(json.dumps(_rich_pool()), encoding="utf-8")
    v1_dir = tmp_path / "v1"

    # Drop the gates we know the synthetic pool can't satisfy (R4/R5 need enrichment)
    relaxed = ("R1_volume", "R2_mode1",
               "assembly_normal", "assembly_advanced")
    v = fg.verdict_for_rich(rich_path, n_seeds=10, v1_dir=v1_dir,
                            required_gates=relaxed, make_plot=False)
    assert v["accepted"], f"blocked: {v['blocked_gates']}"


def test_from_generator_batch_skips_flat_format(tmp_path):
    """A folder mixing rich pools (dicts) and flat-format files (lists) should
    process only the rich ones."""
    rich_path = tmp_path / "rich.json"
    rich_path.write_text(json.dumps(_rich_pool()), encoding="utf-8")
    flat_path = tmp_path / "flat.json"
    flat_path.write_text(json.dumps([{"name": "x", "tags": ["y"]}]), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("not json")

    v1_dir = tmp_path / "v1"
    results = fg.batch(tmp_path, v1_dir=v1_dir, n_seeds=10)
    assert len(results) == 1
    assert results[0]["config_id"] == "synthetic_rich"
