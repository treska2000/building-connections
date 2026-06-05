"""Config metrics (R1-R6) for Connections-Bench v1 configs.

No LLM calls in here, anything that needs human/LLM judgement (axis
non-obviousness, "source uses term in the same sense", "area matches terms")
is left for a separate pass. The metrics tool reports those as null with
a note in the module-level README, not as a key in every dict.

Spec: ../../Connections Bench Research/config_spec_human_2026-06-05.md
Schema: configs/v1/schema.json

Run:
    python tests/config_metrics.py configs/v1/ai-safety-v1.json --out-dir reports/ai-safety
"""
import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from statistics import median
from urllib.parse import urlparse


# --- loading / validation ---

def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_schema(cfg):
    """Structural + referential validation. Returns a list of error strings.

    jsonschema-light: we don't bother with the schema file at runtime, since
    the rules are simple and we want concrete reference errors ("term X points
    at unknown axis Y") more than JSON-pointer noise.
    """
    if not isinstance(cfg, dict):
        return ["config root is not an object"]

    errors = []
    for k in ("config_id", "title", "specialty", "whitelist_sources", "axes", "terms"):
        if k not in cfg:
            errors.append(f"missing top-level key: {k}")

    sp = cfg.get("specialty") or {}
    if not isinstance(sp, dict):
        errors.append("specialty is not an object")
    else:
        for k in ("field", "subfield", "area"):
            if not sp.get(k):
                errors.append(f"specialty.{k} is empty or missing")

    axes = cfg.get("axes") or []
    if not isinstance(axes, list) or not axes:
        errors.append("axes must be a non-empty array")

    axis_names = set()
    for i, ax in enumerate(axes):
        if not isinstance(ax, dict):
            errors.append(f"axes[{i}] is not an object")
            continue
        name = ax.get("name")
        if not name:
            errors.append(f"axes[{i}].name is empty or missing")
            continue
        if name in axis_names:
            errors.append(f"duplicate axis name: {name!r}")
        axis_names.add(name)

    terms = cfg.get("terms") or []
    if not isinstance(terms, list) or not terms:
        errors.append("terms must be a non-empty array")

    seen_terms = set()
    for i, t in enumerate(terms):
        if not isinstance(t, dict):
            errors.append(f"terms[{i}] is not an object")
            continue
        name = t.get("name")
        if not name:
            errors.append(f"terms[{i}].name is empty or missing")
        elif name in seen_terms:
            errors.append(f"duplicate term name: {name!r}")
        else:
            seen_terms.add(name)
        if not t.get("axes"):
            errors.append(f"terms[{i}].axes is empty or missing")
        for ref in t.get("axes", []) or []:
            if ref not in axis_names:
                errors.append(f"terms[{i}].axes references unknown axis: {ref!r}")
        for ref in t.get("decoy_for_axes", []) or []:
            if ref not in axis_names:
                errors.append(f"terms[{i}].decoy_for_axes references unknown axis: {ref!r}")
        for j, e in enumerate(t.get("evidence", []) or []):
            if not isinstance(e, dict) or not e.get("url"):
                errors.append(f"terms[{i}].evidence[{j}] missing url")

    return errors


# --- shared helpers ---

WORD_RE = re.compile(r"[A-Za-z]+")


def _axis_sizes(cfg):
    sizes = Counter()
    for t in cfg["terms"]:
        for ax in t.get("axes", []):
            sizes[ax] += 1
    return sizes


def _domain(url):
    d = (urlparse(url).netloc or "").lower()
    return d[4:] if d.startswith("www.") else d


def _long_words(s, min_len=5):
    return [w.lower() for w in WORD_RE.findall(s or "") if len(w) >= min_len]


# --- R1 volume ---

