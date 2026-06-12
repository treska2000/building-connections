"""validation — единый пакет валидации конфигов: движок R1–R7 + приёмка + адаптеры.

Структура пакета:
    движок R1–R7      loader, textutil, solver, r1_volume … r7_reproducibility,
                      enrich, report, run (validate/validate_full + CLI), schema_check
    приёмка           acceptance (метрики + сборка), puzzle_assembly (+ repro_check.mjs,
                      enumerate.mjs — Node-обвязка реального генератора)
    адаптеры          rich_to_v1, legacy_to_v1, from_generator (E2E генерилка → вердикт)
    наследие          config_metrics (deprecation-shim), puzzle_eval, ноутбуки, research/

Позиционирование: connections-gen/connections_v2 валидирует сам себя
(publishability-гейты) — это self-check генератора. Этот пакет — НЕЗАВИСИМЫЙ
приёмочный слой на стороне сборки: принимает v1-конфиг (tags/axes), input-schema
или нативный v2-пул и выдаёт вердикт accept / reject / pending.

API:
    from validation import validate, validate_full, load_acceptance
    from validation import acceptance            # гейтовый вердикт (метрики+сборка)
    report = validate_full("configs/v1/ai-safety-v1.json", load_acceptance(None))

CLI:
    python validation/run.py configs/v1/ai-safety-v1.json --out reports/
    python validation/acceptance.py configs/v1/ai-safety-v1.json
"""
from .run import validate, validate_full, load_acceptance, DEFAULT_ACCEPTANCE  # noqa: F401
from .enrich import Enrichment, DISABLED  # noqa: F401
from . import loader, solver, report  # noqa: F401
