# validation/ — весь код валидации конфигов (движок R1–R7 + приёмка + адаптеры)

Независимый слой приёмки конфигов по спеке «Валидация требований конфигов».
НЕ заменяет self-check генерилки (`connections_v2.validate`, 11 publishability-гейтов) —
это вторая, внешняя линия: генерилка проверяет себя, приёмка проверяет генерилку.

Вход (авто-детект): v1-конфиг (`tags`/`axes`), input-schema (specialty строкой)
или нативный v2-пул (`schema_version: connections_v2_config_pool`).

## Состав папки (2026-06-11, реорганизация: код валидации здесь, в tests/ — только тесты)

- **движок R1–R7**: `loader`, `textutil`, `solver` (CP-SAT: mode-1/2/3 + trap_quality),
  `r1_volume` … `r7_reproducibility`, `enrich` (arXiv+OpenAlex, live+кэш+offline),
  `report`, `run` (validate/validate_full + CLI), `schema_check`, `acceptance.yaml` (пороги);
- **приёмка**: `acceptance.py` — единая точка вердикта (metric gates + assembly gates),
  `puzzle_assembly.py` + `repro_check.mjs` / `enumerate.mjs` (Node-обвязка реального генератора);
- **адаптеры**: `rich_to_v1.py`, `legacy_to_v1.py`, `from_generator.py` (E2E: генерилка → вердикт);
- **наследие**: `config_metrics.py` (deprecation-shim; движок 2026-06-03 — предок пакета,
  маппинг старых имён гейтов в docstring), `puzzle_eval.py`, `golden.json`,
  ноутбуки `acceptance_v1*.ipynb`, `research/` (sem_vs_morph).

## Запуск

```bash
python validation/run.py configs/v1/ai-safety-v1.json --out reports/   # R1–R7, вердикт+exit-код
python validation/run.py ../connections-gen/pool.json --enrich live    # + arXiv/OpenAlex
python validation/acceptance.py configs/v1/ai-safety-v1.json           # метрики + сборка (Node)
python validation/from_generator.py ../connections-gen/seeds --batch   # батч rich-пулов
# exit: 0 accept · 1 reject · 2 pending
```

```python
from validation import validate_full, load_acceptance
rep = validate_full("pool.json", load_acceptance("validation/acceptance.yaml"))
```

## Тесты (pytest, из корня репо; в tests/ только тесты)

- `tests/test_validation_unit.py` — юнит: textutil/solver/loader/R3/R4/R5 на синтетике и фейковых клиентах (без сети);
- `tests/test_acceptance.py` — гейты acceptance.py (сценарии T1–T6 из спеки, наследник test_config_metrics.py);
- `tests/test_validation_integration.py` — validate() на конфигах репозитория, детерминизм, required_gates;
- `tests/test_validation_e2e.py` — v2-пул → (export_input | напрямую) → валидация; CLI exit-коды; батч from-generator;
- `tests/test_from_generator.py` — rich-пул → rich_to_v1 → acceptance.

Зависимости: `ortools` (R2), `nltk` (стемминг R3, опц.), `pyyaml` (acceptance, опц.).
Обогащение R4/R5: arXiv + OpenAlex, кэш в `validation/enrich_cache/`,
env `OPENALEX_MAILTO` / `OPENALEX_API_KEY`. Архитектура и открытые развилки —
`spec_rework_plan_2026-06-11.md` в ресёрч-папке.