def m_volume(cfg):
    sizes = _axis_sizes(cfg)
    n_terms = len(cfg["terms"])
    n_axes = len(sizes)

    sorted_sizes = sorted(sizes.values(), reverse=True)
    big = [s for s in sorted_sizes if s >= 20]
    active = [s for s in sorted_sizes if s >= 4]
    terms_in_big = sum(
        1 for t in cfg["terms"]
        if any(sizes[a] >= 20 for a in t.get("axes", []))
    )

    return {
        "n_terms": n_terms,
        "n_axes": n_axes,
        "mean_terms_per_axis": round(sum(sorted_sizes) / n_axes, 2) if n_axes else 0.0,
        "median_terms_per_axis": median(sorted_sizes) if sorted_sizes else 0,
        "min_terms_per_axis": min(sorted_sizes) if sorted_sizes else 0,
        "max_terms_per_axis": max(sorted_sizes) if sorted_sizes else 0,
        "axes_with_4plus": len(active),
        "axes_with_20plus": len(big),
        "share_axes_20plus": round(len(big) / n_axes, 3) if n_axes else 0.0,
        "share_terms_in_20plus_axis": round(terms_in_big / n_terms, 3) if n_terms else 0.0,
        "axis_size_distribution": dict(sorted(Counter(sorted_sizes).items())),
        "pass_r1": (
            n_terms >= 16
            and n_axes >= 4
            and len(active) >= 4
            and min(active, default=0) >= 4
        ),
    }


# --- R2 lexical leak (proxy for axis non-obviousness) ---

def m_lexical_leak(cfg):
    """A term "leaks" when its name shares a >=5-letter word with one of its
    own axis names. Cheap proxy for "the axis is named after its members".
    Real check needs an LLM and lives in a later pass."""
    leaked = []
    for t in cfg["terms"]:
        words = set(_long_words(t["name"]))
        for ax in t.get("axes", []):
            shared = words & set(_long_words(ax))
            if shared:
                leaked.append({
                    "term": t["name"],
                    "axis": ax,
                    "shared_tokens": sorted(shared),
                })
                break

    n = len(cfg["terms"])
    rate = len(leaked) / n if n else 0.0
    return {
        "lexical_leak_rate": round(rate, 3),
        "leaked_count": len(leaked),
        "leaked_examples": leaked[:10],
        "pass_r2_lexical": rate <= 0.05,
    }


# --- R3 evidence ---

def m_evidence(cfg):
    """Term passes when >=3 evidence URLs hit distinct whitelist domains.
    Multiple URLs from the same domain count once. The whitelist lives in
    the config itself, so different configs can use different sources.
    """
    whitelist = {d.lower().replace("www.", "") for d in cfg.get("whitelist_sources", [])}

    def on_whitelist(url):
        d = _domain(url)
        return bool(d) and any(d == w or d.endswith("." + w) for w in whitelist)

    n_total = len(cfg["terms"])
    counts = []
    under_threshold = []
    for t in cfg["terms"]:
        domains = {_domain(e["url"]) for e in (t.get("evidence") or [])
                   if isinstance(e, dict) and on_whitelist(e.get("url", ""))}
        counts.append(len(domains))
        if len(domains) < 3:
            under_threshold.append({"term": t["name"], "n_sources": len(domains)})

    n_pass = sum(1 for c in counts if c >= 3)
    share = n_pass / n_total if n_total else 0.0
    return {
        "share_terms_3plus_sources": round(share, 3),
        "mean_sources_per_term": round(sum(counts) / n_total, 2) if n_total else 0.0,
        "terms_under_3_sources_count": len(under_threshold),
        "terms_under_3_sources_examples": under_threshold[:10],
        "pass_r3": share >= 0.9,
    }


# --- R4 mode-fit (deterministic proxy; real check needs the JS solver) ---

