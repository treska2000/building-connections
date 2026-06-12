"""enrich.py — обогащение Level 1 (live API + агрессивный файловый кэш) по спеке
source_signals_deterministic_2026-06-07. По умолчанию ВЫКЛ (DISABLED). Live API
недетерминирован → метрики на нём помечаются deterministic=False, пока в
thresholds.yaml не запинён openalex_snapshot (Level 2). offline=True — только кэш.
Ключи (опц.): env OPENALEX_API_KEY, OPENALEX_MAILTO.

enrich.py — Level-1 enrichment (live APIs + an aggressive file cache) per the
source_signals_deterministic_2026-06-07 spec. Disabled by default (DISABLED).
Live APIs are non-deterministic → metrics based on them are flagged
deterministic=False until openalex_snapshot is pinned in thresholds.yaml (Level 2).
offline=True reads the cache only. Optional keys: env OPENALEX_API_KEY, OPENALEX_MAILTO.
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
    """Извлекает arXiv-id из URL (abs/pdf). Вход: url. Выход: id (str) | None.
    Extracts the arXiv id from a URL (abs/pdf). In: url. Out: id (str) | None."""
    m = ARXIV_ID.search(url or "")
    return m.group(1) if m else None


class _Cache:
    """Файловый JSON-кэш по ключу (один ключ = один файл).
    A file-based JSON cache keyed by string (one key = one file)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        """Безопасный путь файла кэша для ключа. Вход: key. Выход: Path.
        Safe cache-file path for a key. In: key. Out: Path."""
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", key)[:150]
        return self.root / f"{safe}.json"

    def get(self, key: str):
        """Читает значение по ключу. Вход: key. Выход: dict | None (нет/битый файл).
        Reads a value by key. In: key. Out: dict | None (missing/corrupt file)."""
        p = self._p(key)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def put(self, key: str, value) -> None:
        """Записывает значение по ключу. Вход: key, value (JSON-сериализуемое). Выход: None.
        Writes a value by key. In: key, value (JSON-serializable). Out: None."""
        self._p(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    """HTTP GET с идентифицирующим User-Agent. Вход: url, timeout. Выход: bytes.
    HTTP GET with an identifying User-Agent. In: url, timeout. Out: bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "connections-bench-validator/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class ArxivClient:
    """Клиент arXiv Atom API: сигналы S1 (resolves), S3 (peer_review), S4
    (primary_category) + title/abstract для faithfulness. Батчи id_list, кэш, politeness.
    arXiv Atom API client: signals S1 (resolves), S3 (peer_review), S4
    (primary_category) + title/abstract for faithfulness. Batched id_list, cache, politeness."""

    def __init__(self, cache_dir="enrich_cache/arxiv", offline=False, politeness_s=3.0):
        self.cache = _Cache(Path(cache_dir))
        self.offline = offline
        self.politeness_s = politeness_s
        self.errors: list[str] = []

    def metas(self, ids: list[str]) -> dict[str, dict | None]:
        """Метаданные статей батчами. Вход: ids (arXiv id). Выход: dict
        id → meta | {'exists': False} (фейк) | None (unknown: сеть/offline без кэша).
        Paper metadata in batches. In: ids (arXiv ids). Out: dict
        id → meta | {'exists': False} (fake) | None (unknown: network/offline cache miss)."""
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
                        continue  # empty/error entry without a recognizable id
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
                time.sleep(self.politeness_s)  # arXiv politeness delay between batches
        for i in missing:
            out.setdefault(i, None)  # offline with no cache → unknown
        return out


class OpenAlexClient:
    """Клиент OpenAlex: сигналы S2 (retracted), S5 (citations), S6 (authorships →
    независимость групп), S7 (corpus_frequency) + topics для R5. Батчи по DOI, кэш.
    OpenAlex client: signals S2 (retracted), S5 (citations), S6 (authorships →
    group independence), S7 (corpus_frequency) + topics for R5. DOI batches, cache."""

    BASE = "https://api.openalex.org"

    def __init__(self, cache_dir="enrich_cache/openalex", offline=False,
                 mailto=None, api_key=None):
        self.cache = _Cache(Path(cache_dir))
        self.offline = offline
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO")
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY")
        self.errors: list[str] = []

    def _params(self, extra: dict) -> str:
        """Query-string с mailto/api_key. Вход: extra (dict). Выход: str.
        Query string with mailto/api_key attached. In: extra (dict). Out: str."""
        p = dict(extra)
        if self.mailto:
            p["mailto"] = self.mailto
        if self.api_key:
            p["api_key"] = self.api_key
        return urllib.parse.urlencode(p)

    @staticmethod
    def _slim(w: dict) -> dict:
        """Ужимает запись OpenAlex до полей, нужных сигналам. Вход: w. Выход: dict.
        Slims an OpenAlex record down to the fields the signals need. In: w. Out: dict."""
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
        """Записи works по DOI 10.48550/arXiv.<id> батчами. Вход: ids. Выход: dict
        id → work | {'found': False} | None (unknown).
        Works records via DOI 10.48550/arXiv.<id> in batches. In: ids. Out: dict
        id → work | {'found': False} | None (unknown)."""
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
                    # missing in OpenAlex != retracted → store as unknown-neutral
                    rec = found.get(i, {"found": False})
                    self.cache.put(f"oa_{i}", rec)
                    out[i] = rec
        for i in missing:
            out.setdefault(i, None)
        return out

    def term_count(self, term: str) -> int | None:
        """S7: распространённость термина = meta.count поиска works. Вход: term.
        Выход: int | None (unknown).
        S7: term prevalence = meta.count of a works search. In: term.
        Out: int | None (unknown)."""
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
    """Контейнер клиентов обогащения; модули спрашивают доступность через *_available().
    Container of enrichment clients; modules probe availability via *_available()."""

    def __init__(self, openalex=None, embedder=None, arxiv=None, snapshot_pinned=False):
        self.openalex = openalex
        self.embedder = embedder
        self.arxiv = arxiv
        self.snapshot_pinned = snapshot_pinned  # Level 2 → deterministic=True

    @classmethod
    def live(cls, cache_dir="enrich_cache", mailto=None, api_key=None, offline=False):
        """Фабрика live-режима: arXiv + OpenAlex с общим кэшем. Вход: cache_dir,
        mailto, api_key, offline. Выход: Enrichment.
        Live-mode factory: arXiv + OpenAlex sharing one cache root. In: cache_dir,
        mailto, api_key, offline. Out: Enrichment."""
        root = Path(cache_dir)
        return cls(arxiv=ArxivClient(root / "arxiv", offline=offline),
                   openalex=OpenAlexClient(root / "openalex", offline=offline,
                                           mailto=mailto, api_key=api_key))

    def sources_available(self) -> bool:
        """Доступен ли arXiv-клиент. Выход: bool. / Is the arXiv client available. Out: bool."""
        return self.arxiv is not None

    def openalex_available(self) -> bool:
        """Доступен ли OpenAlex-клиент. Выход: bool. / Is the OpenAlex client available. Out: bool."""
        return self.openalex is not None

    def embedder_available(self) -> bool:
        """Доступен ли эмбеддер. Выход: bool. / Is the embedder available. Out: bool."""
        return self.embedder is not None

    def deterministic(self) -> bool:
        """Детерминировано ли обогащение: live → False, True только при запиненном
        снапшоте (Level 2). Выход: bool.
        Whether enrichment is deterministic: live → False, True only with a pinned
        snapshot (Level 2). Out: bool."""
        return self.snapshot_pinned


# default: nothing attached → the deterministic core runs, enrichment metrics stay pending
DISABLED = Enrichment()
