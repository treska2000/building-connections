#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
morph_leak_research.py
======================
Reproducible parameter tuning for the deterministic string metric `morph_leak_rate`.

Question the metric answers, per (term, category) link:
    "Is the category GUESSABLE from the morphology / roots of the term names,
     i.e. recoverable from the FORM of the name without knowing its MEANING?"

This is the *form* axis. It is the complement of the *semantic* axis (an embedder).
It must FIRE on shared specific roots/suffixes ("...Theory", "optim-/optimiz-")
and must NOT fire on ubiquitous shared words ("Model", "Learning", "Network").

The file is self-contained:
  * builds a labelled corpus (5 domains, leak / clean / trap / semantic / adversarial),
  * implements the metric with every knob (L, S, w/IDF, M, variant, tau, null-z),
  * runs grid-search + ablation + FP-analysis + scale-test + adversarial-test + sensitivity,
  * writes plots (PNG) and a machine-readable results dump (JSON),
  * prints every number used in the report.

Run:  python3 morph_leak_research.py
Deps: numpy, scikit-learn, matplotlib, nltk (PorterStemmer/SnowballStemmer).
      nltk stemmers are pure-python and need NO downloaded data.
"""

import os, re, json, math, random, itertools, statistics
from collections import defaultdict, Counter
import numpy as np

try:
    from nltk.stem import PorterStemmer, SnowballStemmer
    _PORTER = PorterStemmer()
    _SNOW = SnowballStemmer("english")
    _HAVE_NLTK = True
except Exception:
    _HAVE_NLTK = False

from sklearn.metrics import roc_auc_score, precision_recall_fscore_support, precision_recall_curve

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SEED = 20260608
random.seed(SEED)
np.random.seed(SEED)

OUTDIR = os.path.dirname(os.path.abspath(__file__))
PLOTDIR = os.path.join(OUTDIR, "morph_leak_plots")
os.makedirs(PLOTDIR, exist_ok=True)

# ============================================================================
# 1. LABELLED CORPUS
# ============================================================================
# A "category" = {name, members:[term names], domain, label in {leak,clean}, subtype}
# Link-level label = the category label propagated to each (term, category) link,
# except mixed categories where only a labelled subset of members is a leak.
#
# Subtypes:
#   leak_surface  : members share an IDENTICAL specific word  (word-level catches it)
#   leak_morph    : members share a specific ROOT in different surface forms
#                   (needs stemming/prefix; word-level MISSES it)
#   leak_suffix   : members share a specific derivational suffix (-ation, -ase, -ese)
#   trap_ubiq     : members share only a UBIQUITOUS word (model/learning/...) -> NOT a leak
#   semantic      : members are semantically related but share NO surface token -> NOT a leak
#   adv_syn_name  : a leak category whose NAME was synonym-swapped (form hidden in the name
#                   only; members still rhyme) -> term-term must still catch; term-name won't
#   adv_syn_memb  : a leak category whose shared MEMBER word was replaced by synonyms so the
#                   form is genuinely gone, only meaning remains -> metric SHOULD return clean
#                   (that is the semantic axis's job, not the form axis's)

UBIQUITOUS = ["Model", "Models", "Learning", "Network", "Networks", "Based",
              "Method", "Methods", "Analysis", "System", "Systems", "Approach", "Function"]

# ---- Hand-authored ANCHOR categories (high realism) ------------------------
ANCHORS = [
    # ---------------- AI safety ----------------
    dict(domain="ai_safety", label="leak", subtype="leak_surface", name="Decision Theories",
         members=["Causal Decision Theory", "Evidential Decision Theory",
                  "Functional Decision Theory", "Updateless Decision Theory"]),
    dict(domain="ai_safety", label="leak", subtype="leak_morph", name="Optimisation Pathologies",
         members=["Mesa-Optimization", "Inner Optimizer", "Optimized Proxy Objective",
                  "Over-Optimizing the Reward"]),
    dict(domain="ai_safety", label="leak", subtype="leak_surface", name="Reward Mechanisms",
         members=["Reward Hacking", "Reward Tampering", "Reward Modeling", "Reward Shaping"]),
    dict(domain="ai_safety", label="clean", subtype="semantic", name="Best-Laid Plans",
         members=["Reward Hacking", "Goal Misgeneralization", "Specification Gaming", "Wireheading"]),
    dict(domain="ai_safety", label="clean", subtype="semantic", name="Ghosts in the Machine",
         members=["Mesa-Optimization", "Deceptive Alignment", "Gradient Hacking", "Scheming"]),
    dict(domain="ai_safety", label="clean", subtype="trap_ubiq", name="Two-Faced Models",
         members=["Sleeper Model", "Backdoored Model", "Password-Locked Model", "Sandbagging Model"]),
    dict(domain="ai_safety", label="clean", subtype="semantic", name="Opening the Hood",
         members=["Sparse Autoencoders", "Superposition", "Activation Patching", "Linear Probing"]),
    dict(domain="ai_safety", label="leak", subtype="leak_suffix", name="Alignment Failures (-ing)",
         members=["Scheming", "Sandbagging", "Wireheading", "Sycophancy Faking"]),

    # ---------------- LLM reasoning ----------------
    dict(domain="llm_reasoning", label="leak", subtype="leak_surface", name="Chains of Thought",
         members=["Chain-of-Thought", "Tree-of-Thought", "Graph-of-Thought", "Skeleton-of-Thought"]),
    dict(domain="llm_reasoning", label="leak", subtype="leak_surface", name="Reward Models",
         members=["Process Reward Models", "Outcome Reward Models",
                  "Generative Reward Models", "Verifier Reward Models"]),
    dict(domain="llm_reasoning", label="clean", subtype="trap_ubiq", name="Prompting Families",
         members=["Few-Shot Learning", "In-Context Learning", "Curriculum Learning", "Active Learning"]),
    dict(domain="llm_reasoning", label="clean", subtype="semantic", name="Getting It Right",
         members=["Self-Consistency", "Majority Voting", "Best-of-N Sampling", "Verifier Reranking"]),
    dict(domain="llm_reasoning", label="leak", subtype="leak_morph", name="Decoding Verbs",
         members=["Speculative Decoding", "Decoded Beam Search", "Constrained Decoder",
                  "Contrastive Decoding"]),
    dict(domain="llm_reasoning", label="clean", subtype="trap_ubiq", name="Search Methods",
         members=["Monte Carlo Tree Search Method", "Beam Search Method",
                  "Greedy Search Method", "A-Star Search Method"]),

    # ---------------- Chemistry ----------------
    dict(domain="chemistry", label="leak", subtype="leak_suffix", name="Hydrocarbon Series (-ane)",
         members=["Methane", "Ethane", "Propane", "Butane"]),
    dict(domain="chemistry", label="leak", subtype="leak_suffix", name="Sugars (-ose)",
         members=["Glucose", "Fructose", "Sucrose", "Lactose"]),
    dict(domain="chemistry", label="clean", subtype="semantic", name="Strong Acids",
         members=["Hydrochloric Acid", "Sulfuric Acid", "Nitric Acid", "Perchloric Acid"]),
    dict(domain="chemistry", label="leak", subtype="leak_surface", name="Acids (word)",
         members=["Acetic Acid", "Citric Acid", "Formic Acid", "Lactic Acid"]),
    dict(domain="chemistry", label="clean", subtype="semantic", name="Noble Gases",
         members=["Helium", "Neon", "Argon", "Krypton"]),
    dict(domain="chemistry", label="leak", subtype="leak_suffix", name="Enzymes (-ase)",
         members=["Lactase", "Protease", "Lipase", "Amylase"]),

    # ---------------- Linguistics ----------------
    dict(domain="linguistics", label="leak", subtype="leak_suffix", name="Phonology Terms (-eme)",
         members=["Phoneme", "Morpheme", "Grapheme", "Lexeme"]),
    dict(domain="linguistics", label="leak", subtype="leak_surface", name="Grammar Cases",
         members=["Nominative Case", "Accusative Case", "Dative Case", "Genitive Case"]),
    dict(domain="linguistics", label="clean", subtype="semantic", name="Ways Words Change",
         members=["Metaphor", "Semantic Bleaching", "Grammaticalization", "Reanalysis"]),
    dict(domain="linguistics", label="leak", subtype="leak_morph", name="Inflection Processes",
         members=["Inflectional Affix", "Inflecting Stems", "Inflected Forms", "Over-Inflection"]),
    dict(domain="linguistics", label="clean", subtype="trap_ubiq", name="Analysis Levels",
         members=["Phonological Analysis", "Syntactic Analysis", "Discourse Analysis", "Pragmatic Analysis"]),

    # ---------------- Molecular biology ----------------
    dict(domain="biology", label="leak", subtype="leak_suffix", name="Cell Processes (-osis)",
         members=["Mitosis", "Meiosis", "Apoptosis", "Endocytosis"]),
    dict(domain="biology", label="leak", subtype="leak_morph", name="Transcription Verbs",
         members=["Transcription Factor", "Transcribed Strand", "Transcribing Polymerase",
                  "Co-Transcriptional Splicing"]),
    dict(domain="biology", label="clean", subtype="semantic", name="The Central Dogma",
         members=["Replication", "Transcription", "Translation", "Reverse Transcription"]),
    dict(domain="biology", label="clean", subtype="trap_ubiq", name="Regulatory Systems",
         members=["Immune System", "Endocrine System", "Nervous System", "Lymphatic System"]),
    dict(domain="biology", label="leak", subtype="leak_surface", name="RNA Types",
         members=["Messenger RNA", "Transfer RNA", "Ribosomal RNA", "Micro RNA"]),
]

# ---- Programmatic GENERATORS to reach >=150 categories ----------------------
# Specific (high-IDF) leak tokens, by domain -- each used by exactly one category.
LEAK_SURFACE_TOKENS = {
    "ai_safety": ["Oversight", "Probe", "Benchmark", "Guardrail", "Tripwire", "Sandbox",
                  "Eval", "Audit", "Watermark", "Canary"],
    "llm_reasoning": ["Verifier", "Rollout", "Critique", "Scratchpad", "Rationale",
                      "Heuristic", "Planner", "Reranker", "Sampler", "Aggregator"],
    "chemistry": ["Isomer", "Catalyst", "Ligand", "Solvent", "Buffer", "Reagent",
                  "Electrolyte", "Polymer", "Hydride", "Oxide"],
    "linguistics": ["Diphthong", "Allophone", "Clitic", "Determiner", "Quantifier",
                    "Complementizer", "Gloss", "Corpus", "Treebank", "Lexicon"],
    "biology": ["Operon", "Plasmid", "Ribozyme", "Chaperone", "Telomere", "Centromere",
                "Histone", "Vesicle", "Organelle", "Cofactor"],
}
# Modifiers to build distinct member names.
MODIFIERS = ["Adaptive", "Hierarchical", "Global", "Local", "Latent", "Dynamic",
             "Sparse", "Dense", "Robust", "Minimal", "Canonical", "Hybrid",
             "Implicit", "Explicit", "Nested", "Distributed", "Discrete", "Continuous"]
# Morphological roots: surface variants share a stem only.
MORPH_ROOTS = {
    "ai_safety": [("Generaliz", ["Generalization Gap", "Generalizing Policy", "Generalized Reward", "Mis-Generalization"]),
                  ("Calibrat", ["Calibration Error", "Calibrated Confidence", "Re-Calibrating Logits", "Mis-Calibration"]),
                  ("Quantiz", ["Quantization Noise", "Quantized Weights", "Quantizing Activations", "Post-Quantization Drift"])],
    "llm_reasoning": [("Verif", ["Verification Step", "Verified Trace", "Verifying Subgoals", "Self-Verification"]),
                      ("Retriev", ["Retrieval Context", "Retrieved Passage", "Retrieving Evidence", "Re-Retrieval Loop"]),
                      ("Distil", ["Distillation Target", "Distilled Rationale", "Distilling Chains", "Self-Distillation"])],
    "chemistry": [("Oxid", ["Oxidation State", "Oxidized Surface", "Oxidizing Agent", "Auto-Oxidation"]),
                  ("Hydrol", ["Hydrolysis Rate", "Hydrolyzed Bond", "Hydrolyzing Enzyme", "Acid Hydrolysis"]),
                  ("Crystalliz", ["Crystallization Front", "Crystallized Phase", "Crystallizing Melt", "Re-Crystallization"])],
    "linguistics": [("Nominaliz", ["Nominalization Suffix", "Nominalized Verb", "Nominalizing Morphology", "De-Nominalization"]),
                    ("Palataliz", ["Palatalization Rule", "Palatalized Consonant", "Palatalizing Context", "De-Palatalization"]),
                    ("Lexicaliz", ["Lexicalization Path", "Lexicalized Idiom", "Lexicalizing Phrase", "Re-Lexicalization"])],
    "biology": [("Methyl", ["Methylation Mark", "Methylated Promoter", "Methylating Enzyme", "De-Methylation"]),
                ("Phosphoryl", ["Phosphorylation Site", "Phosphorylated Residue", "Phosphorylating Kinase", "De-Phosphorylation"]),
                ("Transcrib", ["Transcription Bubble", "Transcribed Region", "Transcribing Complex", "Co-Transcription"])],
}
# Suffix-leak families (specific shared suffix).
SUFFIX_FAMILIES = {
    "chemistry": [("-ene", ["Ethene", "Propene", "Butene", "Styrene"]),
                  ("-ol", ["Methanol", "Ethanol", "Phenol", "Glycerol"]),
                  ("-ide", ["Chloride", "Bromide", "Sulfide", "Cyanide"])],
    "biology": [("-cyte", ["Lymphocyte", "Erythrocyte", "Leukocyte", "Phagocyte"]),
                ("-plasm", ["Cytoplasm", "Nucleoplasm", "Protoplasm", "Ectoplasm"]),
                ("-some", ["Ribosome", "Lysosome", "Chromosome", "Proteasome"])],
    "linguistics": [("-eme2", ["Toneme", "Sememe", "Tagmeme", "Chereme"]),
                    ("-ative", ["Ablative", "Locative", "Vocative", "Causative"])],
    "ai_safety": [("-ity", ["Corrigibility", "Interpretability", "Controllability", "Observability"])],
    "llm_reasoning": [("-ization", ["Tokenization", "Normalization", "Regularization", "Quantization"])],
}
# Semantic-clean families (related meaning, no shared surface token).
SEMANTIC_FAMILIES = {
    "ai_safety": [["Tripwire", "Honeypot", "Canary", "Dead-Man Switch"],
                  ["Corrigibility", "Off-Switch", "Shutdownability", "Interruptibility"],
                  ["Red Teaming", "Adversarial Probing", "Stress Testing", "Jailbreak Hunting"]],
    "llm_reasoning": [["Backtracking", "Reflection", "Self-Correction", "Revision"],
                      ["Scratchpad", "Working Memory", "Notepad", "Blackboard"],
                      ["Planner", "Decomposer", "Orchestrator", "Router"]],
    "chemistry": [["Distillation", "Filtration", "Chromatography", "Centrifugation"],
                  ["Exothermic", "Endothermic", "Spontaneous", "Reversible"],
                  ["Anode", "Cathode", "Electrolyte", "Salt Bridge"]],
    "linguistics": [["Assimilation", "Elision", "Epenthesis", "Metathesis"],
                    ["Subject", "Predicate", "Object", "Adjunct"],
                    ["Pidgin", "Creole", "Dialect", "Register"]],
    "biology": [["Mitochondrion", "Nucleus", "Ribosome", "Golgi Apparatus"],
                ["Helicase", "Primase", "Ligase", "Polymerase"],
                ["Codon", "Anticodon", "Promoter", "Enhancer"]],
}


def _trap_category(domain, idx):
    """Members share ONE ubiquitous word; distinct specific modifiers otherwise. NOT a leak."""
    ubiq = random.choice(UBIQUITOUS)
    mods = random.sample(MODIFIERS, 4)
    members = [f"{m} {ubiq}" for m in mods]
    return dict(domain=domain, label="clean", subtype="trap_ubiq",
                name=f"{ubiq} Family {idx}", members=members)


def _leak_surface_category(domain, token, idx):
    mods = random.sample(MODIFIERS, 4)
    members = [f"{m} {token}" for m in mods]
    return dict(domain=domain, label="leak", subtype="leak_surface",
                name=f"{token}s {idx}", members=members)


def build_corpus():
    cats = [dict(c) for c in ANCHORS]
    # surface-leak categories
    for dom, toks in LEAK_SURFACE_TOKENS.items():
        for i, t in enumerate(toks):
            cats.append(_leak_surface_category(dom, t, i))
    # morph-leak categories
    for dom, fams in MORPH_ROOTS.items():
        for root, members in fams:
            cats.append(dict(domain=dom, label="leak", subtype="leak_morph",
                             name=f"{root}* processes", members=list(members)))
    # suffix-leak categories
    for dom, fams in SUFFIX_FAMILIES.items():
        for suf, members in fams:
            cats.append(dict(domain=dom, label="leak", subtype="leak_suffix",
                             name=f"{suf} family", members=list(members)))
    # semantic-clean categories
    for dom, fams in SEMANTIC_FAMILIES.items():
        for members in fams:
            cats.append(dict(domain=dom, label="clean", subtype="semantic",
                             name="related set", members=list(members)))
    # trap categories (inject ubiquitous words widely so their IDF is genuinely low)
    domains = list(LEAK_SURFACE_TOKENS.keys())
    for i in range(40):
        cats.append(_trap_category(random.choice(domains), i))
    # sprinkle ubiquitous words into some leak/semantic members too (realistic noise,
    # further lowers IDF of ubiquitous words)
    for c in cats:
        if c["subtype"] in ("leak_surface", "semantic") and random.random() < 0.25:
            j = random.randrange(len(c["members"]))
            c["members"][j] = c["members"][j] + " " + random.choice(["Model", "Method", "System"])
    # tag link labels: leak categories -> all member links leak; clean -> all 0
    for c in cats:
        c["link_labels"] = [1 if c["label"] == "leak" else 0 for _ in c["members"]]
    return cats


def make_adversarial(corpus):
    """Build adversarial probes from existing leak categories."""
    adv = []
    leaks = [c for c in corpus if c["label"] == "leak" and c["subtype"] == "leak_surface"]
    for c in leaks[:12]:
        # adv_syn_name: keep members (they still rhyme) but rename category to a synonym/metaphor.
        d = dict(domain=c["domain"], label="leak", subtype="adv_syn_name",
                 name="Metaphorical Title", members=list(c["members"]),
                 link_labels=[1] * len(c["members"]))
        adv.append(d)
    # adv_syn_memb: replace the shared word by synonyms -> form gone, meaning stays -> SHOULD be clean
    syn_map = {
        "Theory": ["Framework", "Account", "Doctrine", "Calculus"],
        "Acid": ["Acid", "Proton Donor", "Hydronium Source", "Low-pH Compound"],
        "Reward": ["Reward", "Payoff", "Return", "Incentive"],
        "Thought": ["Thought", "Reasoning", "Deliberation", "Cogitation"],
    }
    # build a couple of explicit synonym-scrambled versions of canonical leaks
    adv.append(dict(domain="ai_safety", label="clean", subtype="adv_syn_memb",
                    name="Decision X (scrambled)",
                    members=["Causal Decision Theory", "Evidential Choice Framework",
                             "Functional Action Doctrine", "Updateless Policy Account"],
                    link_labels=[0, 0, 0, 0]))
    adv.append(dict(domain="llm_reasoning", label="clean", subtype="adv_syn_memb",
                    name="Reasoning shapes (scrambled)",
                    members=["Chain of Thought", "Tree of Reasoning",
                             "Graph of Deliberation", "Skeleton of Cogitation"],
                    link_labels=[0, 0, 0, 0]))
    return adv


# ============================================================================
# 2. METRIC WITH ALL KNOBS
# ============================================================================
_FUNCTION_WORDS = {"the", "a", "an", "of", "for", "and", "to", "in", "on", "via",
                   "with", "by", "as", "at", "or", "from"}
_UBIQ_LOWER = {w.lower() for w in UBIQUITOUS}


from functools import lru_cache


def _base_transform(tok, S):
    if S == "none":
        return tok
    if S.startswith("prefix"):
        return tok[:int(S[6:])]
    if S.startswith("suffix"):
        k = int(S[6:])
        return ("~" + tok[-k:]) if len(tok) > k else ("~" + tok)
    if S == "porter" and _HAVE_NLTK:
        return _PORTER.stem(tok)
    if S == "snowball" and _HAVE_NLTK:
        return _SNOW.stem(tok)
    return tok


@lru_cache(maxsize=400000)
def _tok_cached(name, L, S, dub):
    s = name.lower()
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if len(t) >= L and t not in _FUNCTION_WORDS]
    if dub:
        toks = [t for t in toks if t not in _UBIQ_LOWER]
    out = []
    if S.endswith("_suf3"):          # combined: emit stem AND a marked 3-char suffix
        base = S[:-5]
        for t in toks:
            out.append(_base_transform(t, base))
            if len(t) > 3:
                out.append("~" + t[-3:])
    else:
        for t in toks:
            out.append(_base_transform(t, S))
    return tuple(out)


def transform_token(tok, S):  # kept for API compatibility
    return _base_transform(tok, S)


def tokenize(name, L=4, S="none", drop_ubiq_stop=False):
    return list(_tok_cached(name, L, S, drop_ubiq_stop))


def build_idf(corpus, L, S, drop_ubiq_stop, level="term"):
    """IDF over the config. level='term' -> df = #term-names containing token."""
    df = Counter()
    N = 0
    for c in corpus:
        if level == "term":
            for m in c["members"]:
                N += 1
                for t in set(tokenize(m, L, S, drop_ubiq_stop)):
                    df[t] += 1
        else:  # category-level df
            N += 1
            toks = set()
            for m in c["members"]:
                toks |= set(tokenize(m, L, S, drop_ubiq_stop))
            for t in toks:
                df[t] += 1
    idf = {t: math.log((N + 1) / (d + 1)) + 1.0 for t, d in df.items()}
    idf_max = max(idf.values()) if idf else 1.0
    return idf, idf_max


