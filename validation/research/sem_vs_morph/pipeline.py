"""
pipeline.py -- full empirical pipeline for the semantic-vs-morphological link rule
on gemini-embedding-2-preview.

REQUIRES network access to the Gemini API and an API key in env GEMINI_API_KEY
(or GOOGLE_API_KEY). It is intentionally NOT run inside the sandbox that produced
this file (no Google egress there). Run it where the API is reachable:

    pip install google-genai scikit-learn scipy rapidfuzz matplotlib numpy
    export GEMINI_API_KEY=...      # https://aistudio.google.com/apikey
    python build_probe.py          # writes probe_pairs.csv
    python pipeline.py --dims 3072 1536 768 --tasks SEMANTIC_SIMILARITY CLUSTERING

Outputs: results.json, and PNGs (scatter_lex_sem.png, auc_vs_scale.png, null_dist.png).

Model facts used (verified from Google docs/blog, Mar 2026):
  * model id            : gemini-embedding-2-preview  (alias: gemini-embedding-2)
  * MRL output dims     : 3072 (default), 1536, 768
  * CRITICAL            : dims < 3072 are NOT L2-normalized by the API -> we always
                          normalize before cosine.
  * task instructions   : task_type string (e.g. SEMANTIC_SIMILARITY, CLUSTERING).
  * SDK call            : client.models.embed_content(model, contents, config=
                          types.EmbedContentConfig(output_dimensionality, task_type))
"""
import os, json, time, argparse, itertools, hashlib, math, random
import numpy as np
from collections import defaultdict
from rapidfuzz.distance import Levenshtein
from sklearn.metrics import roc_auc_score, adjusted_rand_score, silhouette_score
from sklearn.cluster import KMeans
from scipy.stats import spearmanr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MODEL = "gemini-embedding-2-preview"
CACHE = "embeddings_cache.json"
SEED = 7
random.seed(SEED); np.random.seed(SEED)

# ----------------------------------------------------------------------------- #
# Embedding client with on-disk cache and retry/backoff.
# ----------------------------------------------------------------------------- #
class Embedder:
    def __init__(self, model=MODEL):
        from google import genai
        from google.genai import types
        self._types = types
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY.")
        self.client = genai.Client(api_key=key)
        self.model = model
        self.cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    def _key(self, text, dim, task):
        h = hashlib.sha1(f"{self.model}|{dim}|{task}|{text}".encode()).hexdigest()
        return h

    def embed(self, text, dim, task):
        k = self._key(text, dim, task)
        if k in self.cache:
            return np.asarray(self.cache[k], dtype=np.float32)
        cfg = self._types.EmbedContentConfig(output_dimensionality=dim, task_type=task)
        for attempt in range(6):
            try:
                r = self.client.models.embed_content(
                    model=self.model, contents=text, config=cfg)
                v = np.asarray(r.embeddings[0].values, dtype=np.float32)
                v = v / (np.linalg.norm(v) + 1e-12)        # MRL dims<3072 unnormalized
                self.cache[k] = v.tolist()
                return v
            except Exception as e:
                wait = 2 ** attempt
                print(f"  retry {attempt} ({e}); sleep {wait}s")
                time.sleep(wait)
        raise RuntimeError(f"embed failed for: {text[:60]}")

    def flush(self):
        json.dump(self.cache, open(CACHE, "w"))

# ----------------------------------------------------------------------------- #
# Lexical axis (mirrors build_probe.py)
# ----------------------------------------------------------------------------- #
import re
STOP = {"the","and","for","with","of","a","to","models","model"}
def toks(s, n=5):
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) >= n and t not in STOP}
def jaccard(a, b):
    u = a | b
    return len(a & b)/len(u) if u else 0.0
def lex(n1, n2):
    return jaccard(toks(n1), toks(n2))
def cos(a, b):
    return float(np.dot(a, b))            # already L2-normalized

def cohens_d(x, y):
    x, y = np.asarray(x), np.asarray(y)
    nx, ny = len(x), len(y)
    sp = math.sqrt(((nx-1)*x.var(ddof=1) + (ny-1)*y.var(ddof=1)) / (nx+ny-2))
    return (x.mean() - y.mean()) / (sp + 1e-12)