def m_mode_fit(cfg):
    by_axis = {a["name"]: [] for a in cfg["axes"]}
    for t in cfg["terms"]:
        for ax in t.get("axes", []):
            if ax in by_axis:
                by_axis[ax].append(t)

    # mode-1: enough axes with 4+ single-tag terms for a clean 4x4
    solo_per_axis = {
        ax: sum(1 for t in ts if len(t.get("axes", [])) == 1)
        for ax, ts in by_axis.items()
    }
    mode1 = [ax for ax, n in solo_per_axis.items() if n >= 4]

    # mode-2: pairs of axes with 4+ shared terms (natural decoy pairs)
    names = list(by_axis)
    member_sets = {ax: {t["name"] for t in by_axis[ax]} for ax in names}
    pairs = []
    for a, b in combinations(names, 2):
        shared = len(member_sets[a] & member_sets[b])
        if shared >= 4:
            pairs.append({"axis_1": a, "axis_2": b, "shared_terms": shared})
    pairs.sort(key=lambda p: -p["shared_terms"])

    decoy_pool = sum(1 for t in cfg["terms"] if t.get("decoy_for_axes"))

    return {
        "mode1_eligible_axes": len(mode1),
        "mode1_eligible_axis_names": mode1,
        "mode2_cooccurrence_pairs": len(pairs),
        "mode2_pairs_examples": pairs[:5],
        "mode3_decoy_pool": decoy_pool,
        "pass_mode1": len(mode1) >= 4,
        "pass_mode2": bool(pairs),
        "pass_mode3": decoy_pool >= 4,
    }


# --- R5 specialty ---

def m_specialty(cfg):
    sp = cfg.get("specialty") or {}
    return {
        "field": sp.get("field"),
        "subfield": sp.get("subfield"),
        "area": sp.get("area"),
        "pass_r5": all(sp.get(k) for k in ("field", "subfield", "area")),
    }


# --- R6 capacity vs overlap ---

def m_capacity(cfg):
    """Two "non-playable" numbers, you don't need to play any puzzles to
    compute them, so they're a cheap way to compare configs:

    puzzle_capacity_mode1 = sum over (4-subset of >=4-term axes) of prod(C(n,4))
    axis_overlap_density  = (multi-tag share) * (mean tags per term)
    """
    sizes = _axis_sizes(cfg)
    active = [(ax, n) for ax, n in sizes.items() if n >= 4]

    capacity = 0
    for sub in combinations(active, 4):
        p = 1
        for _, n in sub:
            p *= math.comb(n, 4)
        capacity += p

    tag_counts = [len(t.get("axes", [])) for t in cfg["terms"]]
    n = len(tag_counts)
    multi_share = sum(1 for c in tag_counts if c >= 2) / n if n else 0.0
    mean_tags = sum(tag_counts) / n if n else 0.0

    return {
        "puzzle_capacity_mode1": capacity,
        "log10_puzzle_capacity": round(math.log10(capacity), 3) if capacity > 0 else None,
        "axis_overlap_density": round(multi_share * mean_tags, 4),
        "multi_tag_term_share": round(multi_share, 3),
        "mean_tags_per_term": round(mean_tags, 3),
        "eligible_axes_count": len(active),
    }


# --- bootstrap for the R6 plot ---

def bootstrap_capacity_overlap(cfg, n_samples=50, seed=42):
    """Random subsets of the axis pool give us a scatter of (overlap, capacity)
    points so we can SEE whether overlap and capacity actually move together
    in this config. One number per config is not enough."""
    rng = random.Random(seed)
    all_axes = [a["name"] for a in cfg["axes"]]
    if len(all_axes) < 4:
        return []

    points = []
    for _ in range(n_samples):
        k = rng.randint(4, len(all_axes))
        keep = set(rng.sample(all_axes, k))

        sub_terms = []
        for t in cfg["terms"]:
            sub_ax = [a for a in t.get("axes", []) if a in keep]
            if not sub_ax:
                continue
            new_t = dict(t, axes=sub_ax)
            if "decoy_for_axes" in new_t:
                new_t["decoy_for_axes"] = [a for a in new_t["decoy_for_axes"] if a in keep]
            sub_terms.append(new_t)

        sub_cfg = dict(cfg, terms=sub_terms,
                      axes=[a for a in cfg["axes"] if a["name"] in keep])
        cap = m_capacity(sub_cfg)
        points.append((
            cap["axis_overlap_density"],
            cap["log10_puzzle_capacity"] or 0.0,
        ))
    return points