def token_weight(tok, w, idf, idf_max, idf_cutoff):
    if w == "uniform":
        return 1.0
    v = idf.get(tok, math.log(2.0)) / idf_max  # normalized to ~[0,1]
    if v < idf_cutoff:
        return 0.0
    return v


def _levclose(a, b, maxd=1):
    if a == b:
        return True
    if abs(len(a) - len(b)) > maxd:
        return False
    # cheap bounded Levenshtein
    la, lb = len(a), len(b)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = dp[0]; dp[0] = i; best = dp[0]
        for j in range(1, lb + 1):
            cur = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur; best = min(best, dp[j])
        if best > maxd:
            return False
    return dp[lb] <= maxd


def leak_score_link(term_idx, members, name, params, idf, idf_max,
                    variant, min_other=1):
    """Score one (term, category) link in [0,1]-ish. Higher = more form-leak."""
    L, S, w, M, cutoff, dub = (params["L"], params["S"], params["w"],
                               params["M"], params["idf_cutoff"], params["drop_ubiq_stop"])
    A = tokenize(members[term_idx], L, S, dub)
    if not A:
        return 0.0
    if variant == "term_name":
        other_counts = Counter(set(tokenize(name, L, S, dub)))
        n_other = 1
    else:  # term_term: count how many OTHER members contain each token
        other_counts = Counter()
        for j, m in enumerate(members):
            if j == term_idx:
                continue
            for t in set(tokenize(m, L, S, dub)):
                other_counts[t] += 1
        n_other = max(1, len(members) - 1)

    def wt(t):
        return token_weight(t, w, idf, idf_max, cutoff)

    if M == "fuzzy":
        # token in A counts as shared if a near-variant appears in >=min_other others
        shared = []
        for t in set(A):
            cnt = sum(c for tok, c in other_counts.items() if _levclose(t, tok, 1))
            if cnt >= min_other:
                shared.append(t)
        return max([wt(t) for t in shared], default=0.0)

    shared = [t for t in set(A) if other_counts.get(t, 0) >= min_other]
    if M == "maxweight":
        return max([wt(t) for t in shared], default=0.0)
    if M == "wjaccard":
        denom = sum(wt(t) for t in set(A))
        return (sum(wt(t) for t in shared) / denom) if denom > 0 else 0.0
    if M == "coverage":
        if not shared:
            return 0.0
        best = max(shared, key=lambda t: (wt(t), other_counts[t]))
        return (other_counts[best] / n_other) * (1.0 if wt(best) > 0 else 0.0)
    raise ValueError(M)