# ----------------------------------------------------------------------------- #
# Load probe
# ----------------------------------------------------------------------------- #
def load_probe():
    probe = json.load(open("probe.json"))
    import csv
    pairs = list(csv.DictReader(open("probe_pairs.csv")))
    return probe, pairs

def input_text(term, mode):
    if mode == "name":  return term["name"]
    if mode == "desc":  return term["desc"]
    if mode == "name_desc": return f"{term['name']}: {term['desc']}"
    raise ValueError(mode)

# ----------------------------------------------------------------------------- #
# 1-2. Separability & de-confound
# ----------------------------------------------------------------------------- #
def separability(emb, terms, pairs, dim, task):
    """For one (dim,task), compute sem on name/desc inputs and SS-vs-LL stats."""
    def sem_for(mode):
        cache = {tid: emb.embed(input_text(terms[tid], mode), dim, task) for tid in terms}
        out = {}
        for p in pairs:
            out[(p["a"], p["b"])] = cos(cache[p["a"]], cache[p["b"]])
        return out
    sem_name = sem_for("name")
    sem_desc = sem_for("desc")

    def quad(q, d): return [d[(p["a"], p["b"])] for p in pairs if p["quad"] == q]
    res = {}
    for label, d in (("name", sem_name), ("desc", sem_desc)):
        ss, ll = quad("SS", d), quad("LL", d)
        y = [1]*len(ss) + [0]*len(ll)
        res[label] = {
            "auc_SS_vs_LL": round(roc_auc_score(y, ss+ll), 4),
            "cohens_d_SS_LL": round(cohens_d(ss, ll), 3),
            "mean_SS": round(float(np.mean(ss)), 4),
            "mean_LL": round(float(np.mean(ll)), 4),
            "mean_DD": round(float(np.mean(quad("DD", d))), 4),
            "mean_BB": round(float(np.mean(quad("BB", d))), 4),
        }
        lexv = [lex(p["name_a"], p["name_b"]) for p in pairs]
        semv = [d[(p["a"], p["b"])] for p in pairs]
        rho, _ = spearmanr(lexv, semv)
        res[label]["rho_lex_sem"] = round(float(rho), 3)   # "mirror of form" test
    res["deconfound_gain_auc"] = round(res["desc"]["auc_SS_vs_LL"]
                                       - res["name"]["auc_SS_vs_LL"], 4)
    return res, sem_name, sem_desc

# ----------------------------------------------------------------------------- #
# 3. Null-normalization (scale-invariant link signal)
# ----------------------------------------------------------------------------- #
def loo_centroid(vecs):
    """leave-one-out centroids: (sum - v_i)/(n-1), L2-normalized."""
    M = np.stack(vecs); s = M.sum(0)
    out = (s[None, :] - M) / (len(M) - 1)
    out /= (np.linalg.norm(out, axis=1, keepdims=True) + 1e-12)
    return out

def config_signal(member_vecs, n_perm=200, rng=None):
    """
    member_vecs: dict category -> list of member term vectors (descriptions).
    Returns per-link raw sim (to own LOO centroid) and a permutation-null z-score
    obtained by reshuffling term->category membership at fixed sizes/density.
    """
    rng = rng or np.random.default_rng(SEED)
    cats = list(member_vecs)
    if not any(len(member_vecs[c]) >= 2 for c in cats):
        raise ValueError("config_signal: need at least one category with >=2 members")
    allv, owner = [], []
    for c in cats:
        for v in member_vecs[c]:
            allv.append(v); owner.append(c)
    allv = np.stack(allv); owner = np.array(owner)
    idx = {c: np.where(owner == c)[0] for c in cats}

    def link_sims(assign):
        sims = np.empty(len(allv))
        for c in cats:
            members = np.where(assign == c)[0]
            if len(members) < 2:
                sims[members] = 0.0; continue
            cen = loo_centroid([allv[i] for i in members])
            for k, i in enumerate(members):
                sims[i] = float(np.dot(allv[i], cen[k]))
        return sims

    raw = link_sims(owner)
    null = np.empty((n_perm, len(allv)))
    for t in range(n_perm):
        perm = owner.copy(); rng.shuffle(perm)
        null[t] = link_sims(perm)
    mu, sd = null.mean(0), null.std(0) + 1e-9
    z = (raw - mu) / sd
    return {"raw": raw, "z": z, "owner": owner,
            "raw_thr_med": float(np.median(raw)),
            "null_mean": float(mu.mean())}

