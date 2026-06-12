"""DEPRECATED (2026-06-11): метрики переехали в пакет validation/.

Этот модуль был движком R1–R6 образца 2026-06-03 (лексический Jaccard-лик,
mode-fit без solver'а, capacity-bootstrap). Его развитие — пакет `validation/`
(CP-SAT solver, ресёрч-конфиг morph_leak, обогащение arXiv/OpenAlex, R7).
Единая точка вердикта — validation/acceptance.py (метрики + сборка).

Маппинг старых имён: R2_lexical_leak→R3_morph_leak · R3_evidence→R4_sources ·
R4_modeN→R2_modeN · R6 capacity→R2 ceiling/effective (extra solver-отчёта).

Для совместимости здесь остаётся только validate_schema (теперь обёртка над
validation.schema_check, понимает tags и axes).
"""
from __future__ import annotations
from .schema_check import validate_schema  # noqa: F401
from .loader import load_config  # noqa: F401


def run(*args, **kwargs):  # pragma: no cover
    raise RuntimeError(
        "config_metrics.run() удалён: используй acceptance.acceptance() "
        "или validation.validate_full(). См. docstring модуля (маппинг гейтов)."
    )
