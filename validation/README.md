# validation/ — весь код валидации конфигов (движок R1–R7 + приёмка + адаптеры)

Независимый слой приёмки конфигов по спеке «Валидация требований конфигов».
НЕ заменяет self-check генерилки (`connections_v2.validate`, 11 publishability-гейтов) —
это вторая, внешняя линия: генерилка проверяет себя, приёмка проверяет генерилку.

Вход (авто-детект): v1-конфиг (`tags`/`axes`), input-schema (specialty строкой)
или нативный v2-пул (`schema_version: connections_v2_config_pool`).

## Состав папки (2026-06-11, реорганизация: код валидации здесь, в tests/ — только тесты)

- **движок R1–R7**: `loader`, `textutil`, `solver` (CP-SAT: mode-1/2/3 + trap_quality),
  `r1_volume` … `r7_reproducibility`, `enrich` (arXiv+OpenAlex, live+кэш+offline),
  `report`, `run` (validate/validate_full + CLI), `schema_check`, `thresholds.yaml` (пороги, required_gates);
- **приёмка**: `acceptance.py` — единая точка вердикта (metric gates + assembly gates),
  `puzzle_assembly.py` + `repro_check.mjs` / `enumerate.mjs` (Node-обвязка реального генератора);
- **адаптеры**: `rich_to_v1.py`, `legacy_to_v1.py`, `from_generator.py` (E2E: генерилка → вердикт);
- **наследие**: `puzzle_eval.py` + `golden.json` — оценка банка терминов ЖИВОЙ игры
  (старые форматы configs/*.json; M1 сборка/эталон, M2–M4 структура, M5 LLM-judge);
  используется sampling_test.ipynb. Предок движка config_metrics.py удалён 2026-06-11
  (история в git, маппинг гейтов — в docstring acceptance.py); ресёрч sem_vs_morph
  переехал в ресёрч-папку проекта.

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
rep = validate_full("pool.json", load_acceptance("validation/thresholds.yaml"))
```

## Тесты (pytest, из корня репо; в tests/ только тесты)

- `tests/test_validation_unit.py` — юнит: textutil/solver/loader/R3/R4/R5 на синтетике и фейковых клиентах (без сети);
- `tests/test_acceptance.py` — гейты acceptance.py (сценарии T1–T6 из спеки, наследник test_config_metrics.py);
- `tests/test_validation_integration.py` — validate() на конфигах репозитория, детерминизм, required_gates;
- `tests/test_validation_e2e.py` — v2-пул → (export_input | напрямую) → валидация; CLI exit-коды; батч from-generator;
- `tests/test_from_generator.py` — rich-пул → rich_to_v1 → acceptance.

Зависимости: `validation/requirements.txt` (ortools — R2; nltk — стемминг R3, опц.; PyYAML — пороги, опц.).
Обогащение R4/R5: arXiv + OpenAlex, кэш в `validation/enrich_cache/`,
env `OPENALEX_MAILTO` / `OPENALEX_API_KEY`. Архитектура и открытые развилки —
`spec_rework_plan_2026-06-11.md` в ресёрч-папке.

---

# Метрики R1–R7: логика реализации / Metrics R1–R7: implementation logic

Двуязычное описание: сначала русский, затем английский. / Bilingual: Russian first, then English.

## Русский

Контракт каждой метрики единый: `{value, pass, deterministic, requires, gameable, counter}`.
Метрика с невыполненным `requires` даёт `pass = None` (⏳) и не валит вердикт, если её
требование не входит в `required_gates` (дефолт: R1, R2, R3, R5, R7). Пороги — в `thresholds.yaml`.

### R1 · Объём (`r1_volume.py`, детерминированно)

- `is_terms_count_ge_16` — уникальных терминов ≥ 16.
- `is_dedup` — дублей нормализованных имён терминов и дублей рёбер (термин, категория) ровно 0; контр-метрика к искусственному набиванию счётчиков.
- `is_base_categories_count_ge_4` — категорий с ≥ 4 терминами (`min_tag_size`) не меньше 4 (`min_base_tags`).
- `bonus_size3_pool` — справочно: категории ровно по 3 термина как пул естественных обманок; не гейт.
- Вердикт R1 = первые три метрики.

### R2 · Решаемость (`r2_solvability.py` + `solver.py`, CP-SAT, детерминированно)

Примитив `count_partitions(W)`: квадры = все 4-подмножества слов внутри одной категории;
CP-SAT-модель точного покрытия (каждое слово ровно в одной выбранной квадре, всего |W|/4 квадр);
перечисление решений с отсечкой cap = 2 — достаточно различить 0 / 1 / ≥ 2. `is_unique` = ровно одна разбивка.
Каждая мода = играбельное предусловие (счёт по множествам) + честная проверка solver'ом:

- **mode-1 (чистый пазл):** предусловие — ≥ 4 категорий с ≥ 4 solo-терминами (|tags| = 1);
  сэмплер (N = 400, seed = 42) тянет 4 категории × 4 термина → `exists_valid_puzzle_mode1`
  и `good_puzzle_yield_mode1` = VPY ≥ 0.70; в extra — `ceiling` (комбинаторный потолок
  Σ∏C(size, 4)) и `effective = ceiling × VPY`.
- **mode-2 (пересечения-ловушки):** предусловие — ≥ 1 пара категорий с |пересечения| ≥ 4;
  кандидат W обязан содержать термин с ≥ 2 тегами из выбранной четвёрки S, слова не
  переиспользуются; valid = `is_unique`. τ₂ не задан → гейт по exists (калибровка TBD).
  При drawn = 0 note объясняет: пересечение плотнее, чем |tag \ overlap| ≥ 4.
- **mode-3 (явные обманки):** пул = термины с `decoy_for_tags` (≥ 4); расширенное отношение
  R₃ = member_of ∪ decoy_for; `trap_quality(W)` = квадры, монохромные под R₃, но не под
  member_of (соблазнительная неверная четвёрка существует только из-за decoy-рёбер);
  valid₃ = `is_unique(member_of)` ∧ trap_quality ≥ 1. Без явных decoy — pending до эмбеддера
  (margin-вывод обманок).
- Вердикт R2 = mode-1 (pass_base); mode-2/3 — углубления: репортятся, гейт не валят.

### R3 · Семантичность (`r3_semanticity.py` + `textutil.py`)

- `morph_leak_rate` (детерм., ресёрч-конфиг 2026-06-08, held-out F1 = 0.858): токены имени =
  snowball-стем + помеченный 3-символьный суффикс (`~ase`), L = 3; IDF строится по именам
  терминов ЭТОГО конфига (config-relative); скор связи термин↔категория = максимальный вес
  `idf/idf_max` среди токенов, разделённых хотя бы с одним ДРУГИМ членом той же категории
  (вариант term↔term — неиграбелен переименованием категории); лик при скоре ≥ τ_link = 0.74;
  `rate = leaked/links ≤ 0.05`. Играбельна на синонимах → контр-метрика is_semantic.
- `is_semantic` — pending: ждёт ресёрч-задачи по эмбеддеру (семантика vs форма).

### R4 · Достоверность источников (`r4_source_quality.py` + `enrich.py`)

- `source_count` (детерм.): доля терминов с ≥ 3 источниками ≥ 0.9 — анкор требования, играбелен.
- Через обогащение (arXiv Atom батчами + OpenAlex works по DOI; файловый кэш;
  `deterministic = False` до пина `openalex_snapshot`):
  S1 `resolves_on_arxiv` (🔴 фейк-ссылка) · S2 `retracted` (🔴; нет записи ≠ retracted) ·
  S3 `peer_review_venue` (позитив: journal_ref / не-arXiv DOI / regex по comment) ·
  S4 `field_match` (архив primary_category ∈ маппинга поля; 🔴 только если чужие ВСЕ источники) ·
  S5 citations (контекст, не 🔴 по нулю) · S6 `independent_source_groups` — union-find источников
  по общим авторам/институтам, pass при ≥ 3 независимых группах (3 статьи одной лаборатории =
  1 группа → yellow) · S7 `corpus_frequency` (OpenAlex search count, контекст).
- `source_sanity_check` (faithfulness): стем-токены имени термина целиком найдены в
  title + abstract хотя бы одного его источника; доля ≥ 0.9.
- `source_attestation` (агрегация-DEFAULT, калибруемо): per-term red = фейк-ссылка ∨ ретракция ∨
  все источники чужого поля ∨ нет arXiv-источников; pass per-term = нет red ∧ sane ∧ groups ≥ 3;
  конфиг = нет red'ов ∧ доля проходящих терминов ≥ 0.9.

### R5 · Покрытие специализации (`r5_specialization.py`)

- `specialty_filled` (детерм.): area заполнен.
- `area_term_consistency`: поле термина = модальное поле его источников (OpenAlex
  `primary_topic.field`, fallback — маппинг архива arXiv → поле); доля терминов с полем ==
  ожидаемому (`specialty.field`) ≥ 0.70.
- `area_concentration`: модальное поле по всем терминам == ожидаемому ∧ modal_share ≥ 0.70 —
  ловит «размазанный» конфиг даже при проходной consistency.

### R6 · Сила связи (`r6_link_strength.py`) — legacy

Старая спека (sim_in / sim_margin / pmi); все метрики pending (requires embedder), в
`required_gates` не входит. Ждёт редизайна по ревизии 2026-06-11: cluster_cohesion +
label_fidelity + NPMI термин↔термин в пространстве абстрактов ИСТОЧНИКОВ (sim к
синтетическому описанию — самосогласованность генератора, не гейт).

### R7 · Воспроизводимость (`r7_reproducibility.py`, мета — на сам валидатор)

- `determinism_flags`: каждая метрика декларирует deterministic/requires — недетерминированное
  (live API, LLM) изолировано и помечено.
- `provenance`: config_id/version, версия acceptance, seed, эмбеддер, снапшот OpenAlex — в каждом отчёте.
- `reproducibility_check`: два независимых прогона validate(), diff детерминированных значений == 0.
- Автоматизация/e2e закрыты CLI (`run.py`, exit 0 accept / 1 reject / 2 pending) и тестовым контрактом.

### Гейтовый слой (`acceptance.py`)

Маппит отчёт R1–R7 в плоские гейты (`R2_modeN` = предусловие ∧ exists) и добавляет
поведенческую проверку реальным JS-генератором: `assembly_normal/advanced` = собирается ∧
воспроизводимо ∧ доска полная ∧ «один концепт — одна категория». Дефолтный required:
R1_volume, R2_mode1, R3_morph_leak, R5_specialty + обе сборки.

---

## English

Every metric follows one contract: `{value, pass, deterministic, requires, gameable, counter}`.
A metric whose `requires` is unmet yields `pass = None` (⏳) and does not block the verdict
unless its requirement is in `required_gates` (default: R1, R2, R3, R5, R7). Thresholds live in `thresholds.yaml`.

### R1 · Volume (`r1_volume.py`, deterministic)

- `is_terms_count_ge_16` — at least 16 unique terms.
- `is_dedup` — exactly zero duplicates of normalized term names and of (term, category) edges; counter-metric against inflating the counts.
- `is_base_categories_count_ge_4` — at least 4 categories (`min_base_tags`) with ≥ 4 terms each (`min_tag_size`).
- `bonus_size3_pool` — informational: categories with exactly 3 terms as a pool of natural decoys; not a gate.
- R1 verdict = the first three metrics.

### R2 · Solvability (`r2_solvability.py` + `solver.py`, CP-SAT, deterministic)

Primitive `count_partitions(W)`: quads = all 4-subsets of words within one category;
a CP-SAT exact-cover model (each word in exactly one chosen quad, |W|/4 quads total);
solution enumeration capped at 2 — distinguishing 0 / 1 / ≥ 2 is all that matters.
`is_unique` = exactly one partition. Each mode = a gameable precondition (set counting)
plus an honest solver check:

- **mode-1 (clean puzzle):** precondition — ≥ 4 categories with ≥ 4 solo terms (|tags| = 1);
  the sampler (N = 400, seed = 42) draws 4 categories × 4 terms → `exists_valid_puzzle_mode1`
  and `good_puzzle_yield_mode1` = VPY ≥ 0.70; extras carry `ceiling` (combinatorial upper
  bound Σ∏C(size, 4)) and `effective = ceiling × VPY`.
- **mode-2 (overlaps as traps):** precondition — ≥ 1 category pair with |intersection| ≥ 4;
  a candidate board W must contain a term holding ≥ 2 tags within the chosen four categories S,
  with no word reused; valid = `is_unique`. τ₂ is not set → the gate runs on exists (calibration TBD).
  When drawn = 0, the note explains: the overlap is denser than |tag \ overlap| ≥ 4 allows.
- **mode-3 (explicit decoys):** pool = terms with `decoy_for_tags` (≥ 4); extended relation
  R₃ = member_of ∪ decoy_for; `trap_quality(W)` = quads monochromatic under R₃ but not under
  member_of (the tempting wrong quad exists only because of decoy edges);
  valid₃ = `is_unique(member_of)` ∧ trap_quality ≥ 1. Without explicit decoys — pending until
  the embedder (margin-based decoy inference).
- R2 verdict = mode-1 (pass_base); mode-2/3 are deepenings: reported, never block the gate.

### R3 · Semanticity (`r3_semanticity.py` + `textutil.py`)

- `morph_leak_rate` (deterministic, research config 2026-06-08, held-out F1 = 0.858): name
  tokens = Snowball stem + a marked 3-character suffix token (`~ase`), L = 3; IDF is built over
  the term names of THIS config (config-relative); the term↔category link score = the maximum
  `idf/idf_max` weight among tokens shared with at least one OTHER member of the same category
  (the term↔term variant is ungameable by renaming the category); a link leaks at score ≥
  τ_link = 0.74; `rate = leaked/links ≤ 0.05`. Gameable via synonyms → countered by is_semantic.
- `is_semantic` — pending: awaits the embedder research task (semantics vs form).

### R4 · Source quality (`r4_source_quality.py` + `enrich.py`)

- `source_count` (deterministic): share of terms with ≥ 3 sources ≥ 0.9 — the requirement's anchor, gameable.
- Via enrichment (batched arXiv Atom + OpenAlex works by DOI; file cache;
  `deterministic = False` until `openalex_snapshot` is pinned):
  S1 `resolves_on_arxiv` (🔴 fake link) · S2 `retracted` (🔴; no record ≠ retracted) ·
  S3 `peer_review_venue` (positive: journal_ref / non-arXiv DOI / comment regex) ·
  S4 `field_match` (primary_category archive ∈ field mapping; 🔴 only when ALL sources are
  off-field) · S5 citations (context, never 🔴 for zero) · S6 `independent_source_groups` —
  union-find over sources sharing authors/institutions, pass at ≥ 3 independent groups
  (3 papers from one lab = 1 group → yellow) · S7 `corpus_frequency` (OpenAlex search count, context).
- `source_sanity_check` (faithfulness): the term name's stem tokens are all found in the
  title + abstract of at least one of its sources; share ≥ 0.9.
- `source_attestation` (DEFAULT aggregation, calibratable): per-term red = fake link ∨
  retraction ∨ all sources off-field ∨ no arXiv sources; per-term pass = no red ∧ sane ∧
  groups ≥ 3; config level = no reds ∧ share of passing terms ≥ 0.9.

### R5 · Specialization coverage (`r5_specialization.py`)

- `specialty_filled` (deterministic): area is non-empty.
- `area_term_consistency`: a term's field = the modal field of its sources (OpenAlex
  `primary_topic.field`, falling back to an arXiv-archive → field mapping); share of terms whose
  field == the expected one (`specialty.field`) ≥ 0.70.
- `area_concentration`: the modal field across all terms == expected ∧ modal_share ≥ 0.70 —
  catches a "smeared" config even when consistency passes.

### R6 · Link strength (`r6_link_strength.py`) — legacy

The old spec (sim_in / sim_margin / pmi); all metrics pending (requires embedder), not in
`required_gates`. Awaits the redesign per the 2026-06-11 revision: cluster_cohesion +
label_fidelity + term↔term NPMI in the space of SOURCE abstracts (similarity to a synthetic
description is generator self-consistency, not a gate).

### R7 · Reproducibility (`r7_reproducibility.py`, meta — about the validator itself)

- `determinism_flags`: every metric declares deterministic/requires — the non-deterministic
  parts (live APIs, LLMs) are isolated and labeled.
- `provenance`: config_id/version, acceptance version, seed, embedder, OpenAlex snapshot — in every report.
- `reproducibility_check`: two independent validate() runs; the diff of deterministic values must be 0.
- Automation/e2e are covered by the CLI (`run.py`, exit 0 accept / 1 reject / 2 pending) and the test contract.

### Gate layer (`acceptance.py`)

Maps the R1–R7 report onto flat gates (`R2_modeN` = precondition ∧ exists) and adds a
behavioral check with the real JS generator: `assembly_normal/advanced` = assembles ∧
reproducible ∧ board complete ∧ "one concept — one category". Default required:
R1_volume, R2_mode1, R3_morph_leak, R5_specialty + both assemblies.