def score_corpus(corpus, params, variant, idf=None, idf_max=None, idf_level="term"):
    if idf is None:
        idf, idf_max = build_idf(corpus, params["L"], params["S"],
                                 params["drop_ubiq_stop"], idf_level)
    scores, labels, meta = [], [], []
    for c in corpus:
        for i, m in enumerate(c["members"]):
            s = leak_score_link(i, c["members"], c["name"], params, idf, idf_max, variant)
            scores.append(s)
            labels.append(c["link_labels"][i])
            meta.append((c["domain"], c["subtype"], m, c["name"]))
    return np.array(scores), np.array(labels), meta, idf, idf_max


def null_z_scores(corpus, params, variant, R=80, idf_level="term"):
    """Null-normalised z: permute term->category membership (sizes fixed), recompute,
    z = (obs - null_mean)/null_std. Scale-invariant."""
    obs, labels, meta, idf, idf_max = score_corpus(corpus, params, variant, idf_level=idf_level)
    sizes = [len(c["members"]) for c in corpus]
    all_terms = [m for c in corpus for m in c["members"]]
    names = [c["name"] for c in corpus]
    null = np.zeros((R, len(obs)))
    rng = random.Random(SEED)
    for r in range(R):
        perm = all_terms[:]
        rng.shuffle(perm)
        shuffled = []
        k = 0
        for sz, nm in zip(sizes, names):
            shuffled.append(dict(name=nm, members=perm[k:k + sz],
                                 link_labels=[0] * sz, domain="x", subtype="x", label="clean"))
            k += sz
        s, _, _, _, _ = score_corpus(shuffled, params, variant, idf_level=idf_level)
        null[r] = s
    mu = null.mean(0); sd = null.std(0) + 1e-9
    z = (obs - mu) / sd
    return z, obs, labels, meta