# ----------------------------------------------------------------------------- #
# 4. Scale test: merge domains into configs of size 4/8/30/100 "categories".
#    (Here a "category" = a fine sub-topic; we approximate with domain splits.
#     Replace with your real tags + category descriptions for production.)
# ----------------------------------------------------------------------------- #
def scale_test(emb, terms, dim, task, scales=(4, 8, 30, 100)):
    by_dom = defaultdict(list)
    for tid, t in terms.items():
        by_dom[t["domain"]].append(tid)
    descvec = {tid: emb.embed(terms[tid]["desc"], dim, task) for tid in terms}

    n_terms = len(terms)
    out = {}
    for D in scales:
        # Build D synthetic categories by splitting each domain into chunks.
        cats, c = {}, 0
        per = max(1, math.ceil(D / len(by_dom)))
        for dom, members in by_dom.items():
            random.Random(SEED+D).shuffle(members)
            for chunk in np.array_split(members, per):
                if len(chunk) >= 2 and len(cats) < D:
                    cats[f"cat{c}"] = [descvec[m] for m in chunk]; c += 1
        # Need >=2 categories of >=2 members each; otherwise this scale is not
        # representable from the available terms (needs ~2*D terms). Skip cleanly.
        if len(cats) < 2:
            out[D] = {"skipped": f"only {len(cats)} categories buildable from "
                                 f"{n_terms} terms (need ~{2*D}); plug real category "
                                 f"tags+descriptions for this scale",
                      "n_cats_built": len(cats)}
            continue
        sig = config_signal(cats, n_perm=150)
        out[D] = {"raw_median": round(sig["raw_thr_med"], 4),
                  "n_cats_built": len(cats),
                  "null_mean": round(sig["null_mean"], 4),
                  "z_median": round(float(np.median(sig["z"])), 3),
                  "z_p10": round(float(np.percentile(sig["z"], 10)), 3),
                  "n_links": int(len(sig["raw"]))}
    return out

# ----------------------------------------------------------------------------- #
# 6. Aggregation comparison (ARI is overlap-biased; margin tolerates overlap)
# ----------------------------------------------------------------------------- #
def aggregation(emb, terms, dim, task):
    tids = list(terms)
    X = np.stack([emb.embed(terms[t]["desc"], dim, task) for t in tids])
    labels = [terms[t]["domain"] for t in tids]
    uniq = sorted(set(labels)); lid = {d: i for i, d in enumerate(uniq)}
    y = np.array([lid[l] for l in labels])
    km = KMeans(n_clusters=len(uniq), n_init=10, random_state=SEED).fit(X)
    ari = adjusted_rand_score(y, km.labels_)
    sil = silhouette_score(X, y, metric="cosine")
    # margin per term: sim to own-domain LOO centroid minus best other-domain centroid
    cen = {}
    for d in uniq:
        idx = [i for i, l in enumerate(labels) if l == d]
        cen[d] = X[idx].mean(0); cen[d] /= np.linalg.norm(cen[d]) + 1e-12
    margins = []
    for i, t in enumerate(tids):
        own = labels[i]
        idx = [j for j, l in enumerate(labels) if l == own and j != i]
        oc = X[idx].mean(0); oc /= np.linalg.norm(oc) + 1e-12
        sim_in = float(np.dot(X[i], oc))
        sim_out = max(float(np.dot(X[i], cen[d])) for d in uniq if d != own)
        margins.append(sim_in - sim_out)
    return {"ARI_overlap_biased": round(float(ari), 3),
            "silhouette_cosine": round(float(sil), 3),
            "margin_mean": round(float(np.mean(margins)), 4),
            "margin_frac_positive": round(float(np.mean(np.array(margins) > 0)), 3)}