def plot_r6(points, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5))
    if points:
        xs, ys = zip(*points)
        ax.scatter(xs, ys, alpha=0.6, s=40)
        ax.set_xlabel("axis_overlap_density")
        ax.set_ylabel("log10(puzzle_capacity, mode-1)")
        ax.set_title("R6 · capacity vs overlap (bootstrap subsamples)")
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, "Not enough axes for bootstrap (need >=4)",
                ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


# --- report writer ---

def _badge(p):
    if p is None:
        return "-"
    return "PASS" if p else "FAIL"


def write_report(metrics, out_path):
    r1 = metrics["R1_volume"]
    r2 = metrics["R2_lexical_leak"]
    r3 = metrics["R3_evidence"]
    r4 = metrics["R4_mode_fit"]
    r5 = metrics["R5_specialty"]
    r6 = metrics["R6_capacity"]

    lines = [f"# Config metrics - `{metrics.get('config_id','?')}`", ""]
    if metrics["schema_errors"]:
        lines.append("## Schema errors\n")
        lines += [f"- {e}" for e in metrics["schema_errors"]]
        lines.append("")

    lines += [
        "## Summary\n",
        "| Req | Key metric | Value | Status |",
        "|---|---|---:|:-:|",
        f"| R1 volume | n_terms / n_axes / axes>=4 | {r1['n_terms']} / {r1['n_axes']} / {r1['axes_with_4plus']} | {_badge(r1['pass_r1'])} |",
        f"| R2 leak rate | <=5% target | {r2['lexical_leak_rate']:.1%} | {_badge(r2['pass_r2_lexical'])} |",
        f"| R3 evidence | share >=3 sources | {r3['share_terms_3plus_sources']:.1%} | {_badge(r3['pass_r3'])} |",
        f"| R4 mode-1 | eligible axes | {r4['mode1_eligible_axes']} | {_badge(r4['pass_mode1'])} |",
        f"| R4 mode-2 | co-occurrence pairs | {r4['mode2_cooccurrence_pairs']} | {_badge(r4['pass_mode2'])} |",
        f"| R4 mode-3 | decoy pool | {r4['mode3_decoy_pool']} | {_badge(r4['pass_mode3'])} |",
        f"| R5 specialty | filled | {r5['field']} / {r5['subfield']} / {r5['area']} | {_badge(r5['pass_r5'])} |",
        f"| R6 capacity | log10 · overlap | {r6['log10_puzzle_capacity']} · {r6['axis_overlap_density']} | - |",
        "",
        "## R1 · volume\n",
        f"- mean / median / min / max terms per axis: {r1['mean_terms_per_axis']} / "
        f"{r1['median_terms_per_axis']} / {r1['min_terms_per_axis']} / {r1['max_terms_per_axis']}",
        f"- axes with >=20 terms: {r1['axes_with_20plus']} ({r1['share_axes_20plus']:.0%})",
        f"- share of terms in any >=20-axis: {r1['share_terms_in_20plus_axis']:.0%}",
        f"- size distribution: `{r1['axis_size_distribution']}`",
        "",
        "## R2 · lexical leak\n",
        f"- rate: {r2['lexical_leak_rate']:.1%} ({r2['leaked_count']} terms)",
    ]
    if r2["leaked_examples"]:
        lines.append("- examples (first 10):")
        for ex in r2["leaked_examples"]:
            lines.append(f"  - `{ex['term']}` -> `{ex['axis']}` (shared: {ex['shared_tokens']})")

    lines += [
        "",
        "## R3 · evidence\n",
        f"- share with >=3 whitelist-domain sources: {r3['share_terms_3plus_sources']:.1%}",
        f"- mean unique-domain sources per term: {r3['mean_sources_per_term']}",
        f"- terms under threshold: {r3['terms_under_3_sources_count']}",
    ]
    for ex in r3["terms_under_3_sources_examples"]:
        lines.append(f"  - `{ex['term']}` -> {ex['n_sources']} source(s)")

    lines += [
        "",
        "## R4 · mode-fit\n",
        f"- mode-1 axes (>=4 single-tag terms): {r4['mode1_eligible_axes']} -> {r4['mode1_eligible_axis_names']}",
        f"- mode-2 pairs (>=4 shared): {r4['mode2_cooccurrence_pairs']}",
    ]
    for ex in r4["mode2_pairs_examples"]:
        lines.append(f"  - {ex['axis_1']} <-> {ex['axis_2']} ({ex['shared_terms']} shared)")
    lines.append(f"- mode-3 decoy pool: {r4['mode3_decoy_pool']}")

    lines += [
        "",
        "## R5 · specialty\n",
        f"- field: `{r5['field']}` · subfield: `{r5['subfield']}` · area: `{r5['area']}`",
        "",
        "## R6 · capacity vs overlap\n",
        f"- puzzle_capacity_mode1 = {r6['puzzle_capacity_mode1']:,}",
        f"- log10(capacity) = {r6['log10_puzzle_capacity']}",
        f"- axis_overlap_density = {r6['axis_overlap_density']}",
        f"- multi-tag share / mean tags-per-term = {r6['multi_tag_term_share']} / {r6['mean_tags_per_term']}",
        "- plot: `r6_capacity_vs_overlap.png`",
        "",
        "## Pending LLM review",
        "- R2: axis-name non-obviousness (is each axis a concept rather than an entity type?)",
        "- R3: source uses term in the same sense as its description",
        "- R5: area actually matches the terms in the config",
        "- R4: real solver-based mode-fit (uniqueness, trap quality)",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


# --- top-level runner ---

def run(config_path, out_dir, *, make_plot=True, seed=42):
    cfg = load_config(config_path)
    errors = validate_schema(cfg)

    if errors:
        empty = {}
        metrics = {
            "config_id": cfg.get("config_id"),
            "schema_errors": errors,
            "R1_volume": empty, "R2_lexical_leak": empty, "R3_evidence": empty,
            "R4_mode_fit": empty, "R5_specialty": empty, "R6_capacity": empty,
            "summary": {k: None for k in (
                "pass_r1", "pass_r2_lexical", "pass_r3",
                "pass_mode1", "pass_mode2", "pass_mode3", "pass_r5",
            )},
        }
    else:
        r1 = m_volume(cfg)
        r2 = m_lexical_leak(cfg)
        r3 = m_evidence(cfg)
        r4 = m_mode_fit(cfg)
        r5 = m_specialty(cfg)
        r6 = m_capacity(cfg)
        metrics = {
            "config_id": cfg.get("config_id"),
            "schema_errors": errors,
            "R1_volume": r1, "R2_lexical_leak": r2, "R3_evidence": r3,
            "R4_mode_fit": r4, "R5_specialty": r5, "R6_capacity": r6,
            "summary": {
                "pass_r1": r1["pass_r1"],
                "pass_r2_lexical": r2["pass_r2_lexical"],
                "pass_r3": r3["pass_r3"],
                "pass_mode1": r4["pass_mode1"],
                "pass_mode2": r4["pass_mode2"],
                "pass_mode3": r4["pass_mode3"],
                "pass_r5": r5["pass_r5"],
            },
        }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # write_report assumes populated metric blocks. Skip on schema errors,
    # the json file already carries the error list.
    if not errors:
        write_report(metrics, out / "report.md")
        if make_plot:
            plot_r6(bootstrap_capacity_overlap(cfg, seed=seed),
                    out / "r6_capacity_vs_overlap.png")

    return metrics


def main():
    p = argparse.ArgumentParser(description="Connections-Bench config metrics (v1).")
    p.add_argument("config", help="path to config JSON")
    p.add_argument("--out-dir", default="reports", help="output directory")
    p.add_argument("--no-plot", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    try:
        metrics = run(args.config, args.out_dir, make_plot=not args.no_plot, seed=args.seed)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"error reading config: {e}", file=sys.stderr)
        return 1

    if metrics["schema_errors"]:
        print(json.dumps(metrics["schema_errors"], ensure_ascii=False), file=sys.stderr)
        return 1

    out = Path(args.out_dir)
    files = [out / "metrics.json", out / "report.md"]
    if not args.no_plot:
        files.append(out / "r6_capacity_vs_overlap.png")
    print("Wrote:", ", ".join(str(f) for f in files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
