"""validation — единый пакет валидации конфигов: движок R1–R7 + приёмка + адаптеры.

Структура пакета:
    движок R1–R7      loader, textutil, solver, r1_volume … r7_reproducibility,
                      enrich, report, run (validate/validate_full + CLI), schema_check
    приёмка           acceptance (метрики + сборка), puzzle_assembly (+ repro_check.mjs,
                      enumerate.mjs — Node-обвязка реального генератора)
    адаптеры          rich_to_v1, legacy_to_v1, from_generator (E2E генерилка → вердикт)
    наследие          puzzle_eval (+golden.json) — оценка банка терминов живой игры

Позиционирование: connections-gen/connections_v2 валидирует сам себя
(publishability-гейты) — это self-check генератора. Этот пакет — НЕЗАВИСИМЫЙ
приёмочный слой на стороне сборки: принимает v1-конфиг (tags/axes), input-schema
или нативный v2-пул и выдаёт вердикт accept / reject / pending.

EN: validation — the unified config-validation package: the R1–R7 engine
(loader/solver/r1…r7/enrich/report/run/schema_check + thresholds.yaml), the
acceptance layer (acceptance.py + puzzle_assembly with the Node harness), format
adapters (rich_to_v1, legacy_to_v1, from_generator), and legacy tooling
(puzzle_eval, research/). It is the independent acceptance line on the assembly
side — the generator's own publishability gates live in connections_v2.

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