# ----------------------------------------------------------------------------- #
# 7. Adversarial: recompute SS/LL AUC using attacker-rewritten LL descriptions.
#    Provide adversarial_descs.json = {term_id: "rewritten desc"} to run.
# ----------------------------------------------------------------------------- #
def adversarial(emb, terms, pairs, dim, task):
    if not os.path.exists("adversarial_descs.json"):
        return {"skipped": "provide adversarial_descs.json to run"}
    adv = json.load(open("adversarial_descs.json"))
    base = {t: emb.embed(terms[t]["desc"], dim, task) for t in terms}
    atk = {t: emb.embed(adv.get(t, terms[t]["desc"]), dim, task) for t in terms}
    def auc(vecs):
        ss = [cos(vecs[p["a"]], vecs[p["b"]]) for p in pairs if p["quad"] == "SS"]
        ll = [cos(vecs[p["a"]], vecs[p["b"]]) for p in pairs if p["quad"] == "LL"]
        return roc_auc_score([1]*len(ss)+[0]*len(ll), ss+ll)
    return {"auc_clean": round(auc(base), 4), "auc_attacked": round(auc(atk), 4)}

# ----------------------------------------------------------------------------- #
# Plots
# ----------------------------------------------------------------------------- #
def plot_scatter(pairs, sem_desc, fname="scatter_lex_sem.png"):
    col = {"SS":"#1f77b4","LL":"#d62728","BB":"#2ca02c","DD":"#7f7f7f"}
    plt.figure(figsize=(6,5))
    for q in ("DD","BB","SS","LL"):
        xs = [lex(p["name_a"],p["name_b"]) for p in pairs if p["quad"]==q]
        ys = [sem_desc[(p["a"],p["b"])] for p in pairs if p["quad"]==q]
        plt.scatter(xs, ys, s=18, alpha=.7, label=q, c=col[q])
    plt.xlabel("lexical Jaccard (names)"); plt.ylabel("sem_desc cosine")
    plt.legend(); plt.title("lex x sem by quadrant"); plt.tight_layout()
    plt.savefig(fname, dpi=130)

def plot_scale(scale_res, fname="auc_vs_scale.png"):
    Ds = [D for D in sorted(scale_res) if "raw_median" in scale_res[D]]
    if len(Ds) < 2:
        return  # not enough representable scales to plot
    raw = [scale_res[D]["raw_median"] for D in Ds]
    nul = [scale_res[D]["null_mean"] for D in Ds]
    zmd = [scale_res[D]["z_median"] for D in Ds]
    fig, ax = plt.subplots(1,2, figsize=(10,4))
    ax[0].plot(Ds, raw, "o-", label="raw sim (median)")
    ax[0].plot(Ds, nul, "s--", label="null mean")
    ax[0].set_xscale("log"); ax[0].set_xlabel("# categories"); ax[0].set_title("RAW drifts"); ax[0].legend()
    ax[1].plot(Ds, zmd, "o-", color="green"); ax[1].set_xscale("log")
    ax[1].set_xlabel("# categories"); ax[1].set_title("null-z median (should be flat)")
    plt.tight_layout(); plt.savefig(fname, dpi=130)

# ----------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dims", nargs="+", type=int, default=[3072, 1536, 768])
    ap.add_argument("--tasks", nargs="+", default=["SEMANTIC_SIMILARITY", "CLUSTERING"])
    args = ap.parse_args()

    probe, pairs = load_probe()
    terms = probe["terms"]
    emb = Embedder()
    results = {"model": MODEL, "configs": {}}
    best = None
    for dim in args.dims:
        for task in args.tasks:
            tag = f"dim{dim}_{task}"
            print("==", tag)
            sep, sem_name, sem_desc = separability(emb, terms, pairs, dim, task)
            scl = scale_test(emb, terms, dim, task)
            agg = aggregation(emb, terms, dim, task)
            adv = adversarial(emb, terms, pairs, dim, task)
            results["configs"][tag] = {"separability": sep, "scale_test": scl,
                                       "aggregation": agg, "adversarial": adv}
            emb.flush()
            cand = (sep["desc"]["auc_SS_vs_LL"], tag, sem_desc)
            if best is None or cand[0] > best[0]:
                best = cand
    # plots from the best config
    plot_scatter(pairs, best[2])
    plot_scale(results["configs"][best[1]]["scale_test"])
    results["recommended_by_auc"] = best[1]
    json.dump(results, open("results.json", "w"), indent=2)
    print("\nWROTE results.json ; best config by desc AUC:", best[1])

if __name__ == "__main__":
    main()