# ============================================================================
# 3. EVALUATION HELPERS
# ============================================================================
def best_threshold(scores, labels):
    """Pick tau maximising F1 on these (train) scores."""
    cand = sorted(set(scores.tolist()))
    best = (-1, 0.5)
    for t in cand:
        pred = (scores >= t).astype(int)
        p, r, f, _ = precision_recall_fscore_support(labels, pred, average="binary",
                                                      zero_division=0)
        if f > best[0]:
            best = (f, t)
    return best[1]


def eval_at(scores, labels, tau):
    pred = (scores >= tau).astype(int)
    p, r, f, _ = precision_recall_fscore_support(labels, pred, average="binary",
                                                  zero_division=0)
    try:
        auc = roc_auc_score(labels, scores) if len(set(labels)) > 1 else float("nan")
    except Exception:
        auc = float("nan")
    return dict(precision=p, recall=r, f1=f, auc=auc, tau=tau)


def split_corpus(corpus, frac=0.5):
    """Stratified split by (domain, label)."""
    by = defaultdict(list)
    for c in corpus:
        by[(c["domain"], c["label"])].append(c)
    train, test = [], []
    rng = random.Random(SEED)
    for k, lst in by.items():
        lst = lst[:]; rng.shuffle(lst)
        n = max(1, int(len(lst) * frac))
        train += lst[:n]; test += lst[n:]
    return train, test


