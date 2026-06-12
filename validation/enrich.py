"""enrich.py — обогащение Level 1 (live API + агрессивный кэш) по спеке
source_signals_deterministic_2026-06-07.md. По умолчанию ВЫКЛ (DISABLED).

Клиенты:
  ArxivClient    — batch Atom API (id_list): existence, title, abstract,
                   primary_category, journal_ref/doi/comment (peer_review).
  OpenAlexClient — works по DOI 10.48550/arXiv.<id> (батч через |):
                   is_retracted, cited_by_count, authorships, topics;
                   works?search=<term> → meta.count (corpus_frequency).

Детерминированность: live API недетерминирован (данные меняются) → метрики на нём
помечаются deterministic=False, ПОКА в acceptance.yaml не запинён openalex_snapshot
(Level 2). Кэш (JSON на id/термин) делает повторные прогоны мгновенными и
воспроизводимыми в пределах кэша. offline=True — читать ТОЛЬКО кэш, в сеть не ходить.

Подключение: en = Enrichment.live(cache_dir=".enrich_cache", mailto="you@x.com")
             validate(cfg, acc, en=en)   (или run.py --enrich live)
API-ключ OpenAlex (опц.): env OPENALEX_API_KEY; mailto: env OPENALEX_MAILTO.
"""
from __future__ import annotations
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?", re.I)
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARX = "{http://arxiv.org/schemas/atom}"

PEER_REVIEW_HINT = re.compile(
    r"accepted|published|to appear|proceedings|camera.ready|NeurIPS|ICML|ICLR|ACL\b|"
    r"EMNLP|NAACL|CVPR|ICCV|ECCV|AAAI|IJCAI|KDD|WWW\b|SIGIR|COLT|AISTATS|UAI\b|"
    r"TMLR|JMLR|TACL|Nature|Science\b", re.I)


def arxiv_id_from_url(url: str) -> str | None:
    m = ARXIV_ID.search(url or "")
    return m.group(1) if m else None


