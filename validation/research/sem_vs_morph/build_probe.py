"""
build_probe.py  -- construct the probe pair set and compute the lexical axis.

Runs with NO network and NO embedding model. It (1) loads the curated term bank
and SS/LL/BB pairs, (2) auto-generates DD pairs (cross-domain, low lexical overlap)
to reach >=300 pairs, (3) computes lex(i,j) on the names, (4) writes probe_pairs.csv
and prints a per-quadrant summary so you can confirm the set is constructed correctly
*before* spending any embedding budget.
"""
import json, re, csv, random, itertools, statistics
from rapidfuzz.distance import Levenshtein

random.seed(7)
STOP = {"the", "and", "for", "with", "of", "a", "to", "models", "model"}

def tokens(name, min_len=5):
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return {t for t in toks if len(t) >= min_len and t not in STOP}

def stem(t):
    for suf in ("ization", "isation", "ational", "tion", "ing", "ers", "er",
                "ed", "ity", "al", "ic", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)]
    return t

def stem_tokens(name, min_len=4):
    return {stem(t) for t in re.findall(r"[a-z0-9]+", name.lower())
            if len(t) >= min_len and t not in STOP}

def jaccard(a, b):
    if not a and not b:
        return 0.0
    u = a | b
    return len(a & b) / len(u) if u else 0.0

def fuzz_ratio(s1, s2):
    return Levenshtein.normalized_similarity(s1.lower(), s2.lower())

def main():
    data = json.load(open("probe.json"))
    terms = data["terms"]
    ids = list(terms)
    name = {i: terms[i]["name"] for i in ids}
    dom = {i: terms[i]["domain"] for i in ids}

    pairs = []
    seen = set()
    def add(a, b, quad, source):
        key = tuple(sorted((a, b)))
        if a == b or key in seen:
            return
        seen.add(key)
        pairs.append((a, b, quad, source))

    for a, b, q in data["curated_pairs"]:
        add(a, b, q, "curated")

    # Auto-generate DD: cross-domain pairs with low name-token overlap.
    cross = [(a, b) for a, b in itertools.combinations(ids, 2)
             if dom[a] != dom[b]
             and jaccard(tokens(name[a]), tokens(name[b])) == 0.0
             and fuzz_ratio(name[a], name[b]) < 0.45]
    random.shuffle(cross)
    target_total = 320
    for a, b in cross:
        if len(pairs) >= target_total:
            break
        add(a, b, "DD", "auto")

    rows = []
    for a, b, q, src in pairs:
        ta, tb = tokens(name[a]), tokens(name[b])
        sa, sb = stem_tokens(name[a]), stem_tokens(name[b])
        rows.append({
            "a": a, "b": b, "name_a": name[a], "name_b": name[b],
            "domain_a": dom[a], "domain_b": dom[b],
            "quad": q, "source": src,
            "lex_jaccard": round(jaccard(ta, tb), 4),
            "lex_jaccard_stem": round(jaccard(sa, sb), 4),
            "fuzz_ratio": round(fuzz_ratio(name[a], name[b]), 4),
        })

    with open("probe_pairs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    # Summary
    print(f"terms={len(ids)}  domains={len(set(dom.values()))}  pairs={len(rows)}")
    print(f"{'quad':4} {'n':>4} {'lex_jac':>8} {'lex_stem':>9} {'fuzz':>7}")
    for q in ("SS", "LL", "BB", "DD"):
        sub = [r for r in rows if r["quad"] == q]
        if not sub:
            continue
        m = lambda k: statistics.mean(r[k] for r in sub)
        print(f"{q:4} {len(sub):>4} {m('lex_jaccard'):>8.3f} "
              f"{m('lex_jaccard_stem'):>9.3f} {m('fuzz_ratio'):>7.3f}")
    print("\nConstruction check (what we WANT to see):")
    print("  LL should have HIGH lex (shared surface form), SS/DD LOW lex.")
    print("  If LL lex is not clearly > SS lex, the trap is not a trap -> fix the set.")

if __name__ == "__main__":
    main()