# ============================================================================
# 4. EXPERIMENTS
# ============================================================================
GRID = dict(
    L=[3, 4, 5],
    S=["none", "prefix4", "prefix5", "porter", "snowball",
       "suffix3", "porter_suf3", "snowball_suf3"],
    w=["uniform", "idf"],
    idf_cutoff=[0.0, 0.2],
    M=["maxweight", "wjaccard", "coverage", "fuzzy"],
    drop_ubiq_stop=[False],
    variant=["term_term", "term_name"],
)


def iter_grid():
    keys = ["L", "S", "w", "idf_cutoff", "M", "drop_ubiq_stop", "variant"]
    for combo in itertools.product(*[GRID[k] for k in keys]):
        d = dict(zip(keys, combo))
        if d["w"] == "uniform" and d["idf_cutoff"] > 0:
            continue  # cutoff only meaningful with idf
        yield d


def run_grid(train, test):
    rows = []
    for d in iter_grid():
        params = dict(L=d["L"], S=d["S"], w=d["w"], M=d["M"],
                      idf_cutoff=d["idf_cutoff"], drop_ubiq_stop=d["drop_ubiq_stop"])
        # IDF must be fit on the *same* config we score (config-relative); fit on train+test
        # corpus statistics independently for train and test (transductive per config).
        s_tr, y_tr, _, _, _ = score_corpus(train, params, d["variant"])
        s_te, y_te, meta_te, _, _ = score_corpus(test, params, d["variant"])
        tau = best_threshold(s_tr, y_tr)
        m = eval_at(s_te, y_te, tau)
        rows.append(dict(**d, **m))
    rows.sort(key=lambda r: (r["f1"], r["auc"] if not math.isnan(r["auc"]) else 0), reverse=True)
    return rows


def fp_analysis(corpus, variant):
    """FP rate on ubiquitous-word traps, uniform vs idf (everything else best)."""
    out = {}
    for w, cutoff in [("uniform", 0.0), ("idf", 0.2)]:
        params = dict(L=4, S="porter", w=w, M="maxweight", idf_cutoff=cutoff, drop_ubiq_stop=False)
        s, y, meta, _, _ = score_corpus(corpus, params, variant)
        tau = best_threshold(s, y)
        # FP among trap_ubiq links
        trap_idx = [i for i, mt in enumerate(meta) if mt[1] == "trap_ubiq"]
        fp = sum(1 for i in trap_idx if s[i] >= tau)
        out[w] = dict(tau=tau, trap_links=len(trap_idx), trap_fired=fp,
                      trap_fp_rate=fp / max(1, len(trap_idx)),
                      overall_f1=eval_at(s, y, tau)["f1"])
    return out


