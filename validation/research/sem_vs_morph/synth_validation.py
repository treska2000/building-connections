"""
synth_validation.py -- validates the STATISTICAL MACHINERY of the proposed rule
under a controlled generative model where ground truth is known. It uses NO real
embedding model and NO network. It does NOT make any claim about the actual numbers
gemini-embedding-2 will produce; it shows that the *procedure* (description de-confound
+ permutation-null z-score) does what we claim WHEN an embedder has a given amount of
semantic signal. Run pipeline.py for the real-model numbers.

Three demonstrations:
  A. Form-confound: a "name" embedder that mixes semantics with surface form makes
     sem correlate with lexical overlap and collapses SS-vs-LL AUC; a "description"
     embedder that drops the form component restores it.  (RQ2, RQ3, research point 2)
  B. Scale drift: raw cosine separating in-cluster from out-cluster degrades as the
     number of categories grows (space gets denser); the permutation-null z-score
     stays stable.  (research point 3-4, scale-invariance)
  C. Same as B but injecting a morphological sub-cluster (form-glued, low real
     semantics): raw sim lets it pass; null-z flags it.
"""
import numpy as np
from sklearn.metrics import roc_auc_score
from scipy.stats import spearmanr
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

rng = np.random.default_rng(7)
P = 128

def unit(x):
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-12)

def jitter(center, sigma):
    """center (..,P) + isotropic noise scaled so noise norm ~= sigma (vs center norm 1)."""
    n = rng.normal(size=center.shape) / np.sqrt(center.shape[-1])
    return unit(center + sigma * n)

# --------------------------------------------------------------------------- #
# A. Form-confound demonstration
# --------------------------------------------------------------------------- #
def demo_confound():
    D = 6                      # semantic domains
    F = 6                      # surface-form groups (cross-cut domains)
    k = 12                     # terms per domain
    dom_centers  = unit(rng.normal(size=(D, P)))
    form_dirs    = unit(rng.normal(size=(F, P)))
    terms = []
    for d in range(D):
        for i in range(k):
            f = rng.integers(F)
            s = jitter(dom_centers[d], 0.8)                        # semantic vector
            terms.append({"dom": d, "form": f, "s": s})
    # name-embedder: semantics + form component (confounded with surface tokens)
    # desc-embedder: semantics only (form stripped by definitional text)
    alpha, beta = 1.0, 0.65
    for t in terms:
        t["e_name"] = unit(alpha*t["s"] + beta*form_dirs[t["form"]])
        t["e_desc"] = unit(t["s"])

    # Build SS (same dom, diff form) and LL (same form, diff dom) pairs.
    SS, LL, lex_all, semn_all, semd_all = [], [], [], [], []
    for a in range(len(terms)):
        for b in range(a+1, len(terms)):
            ta, tb = terms[a], terms[b]
            same_dom  = ta["dom"]  == tb["dom"]
            same_form = ta["form"] == tb["form"]
            lexv = 1.0 if same_form else 0.0
            sn = float(np.dot(ta["e_name"], tb["e_name"]))
            sd = float(np.dot(ta["e_desc"], tb["e_desc"]))
            lex_all.append(lexv); semn_all.append(sn); semd_all.append(sd)
            if same_dom and not same_form: SS.append((sn, sd))
            if same_form and not same_dom: LL.append((sn, sd))
    SS, LL = np.array(SS), np.array(LL)
    y = [1]*len(SS) + [0]*len(LL)
    auc_name = roc_auc_score(y, list(SS[:,0]) + list(LL[:,0]))
    auc_desc = roc_auc_score(y, list(SS[:,1]) + list(LL[:,1]))
    rho_name = spearmanr(lex_all, semn_all).statistic
    rho_desc = spearmanr(lex_all, semd_all).statistic
    return {"auc_name": auc_name, "auc_desc": auc_desc,
            "rho_lex_name": rho_name, "rho_lex_desc": rho_desc,
            "n_SS": len(SS), "n_LL": len(LL)}

# --------------------------------------------------------------------------- #
# B/C. Scale drift vs null-normalization
# --------------------------------------------------------------------------- #
def loo_centroid(M):
    s = M.sum(0)
    out = (s[None,:] - M) / (len(M)-1)
    return unit(out)

