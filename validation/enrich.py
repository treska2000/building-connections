"""Level-1 enrichment (live APIs + an aggressive file cache) per the
source_signals_deterministic spec. Disabled by default (DISABLED). Live APIs are
non-deterministic — metrics based on them are flagged deterministic=False until
openalex_snapshot is pinned in thresholds.yaml (Level 2). offline=True reads the
cache only. Optional env keys: OPENALEX_API_KEY, OPENALEX_MAILTO.

Обогащение Level 1 (live API + агрессивный файловый кэш) по спеке
source_signals_deterministic. По умолчанию ВЫКЛ (DISABLED). Live API недетерминирован —
метрики на нём помечаются deterministic=False, пока в thresholds.yaml не запинён
openalex_snapshot (Level 2). offline=True — только кэш. Ключи (опц.): env
OPENALEX_API_KEY, OPENALEX_MAILTO.
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
    """Extract an arXiv id from a URL (abs/pdf form).

    In: url (str). Out: arXiv id (str) | None.

    Извлекает arXiv id из URL (abs/pdf).
    In: url (str). Out: arXiv id (str) | None.
    """
    m = ARXIV_ID.search(url or "")
    return m.group(1) if m else None


class _Cache:
    """A file-based JSON cache keyed by string (one key = one file).

    Файловый JSON-кэш по ключу (один ключ = один файл).
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        """Return a safe filesystem path for the given cache key.

        In: key (str). Out: Path.

        Безопасный путь файла кэша для ключа.
        In: key (str). Out: Path.
        """
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", key)[:150]
        return self.root / f"{safe}.json"

    def get(self, key: str):
        """Read a cached value by key, returning None if missing or corrupt.

        In: key (str). Out: dict | None.

        Читает значение по ключу; None при отсутствии или битом файле.
        In: key (str). Out: dict | None.
        """
        p = self._p(key)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def put(self, key: str, value) -> None:
        """Write a JSON-serializable value to the cache under the given key.

        In: key (str), value (JSON-serializable). Out: None.

        Записывает JSON-сериализуемое значение по ключу.
        In: key (str), value (JSON-сериализуемое). Out: None.
        """
        self._p(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    """Perform an HTTP GET with an identifying User-Agent.

    In: url (str), timeout (float, seconds). Out: bytes.

    HTTP GET с идентифицирующим User-Agent.
    In: url (str), timeout (float, секунды). Out: bytes.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "connections-bench-validator/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class ArxivClient:
    """arXiv Atom API client covering signals S1 (resolves), S3 (peer_review), S4
    (primary_category) plus title/abstract for faithfulness checks.
    Uses batched id_list requests, a file cache, and politeness delays.

    Клиент arXiv Atom API: сигналы S1 (resolves), S3 (peer_review), S4
    (primary_category) + title/abstract для faithfulness. Батчи id_list, кэш, politeness.
    """

    def __init__(self, cache_dir="enrich_cache/arxiv", offline=False, politeness_s=3.0):
        self.cache = _Cache(Path(cache_dir))
        self.offline = offline
        self.politeness_s = politeness_s
        self.errors: list[str] = []

    def metas(self, ids: list[str]) -> dict[str, dict | None]:
        """Fetch paper metadata for a list of arXiv ids in batches, using the cache.

        In: ids (list of arXiv id strings).
        Out: dict id -> meta dict | {'exists': False} (not found) | None (unknown: network error or offline cache miss).

        Метаданные статей батчами с использованием кэша.
        In: ids (список arXiv id).
        Out: dict id -> meta | {'exists': False} (не найден) | None (unknown: ошибка сети или offline без кэша).
        """
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
    """OpenAlex API client covering signals S2 (retracted), S5 (citations),
    S6 (authorships -> group independence), S7 (corpus_frequency) and topics for R5.
    Uses batched DOI lookups and a file cache.

    Клиент OpenAlex: сигналы S2 (retracted), S5 (citations), S6 (authorships ->
    независимость групп), S7 (corpus_frequency) + topics для R5. Батчи по DOI, кэш.
    """

    BASE = "https://api.openalex.org"

    def __init__(self, cache_dir="enrich_cache/openalex", offline=False,
                 mailto=None, api_key=None):
        self.cache = _Cache(Path(cache_dir))
        self.offline = offline
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO")
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY")
        self.errors: list[str] = []

    def _params(self, extra: dict) -> str:
        """Build a URL query string, appending mailto and api_key when set.

        In: extra (dict of query parameters). Out: str (URL-encoded query string).

        Строит query-string, добавляя mailto/api_key если заданы.
        In: extra (dict параметров). Out: str (URL-encoded query string).
        """
        p = dict(extra)
        if self.mailto:
            p["mailto"] = self.mailto
        if self.api_key:
            p["api_key"] = self.api_key
        return urllib.parse.urlencode(p)

    @staticmethod
    def _slim(w: dict) -> dict:
        """Slim an OpenAlex works record down to the fields required by signals.

        In: w (raw OpenAlex works dict). Out: dict with keys found, is_retracted,
        cited_by_count, authors, institutions, field, subfield.

        Ужимает запись OpenAlex до полей, нужных сигналам.
        In: w (сырой dict OpenAlex works). Out: dict с ключами found, is_retracted,
        cited_by_count, authors, institutions, field, subfield.
        """
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
        """Fetch OpenAlex works records via DOI 10.48550/arXiv.<id> in batches.

        In: ids (list of arXiv id strings).
        Out: dict id -> slimmed work dict | {'found': False} | None (unknown).

        Записи works по DOI 10.48550/arXiv.<id> батчами.
        In: ids (список arXiv id).
        Out: dict id -> slim dict | {'found': False} | None (unknown).
        """
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
        """Return S7 term prevalence as the meta.count of an OpenAlex works search.

        In: term (str). Out: int (count) | None (unknown: network error or offline).

        S7: распространённость термина = meta.count поиска works.
        In: term (str). Out: int | None (unknown: ошибка сети или offline).
        """
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
    """Container of enrichment clients; modules probe availability via *_available().

    Контейнер клиентов обогащения; модули спрашивают доступность через *_available().
    """

    def __init__(self, openalex=None, embedder=None, arxiv=None, snapshot_pinned=False):
        self.openalex = openalex
        self.embedder = embedder
        self.arxiv = arxiv
        self.snapshot_pinned = snapshot_pinned  # Level 2 → deterministic=True

    @classmethod
    def live(cls, cache_dir="enrich_cache", mailto=None, api_key=None, offline=False):
        """Create a live-mode Enrichment with arXiv and OpenAlex sharing one cache root.

        In: cache_dir (str), mailto (str | None), api_key (str | None), offline (bool).
        Out: Enrichment.

        Фабрика live-режима: arXiv + OpenAlex с общим корнем кэша.
        In: cache_dir (str), mailto (str | None), api_key (str | None), offline (bool).
        Out: Enrichment.
        """
        root = Path(cache_dir)
        return cls(arxiv=ArxivClient(root / "arxiv", offline=offline),
                   openalex=OpenAlexClient(root / "openalex", offline=offline,
                                           mailto=mailto, api_key=api_key))

    def sources_available(self) -> bool:
        """Whether the arXiv client is available. / Доступен ли arXiv-клиент."""
        return self.arxiv is not None

    def openalex_available(self) -> bool:
        """Whether the OpenAlex client is available. / Доступен ли OpenAlex-клиент."""
        return self.openalex is not None

    def embedder_available(self) -> bool:
        """Whether the embedder is available. / Доступен ли эмбеддер."""
        return self.embedder is not None

    def deterministic(self) -> bool:
        """Return whether enrichment is deterministic: False in live mode, True only
        when a snapshot is pinned (Level 2).

        In: (none). Out: bool.

        Детерминировано ли обогащение: False в live-режиме, True только при запиненном
        снапшоте (Level 2).
        In: (нет). Out: bool.
        """
        return self.snapshot_pinned


# default: nothing attached → the deterministic core runs, enrichment metrics stay pending
DISABLED = Enrichment()