def scale_test(best_params, variant, sizes=(4, 8, 30, 100), reps=8, R=30):
    """Build synthetic configs of N categories with a fixed leak prevalence and an
    injected morph sub-cluster as negative; report raw-tau F1 vs null-z F1 across N."""
    res = {"raw": defaultdict(list), "z": defaultdict(list), "tau_raw": defaultdict(list),
           "tau_z": defaultdict(list)}
    pool = build_corpus()
    leaks = [c for c in pool if c["label"] == "leak"]
    cleans = [c for c in pool if c["label"] == "clean"]
    for N in sizes:
        for rep in range(reps):
            rng = random.Random(SEED + 1000 * N + rep)
            nl = max(1, N // 2)
            cfg = [dict(rng.choice(leaks)) for _ in range(nl)] + \
                  [dict(rng.choice(cleans)) for _ in range(N - nl)]
            for c in cfg:
                c["link_labels"] = [1 if c["label"] == "leak" else 0 for _ in c["members"]]
            # raw score
            s, y, _, _, _ = score_corpus(cfg, best_params, variant)
            tau = best_threshold(s, y)
            res["raw"][N].append(eval_at(s, y, tau)["f1"]); res["tau_raw"][N].append(tau)
            # null-z score
            z, _, yz, _ = null_z_scores(cfg, best_params, variant, R=R)
            tz = best_threshold(z, np.array(yz))
            res["z"][N].append(eval_at(z, np.array(yz), tz)["f1"]); res["tau_z"][N].append(tz)
    summ = {}
    for N in sizes:
        summ[N] = dict(
            raw_f1=float(np.mean(res["raw"][N])), raw_f1_sd=float(np.std(res["raw"][N])),
            z_f1=float(np.mean(res["z"][N])), z_f1_sd=float(np.std(res["z"][N])),
            tau_raw=float(np.mean(res["tau_raw"][N])), tau_raw_sd=float(np.std(res["tau_raw"][N])),
            tau_z=float(np.mean(res["tau_z"][N])), tau_z_sd=float(np.std(res["tau_z"][N])),
        )
    return summ


def adversarial_test(best_params):
    corpus = build_corpus()
    adv = make_adversarial(corpus)
    idf_tt, m_tt = build_idf(corpus, best_params["L"], best_params["S"],
                             best_params["drop_ubiq_stop"], "term")
    out = []
    for c in adv:
        for variant in ("term_term", "term_name"):
            # score adversarial category embedded in real corpus statistics
            scs = []
            for i in range(len(c["members"])):
                s = leak_score_link(i, c["members"], c["name"], best_params,
                                    idf_tt, m_tt, variant)
                scs.append(s)
            out.append(dict(subtype=c["subtype"], variant=variant,
                            name=c["name"], members=c["members"],
                            scores=[round(x, 3) for x in scs],
                            mean=float(np.mean(scs)), gold=c["link_labels"][0]))
    return out


def sensitivity_L(train, test, variant, base):
    res = {}
    for L in [3, 4, 5, 6]:
        for S in ["none", "porter"]:
            params = dict(base); params["L"] = L; params["S"] = S
            s_tr, y_tr, _, _, _ = score_corpus(train, params, variant)
            s_te, y_te, _, _, _ = score_corpus(test, params, variant)
            tau = best_threshold(s_tr, y_tr)
            res[(L, S)] = eval_at(s_te, y_te, tau)["f1"]
    return res


def subtype_recall(corpus, best_params, variant):
    s, y, meta, _, _ = score_corpus(corpus, best_params, variant)
    tau = best_threshold(s, y)
    bys = defaultdict(lambda: [0, 0])
    for i, mt in enumerate(meta):
        st = mt[1]
        gold = y[i]
        pred = int(s[i] >= tau)
        if gold == 1:
            bys[st][1] += 1
            if pred == 1:
                bys[st][0] += 1
        else:
            # track false positives by subtype
            bys[st][0] += 0
    rec = {st: (hit / tot if tot else float("nan")) for st, (hit, tot) in bys.items() if tot}
    return rec, tau


# ============================================================================
# 5. PLOTS
# ============================================================================
def plot_ablation_S(rows):
    best_by_S = {}
    for r in rows:
        best_by_S.setdefault(r["S"], 0)
        best_by_S[r["S"]] = max(best_by_S[r["S"]], r["f1"])
    order = ["none", "prefix4", "prefix5", "porter", "snowball"]
    vals = [best_by_S.get(o, 0) for o in order]
    plt.figure(figsize=(6, 4))
    plt.bar(order, vals, color="#4C72B0")
    plt.ylabel("best held-out F1"); plt.title("Ablation: token transform S")
    plt.ylim(0, 1); plt.xticks(rotation=20)
    for i, v in enumerate(vals):
        plt.text(i, v + 0.01, f"{v:.3f}", ha="center", fontsize=8)
    plt.tight_layout(); plt.savefig(os.path.join(PLOTDIR, "ablation_S.png"), dpi=130); plt.close()


def plot_ablation_M(rows):
    best = {}
    for r in rows:
        best[r["M"]] = max(best.get(r["M"], 0), r["f1"])
    order = ["maxweight", "wjaccard", "coverage", "fuzzy"]
    vals = [best.get(o, 0) for o in order]
    plt.figure(figsize=(6, 4))
    plt.bar(order, vals, color="#55A868")
    plt.ylabel("best held-out F1"); plt.title("Ablation: intersection measure M")
    plt.ylim(0, 1)
    for i, v in enumerate(vals):
        plt.text(i, v + 0.01, f"{v:.3f}", ha="center", fontsize=8)
    plt.tight_layout(); plt.savefig(os.path.join(PLOTDIR, "ablation_M.png"), dpi=130); plt.close()


def plot_idf_fp(fp):
    ws = ["uniform", "idf"]
    fpr = [fp[w]["trap_fp_rate"] for w in ws]
    f1 = [fp[w]["overall_f1"] for w in ws]
    x = np.arange(2); width = 0.35
    plt.figure(figsize=(6, 4))
    plt.bar(x - width / 2, fpr, width, label="trap FP rate", color="#C44E52")
    plt.bar(x + width / 2, f1, width, label="overall F1", color="#4C72B0")
    plt.xticks(x, ["uniform w", "IDF w"]); plt.ylim(0, 1)
    plt.title("IDF kills ubiquitous-word false positives"); plt.legend()
    for i in range(2):
        plt.text(i - width / 2, fpr[i] + 0.01, f"{fpr[i]:.2f}", ha="center", fontsize=8)
        plt.text(i + width / 2, f1[i] + 0.01, f"{f1[i]:.2f}", ha="center", fontsize=8)
    plt.tight_layout(); plt.savefig(os.path.join(PLOTDIR, "idf_fp.png"), dpi=130); plt.close()


def plot_scale(summ):
    Ns = sorted(summ.keys())
    raw = [summ[N]["raw_f1"] for N in Ns]; rawsd = [summ[N]["raw_f1_sd"] for N in Ns]
    z = [summ[N]["z_f1"] for N in Ns]; zsd = [summ[N]["z_f1_sd"] for N in Ns]
    plt.figure(figsize=(7, 4))
    plt.errorbar(Ns, raw, yerr=rawsd, marker="o", label="raw tau", capsize=3)
    plt.errorbar(Ns, z, yerr=zsd, marker="s", label="null-normalised z", capsize=3)
    plt.xscale("log"); plt.xticks(Ns, [str(n) for n in Ns])
    plt.xlabel("config size (# categories)"); plt.ylabel("F1 (leak detection)")
    plt.title("Scale-invariance: F1 vs config size"); plt.ylim(0, 1.05); plt.legend()
    plt.tight_layout(); plt.savefig(os.path.join(PLOTDIR, "scale.png"), dpi=130); plt.close()

    plt.figure(figsize=(7, 4))
    tr = [summ[N]["tau_raw"] for N in Ns]; tz = [summ[N]["tau_z"] for N in Ns]
    plt.plot(Ns, tr, marker="o", label="raw tau* (drifts)")
    plt.plot(Ns, tz, marker="s", label="z cutoff* (stable)")
    plt.xscale("log"); plt.xticks(Ns, [str(n) for n in Ns])
    plt.xlabel("config size (# categories)"); plt.ylabel("optimal threshold")
    plt.title("Threshold drift: raw tau vs null-z cutoff"); plt.legend()
    plt.tight_layout(); plt.savefig(os.path.join(PLOTDIR, "threshold_drift.png"), dpi=130); plt.close()


def plot_pr(best_params, variant, corpus):
    s, y, _, _, _ = score_corpus(corpus, best_params, variant)
    p, r, th = precision_recall_curve(y, s)
    plt.figure(figsize=(6, 4))
    plt.plot(r, p, marker=".", color="#8172B3")
    plt.xlabel("recall"); plt.ylabel("precision")
    plt.title("Precision-Recall (recommended config)"); plt.ylim(0, 1.02); plt.xlim(0, 1.02)
    plt.tight_layout(); plt.savefig(os.path.join(PLOTDIR, "pr_curve.png"), dpi=130); plt.close()


def plot_sensitivity(sens):
    Ls = [3, 4, 5, 6]
    none = [sens[(L, "none")] for L in Ls]
    port = [sens[(L, "porter")] for L in Ls]
    plt.figure(figsize=(6, 4))
    plt.plot(Ls, none, marker="o", label="S=none (word-level)")
    plt.plot(Ls, port, marker="s", label="S=porter (stem)")
    plt.xlabel("min token length L"); plt.ylabel("held-out F1")
    plt.title("Sensitivity to L and stemmer"); plt.ylim(0, 1.02); plt.xticks(Ls)
    plt.legend(); plt.tight_layout()
    plt.savefig(os.path.join(PLOTDIR, "sensitivity_L.png"), dpi=130); plt.close()


# ============================================================================
# 6. MAIN
# ============================================================================
def main():
    corpus = build_corpus()
    n_cat = len(corpus)
    n_link = sum(len(c["members"]) for c in corpus)
    n_leak_cat = sum(1 for c in corpus if c["label"] == "leak")
    sub = Counter(c["subtype"] for c in corpus)
    print("=" * 78)
    print("CORPUS")
    print(f"  categories={n_cat}  links={n_link}  leak_categories={n_leak_cat}  "
          f"clean_categories={n_cat - n_leak_cat}")
    print(f"  subtypes: {dict(sub)}")
    print(f"  domains: {dict(Counter(c['domain'] for c in corpus))}")
    print(f"  nltk_stemmers={_HAVE_NLTK}")

    train, test = split_corpus(corpus, frac=0.5)
    print(f"  split: train_cat={len(train)}  test_cat={len(test)}")

    print("\n" + "=" * 78); print("GRID SEARCH (held-out, F1-tuned tau on train)")
    rows = run_grid(train, test)
    print(f"  evaluated {len(rows)} configs")
    print("  TOP 12 configs by held-out F1:")
    hdr = f"  {'F1':>5} {'AUC':>5} {'P':>5} {'R':>5} | var L  S        w        M          cut  tau"
    print(hdr); print("  " + "-" * len(hdr))
    for r in rows[:12]:
        print(f"  {r['f1']:.3f} {r['auc']:.3f} {r['precision']:.3f} {r['recall']:.3f} | "
              f"{r['variant'][:9]:9} {r['L']} {r['S']:8} {r['w']:8} {r['M']:10} "
              f"{r['idf_cutoff']:.1f} {r['tau']:.3f}")

    def best_where(pred):
        cand = [r for r in rows if pred(r)]
        return cand[0] if cand else None
    print("\n  Best config per intersection measure M:")
    for M in ["maxweight", "wjaccard", "coverage", "fuzzy"]:
        b = best_where(lambda r, M=M: r["M"] == M)
        if b:
            print(f"    M={M:10} F1={b['f1']:.3f} P={b['precision']:.3f} R={b['recall']:.3f} "
                  f"(L={b['L']} S={b['S']} w={b['w']} var={b['variant']})")
    print("  Best config per variant:")
    for v in ["term_term", "term_name"]:
        b = best_where(lambda r, v=v: r["variant"] == v)
        if b:
            print(f"    {v:10} F1={b['f1']:.3f} P={b['precision']:.3f} R={b['recall']:.3f} "
                  f"(L={b['L']} S={b['S']} M={b['M']})")

    best = rows[0]
    best_params = dict(L=best["L"], S=best["S"], w=best["w"], M=best["M"],
                       idf_cutoff=best["idf_cutoff"], drop_ubiq_stop=best["drop_ubiq_stop"])
    best_variant = best["variant"]

    # best WORD-LEVEL (S=none) config for the stemming verdict
    word_rows = [r for r in rows if r["S"] == "none"]
    stem_rows = [r for r in rows if r["S"] in ("porter", "snowball", "prefix4", "prefix5")]
    print(f"\n  STEMMING VERDICT: best word-level F1={word_rows[0]['f1']:.3f} "
          f"(S=none, M={word_rows[0]['M']}) vs best stemmed F1={stem_rows[0]['f1']:.3f} "
          f"(S={stem_rows[0]['S']}, M={stem_rows[0]['M']})  "
          f"delta={stem_rows[0]['f1'] - word_rows[0]['f1']:+.3f}")

    # IDF verdict
    uniform_rows = [r for r in rows if r["w"] == "uniform"]
    idf_rows = [r for r in rows if r["w"] == "idf"]
    print(f"  IDF VERDICT:      best uniform F1={uniform_rows[0]['f1']:.3f} vs "
          f"best IDF F1={idf_rows[0]['f1']:.3f}  "
          f"delta={idf_rows[0]['f1'] - uniform_rows[0]['f1']:+.3f}")

    print("\n" + "=" * 78); print("FP ANALYSIS on ubiquitous-word traps (uniform vs IDF)")
    fp = fp_analysis(corpus, best_variant)
    for w in ("uniform", "idf"):
        d = fp[w]
        print(f"  w={w:8} trap_links={d['trap_links']:3d} fired={d['trap_fired']:3d} "
              f"FP_rate={d['trap_fp_rate']:.3f} overall_F1={d['overall_f1']:.3f} tau={d['tau']:.3f}")

    print("\n" + "=" * 78); print("SUBTYPE RECALL / behaviour (recommended config)")
    rec, tau_full = subtype_recall(corpus, best_params, best_variant)
    for st in ["leak_surface", "leak_morph", "leak_suffix", "trap_ubiq", "semantic"]:
        if st in rec:
            kind = "recall" if st.startswith("leak") else "FP-rate"
            print(f"  {st:14} {kind}={rec[st]:.3f}")

    print("\n" + "=" * 78); print("SCALE TEST (4/8/30/100 categories): raw tau vs null-z")
    scale = scale_test(best_params, best_variant)
    print(f"  {'N':>4} {'raw_F1':>8} {'z_F1':>8} {'tau_raw':>9} {'tau_z':>8}")
    for N in sorted(scale):
        d = scale[N]
        print(f"  {N:>4} {d['raw_f1']:.3f}±{d['raw_f1_sd']:.2f} {d['z_f1']:.3f}±{d['z_f1_sd']:.2f}"
              f"   {d['tau_raw']:.3f}   {d['tau_z']:.3f}")
    tau_raw_range = max(scale[N]['tau_raw'] for N in scale) - min(scale[N]['tau_raw'] for N in scale)
    tau_z_range = max(scale[N]['tau_z'] for N in scale) - min(scale[N]['tau_z'] for N in scale)
    print(f"  tau drift across scale: raw={tau_raw_range:.3f}  null-z={tau_z_range:.3f}")

    print("\n" + "=" * 78); print("ADVERSARIAL TEST")
    adv = adversarial_test(best_params)
    print("  (adv_syn_name: leak hidden in NAME; members still rhyme -> term_term SHOULD fire,")
    print("   term_name should NOT.  adv_syn_memb: form gone, meaning stays -> BOTH should stay low)")
    agg = defaultdict(lambda: defaultdict(list))
    for a in adv:
        agg[a["subtype"]][a["variant"]].append(a["mean"])
    for st in ["adv_syn_name", "adv_syn_memb"]:
        for v in ["term_term", "term_name"]:
            if agg[st][v]:
                print(f"  {st:14} variant={v:10} mean_score={np.mean(agg[st][v]):.3f}")

    print("\n" + "=" * 78); print("SENSITIVITY to L x stemmer")
    sens = sensitivity_L(train, test, best_variant, best_params)
    for L in [3, 4, 5, 6]:
        print(f"  L={L}: none={sens[(L,'none')]:.3f}  porter={sens[(L,'porter')]:.3f}")

    # ---- plots ----
    plot_ablation_S(rows); plot_ablation_M(rows); plot_idf_fp(fp)
    plot_scale(scale); plot_pr(best_params, best_variant, corpus); plot_sensitivity(sens)
    print(f"\n  plots -> {PLOTDIR}")

    # ---- dump ----
    dump = dict(
        corpus=dict(n_cat=n_cat, n_link=n_link, n_leak_cat=n_leak_cat, subtypes=dict(sub)),
        recommended=dict(params=best_params, variant=best_variant,
                         tau_full_corpus=float(tau_full),
                         heldout=dict(f1=best["f1"], auc=best["auc"],
                                      precision=best["precision"], recall=best["recall"])),
        stemming=dict(word_f1=word_rows[0]["f1"], stem_f1=stem_rows[0]["f1"],
                      best_word=word_rows[0], best_stem=stem_rows[0]),
        idf=dict(uniform_f1=uniform_rows[0]["f1"], idf_f1=idf_rows[0]["f1"]),
        fp_analysis=fp, subtype_recall=rec, scale=scale,
        adversarial=[{k: a[k] for k in ("subtype", "variant", "mean", "name")} for a in adv],
        sensitivity={f"L{L}_{S}": sens[(L, S)] for L in [3,4,5,6] for S in ["none","porter"]},
        top_configs=rows[:12],
    )
    with open(os.path.join(OUTDIR, "morph_leak_results.json"), "w") as f:
        json.dump(dump, f, indent=2, default=float)
    print(f"  results -> morph_leak_results.json")
    print("\nDONE.")
    return dump


if __name__ == "__main__":
    main()