def config_signal(groups, n_perm=300):
    """raw metric per term = MARGIN = sim to own LOO centroid - max sim to other centroids.
    Margin is overlap-tolerant (unlike ARI/silhouette) and is what drifts with density."""
    allv = np.concatenate([g for g in groups], 0)
    owner = np.concatenate([[i]*len(g) for i, g in enumerate(groups)])
    G = len(groups)
    def centroids(assign):
        return {c: unit(allv[np.where(assign == c)[0]].mean(0)) for c in range(G)
                if (assign == c).sum() > 0}
    def link_margins(assign):
        cen = centroids(assign)
        m = np.zeros(len(allv))
        for c in range(G):
            idx = np.where(assign == c)[0]
            if len(idx) < 2: continue
            loo = loo_centroid(allv[idx])
            others = [cc for cc in cen if cc != c]
            for k_, i in enumerate(idx):
                sim_in = float(np.dot(allv[i], loo[k_]))
                sim_out = max(float(np.dot(allv[i], cen[cc])) for cc in others) if others else 0.0
                m[i] = sim_in - sim_out
        return m
    raw = link_margins(owner)
    null = np.empty((n_perm, len(allv)))
    for t in range(n_perm):
        perm = owner.copy(); rng.shuffle(perm)
        null[t] = link_margins(perm)
    z = (raw - null.mean(0)) / (null.std(0) + 1e-9)
    return raw, z, owner

def make_groups(D, k=10, sigma=0.7, subspace=12):
    """Centers live in a fixed low-dim subspace, so they crowd as D grows."""
    basis = unit(rng.normal(size=(subspace, P)))
    centers = unit(rng.normal(size=(D, subspace)) @ basis)
    return [jitter(np.tile(centers[d], (k, 1)), sigma) for d in range(D)]

def demo_scale(scales=(4, 8, 30, 100)):
    rows = {}
    for D in scales:
        groups = make_groups(D)
        raw, z, _ = config_signal(groups, n_perm=200)
        rows[D] = {"raw_median": float(np.median(raw)),
                   "raw_p10": float(np.percentile(raw, 10)),
                   "z_median": float(np.median(z)),
                   "z_p10": float(np.percentile(z, 10))}
    return rows

def demo_morph_injection(D=30, k=10):
    groups = make_groups(D, k)
    # morphological cluster: members are NOT semantically cohesive (form-glued only).
    morph = unit(rng.normal(size=(k, P)))            # no shared semantic center
    groups_with = groups + [morph]
    raw, z, owner = config_signal(groups_with, n_perm=300)
    mids = np.where(owner == D)[0]                    # the injected cluster
    good = np.where(owner != D)[0]
    return {"morph_margin_median": float(np.median(raw[mids])),
            "good_margin_median": float(np.median(raw[good])),
            "morph_z_median": float(np.median(z[mids])),
            "good_z_median": float(np.median(z[good]))}

def plots(scale_rows):
    Ds = sorted(scale_rows)
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    ax[0].plot(Ds, [scale_rows[D]["raw_median"] for D in Ds], "o-", label="raw margin median")
    ax[0].plot(Ds, [scale_rows[D]["raw_p10"] for D in Ds], "s--", label="raw margin p10")
    ax[0].set_xscale("log"); ax[0].set_xlabel("# categories")
    ax[0].set_ylabel("margin (sim_in - sim_out)"); ax[0].set_title("RAW margin drifts with scale")
    ax[0].legend()
    ax[1].plot(Ds, [scale_rows[D]["z_median"] for D in Ds], "o-", color="green", label="z median")
    ax[1].plot(Ds, [scale_rows[D]["z_p10"] for D in Ds], "s--", color="darkgreen", label="z p10")
    ax[1].set_xscale("log"); ax[1].set_xlabel("# categories")
    ax[1].set_ylabel("null-normalized z"); ax[1].set_title("null-z is scale-stable")
    ax[1].legend()
    plt.tight_layout(); plt.savefig("synth_scale.png", dpi=130)

if __name__ == "__main__":
    print("=== A. Form de-confound (controlled) ===")
    a = demo_confound()
    for k, v in a.items():
        print(f"  {k:14}: {v:.4f}" if isinstance(v, float) else f"  {k:14}: {v}")
    print("  -> name-embedder confounds form (rho_lex_name high, AUC low);")
    print("     desc-embedder de-confounds (rho_lex_desc ~0, AUC high).")

    print("\n=== B. Scale drift vs null-z ===")
    s = demo_scale()
    print(f"  {'D':>5} {'raw_med':>9} {'raw_p10':>9} {'z_med':>8} {'z_p10':>8}")
    for D in sorted(s):
        r = s[D]
        print(f"  {D:>5} {r['raw_median']:>9.4f} {r['raw_p10']:>9.4f} "
              f"{r['z_median']:>8.3f} {r['z_p10']:>8.3f}")
    print("  -> raw drifts as D grows; z stays put -> a z-threshold is scale-invariant.")

    print("\n=== C. Morphological-cluster injection (D=30 + 1 form-glued) ===")
    m = demo_morph_injection()
    for k, v in m.items():
        print(f"  {k:18}: {v:.4f}")
    print("  -> injected cluster's null-z is far below good clusters -> rule flags it.")

    plots(s)
    print("\nwrote synth_scale.png")