class _Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", key)[:150]
        return self.root / f"{safe}.json"

    def get(self, key: str):
        p = self._p(key)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def put(self, key: str, value) -> None:
        self._p(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "connections-bench-validator/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class ArxivClient:
    """Сигналы S1 (resolves_on_arxiv), S3 (peer_review_venue), S4 (primary_category) + abstract."""

    def __init__(self, cache_dir="enrich_cache/arxiv", offline=False, politeness_s=3.0):
        self.cache = _Cache(Path(cache_dir))
        self.offline = offline
        self.politeness_s = politeness_s
        self.errors: list[str] = []

    def metas(self, ids: list[str]) -> dict[str, dict | None]:
        """id → meta | {'exists': False} | None (=unknown: сеть недоступна/ошибка)."""
        out: dict[str, dict | None] = {}
        missing = []
        for i in sorted(set(ids)):
            c = self.cache.get(f"arxiv_{i}")
            if c is not None:
                out[i] = c
            else:
                missing.append(i)
        if missing and not self.offline:
            for chunk_start in range(0, len(missing), 80):
                chunk = missing[chunk_start:chunk_start + 80]
                url = ("https://export.arxiv.org/api/query?id_list="
                       + ",".join(chunk) + f"&max_results={len(chunk)}")
                try:
                    feed = ET.fromstring(_http_get(url))
                except Exception as e:
                    self.errors.append(f"arxiv batch: {e}")
                    for i in chunk:
                        out[i] = None
                    continue
                found = {}
                for entry in feed.findall(f"{_ATOM}entry"):
                    eid = (entry.findtext(f"{_ATOM}id") or "")
                    m = re.search(r"abs/(\d{4}\.\d{4,5})", eid)
                    if not m:
                        continue  # пустой/ошибочный entry
                    pc = entry.find(f"{_ARX}primary_category")
                    found[m.group(1)] = {
                        "exists": True,
                        "title": (entry.findtext(f"{_ATOM}title") or "").strip(),
                        "abstract": (entry.findtext(f"{_ATOM}summary") or "").strip(),
                        "primary_category": pc.get("term") if pc is not None else None,
                        "journal_ref": (entry.findtext(f"{_ARX}journal_ref") or "").strip(),
                        "doi": (entry.findtext(f"{_ARX}doi") or "").strip(),
                        "comment": (entry.findtext(f"{_ARX}comment") or "").strip(),
                    }
                for i in chunk:
                    meta = found.get(i, {"exists": False})
                    self.cache.put(f"arxiv_{i}", meta)
                    out[i] = meta
                time.sleep(self.politeness_s)  # politeness arXiv
        for i in missing:
            out.setdefault(i, None)  # offline и нет кэша → unknown
        return out


class OpenAlexClient:
    """Сигналы S2 (retracted), S5 (citations), S6 (authorships→независимость),
    S7 (corpus_frequency), topics (R5)."""

    BASE = "https://api.openalex.org"

    def __init__(self, cache_dir="enrich_cache/openalex", offline=False,
                 mailto=None, api_key=None):
        self.cache = _Cache(Path(cache_dir))
        self.offline = offline
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO")
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY")
        self.errors: list[str] = []

    def _params(self, extra: dict) -> str:
        p = dict(extra)
        if self.mailto:
            p["mailto"] = self.mailto
        if self.api_key:
            p["api_key"] = self.api_key
        return urllib.parse.urlencode(p)

    @staticmethod
    def _slim(w: dict) -> dict:
        prim = (w.get("primary_topic") or {})
        return {
            "found": True,
            "is_retracted": bool(w.get("is_retracted")),
            "cited_by_count": w.get("cited_by_count", 0),
            "authors": [a.get("author", {}).get("id") for a in w.get("authorships", [])
                        if a.get("author", {}).get("id")],
            "institutions": [i.get("id") for a in w.get("authorships", [])
                             for i in a.get("institutions", []) if i.get("id")],
            "field": (prim.get("field") or {}).get("display_name"),
            "subfield": (prim.get("subfield") or {}).get("display_name"),
        }

    def works_by_arxiv(self, ids: list[str]) -> dict[str, dict | None]:
        """id → work | {'found': False} | None (unknown)."""
        out: dict[str, dict | None] = {}
        missing = []
        for i in sorted(set(ids)):
            c = self.cache.get(f"oa_{i}")
            if c is not None:
                out[i] = c
            else:
                missing.append(i)
        if missing and not self.offline:
            for s in range(0, len(missing), 40):
                chunk = missing[s:s + 40]
                dois = "|".join(f"doi:10.48550/arxiv.{i}" for i in chunk)
                url = (f"{self.BASE}/works?" + self._params({
                    "filter": dois, "per-page": len(chunk),
                    "select": "doi,is_retracted,cited_by_count,authorships,primary_topic"}))
                try:
                    data = json.loads(_http_get(url))
                except Exception as e:
                    self.errors.append(f"openalex works: {e}")
                    for i in chunk:
                        out[i] = None
                    continue
                found = {}
                for w in data.get("results", []):
                    m = re.search(r"arxiv\.(\d{4}\.\d{4,5})", (w.get("doi") or ""), re.I)
                    if m:
                        found[m.group(1)] = self._slim(w)
                for i in chunk:
                    rec = found.get(i, {"found": False})  # нет в OpenAlex ≠ retracted → unknown-нейтрально
                    self.cache.put(f"oa_{i}", rec)
                    out[i] = rec
        for i in missing:
            out.setdefault(i, None)
        return out

    def term_count(self, term: str) -> int | None:
        key = "cnt_" + term.lower().strip()
        c = self.cache.get(key)
        if c is not None:
            return c.get("count")
        if self.offline:
            return None
        url = f"{self.BASE}/works?" + self._params(
            {"search": term, "per-page": 1, "select": "id"})
        try:
            data = json.loads(_http_get(url))
        except Exception as e:
            self.errors.append(f"openalex search '{term}': {e}")
            return None
        cnt = int(data.get("meta", {}).get("count", 0))
        self.cache.put(key, {"count": cnt})
        return cnt


class Enrichment:
    def __init__(self, openalex=None, embedder=None, arxiv=None, snapshot_pinned=False):
        self.openalex = openalex
        self.embedder = embedder
        self.arxiv = arxiv
        self.snapshot_pinned = snapshot_pinned  # Level 2 → deterministic=True

    @classmethod
    def live(cls, cache_dir="enrich_cache", mailto=None, api_key=None, offline=False):
        root = Path(cache_dir)
        return cls(arxiv=ArxivClient(root / "arxiv", offline=offline),
                   openalex=OpenAlexClient(root / "openalex", offline=offline,
                                           mailto=mailto, api_key=api_key))

    def sources_available(self) -> bool:
        return self.arxiv is not None

    def openalex_available(self) -> bool:
        return self.openalex is not None

    def embedder_available(self) -> bool:
        return self.embedder is not None

    def deterministic(self) -> bool:
        """Live API → False; True только при запиненном снапшоте (Level 2)."""
        return self.snapshot_pinned


# дефолт: ничего не подключено → детерминированное ядро считается, обогащение помечается requires
DISABLED = Enrichment()
