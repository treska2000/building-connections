# Validation: весь код валидации конфигов (движок R1–R7 + приёмка + адаптеры)

> This document is bilingual: the full Russian version comes first; the English version follows below.
> Документ двуязычный: сначала полная русская версия, ниже английская.

---

# Русская версия

Независимый слой приёмки конфигов по спеке «Валидация требований конфигов».
НЕ заменяет self-check генерации конфигов (`connections_v2.validate`, 11 publishability-гейтов) —
это вторая, внешняя линия: генерация конфигов проверяет себя, приёмка проверяет её выход.

## Что подаётся на вход

Один JSON-файл с конфигом. Формат указывать не нужно — `loader.load_config`
распознаёт его автоматически по структуре файла. Поддержаны три формата
(три поколения пайплайна, валидатор принимает все):

1. **v1-конфиг** — рабочий формат сборки, файлы в `configs/v1/`. Категории — в поле
   `tags` (в старых файлах оно называется `axes` — тоже читается), у термина — имя,
   список категорий и источники:

   ```json
   {"config_id": "ai-safety-v1",
    "specialty": {"field": "computer science", "subfield": "ai", "area": "ai safety"},
    "tags": [{"name": "Alignment Failures"}, ...],
    "terms": [{"name": "Reward Hacking", "tags": ["Alignment Failures"],
               "sources": [{"url": "https://arxiv.org/abs/2201.xxxxx"}]}, ...]}
   ```

2. **Input-schema** — то же самое, но как его выдаёт экспорт генерации конфигов
   (`connections_v2.export_input`): единственное отличие — `specialty` не объект,
   а одна строка (`"specialty": "diffusion text-to-image..."`).

3. **Нативный v2-пул** — сырой выход генерации конфигов (например,
   `connections-gen/pool.json`), без экспорта. Распознаётся по полю
   `"schema_version": "connections_v2_config_pool"`; категории там — объекты
   с `id`/`label`, у терминов — `home_category` и `candidate_categories`.
   Loader сам приводит его к виду v1 (label-имена, home-категория первой).

Практически: можно подать и файл из `configs/v1/`, и `pool.json` прямо из репозитория
генерации конфигов — оба пройдут одним и тем же пайплайном.

## Состав папки

- **движок R1–R7**: `loader`, `textutil`, `solver` (CP-SAT: mode-1/2/3 + trap_quality),
  `r1_volume` … `r7_reproducibility`, `enrich` (arXiv+OpenAlex, live+кэш+offline),
  `report`, `run` (validate/validate_full + CLI), `schema_check`, `thresholds.yaml` (пороги, required_gates);
- **приёмка**: `acceptance.py` — единая точка вердикта (metric gates + assembly gates),
  `puzzle_assembly.py` + `repro_check.mjs` / `enumerate.mjs` (Node-обвязка реального генератора);
- **адаптеры**: `rich_to_v1.py`, `legacy_to_v1.py`, `from_generator.py` (E2E: генерация конфигов → вердикт);
- **наследие**: `puzzle_eval.py` + `golden.json` — оценка банка терминов ЖИВОЙ игры
  (старые форматы configs/game/*.json; M1 сборка/эталон, M2–M4 структура, M5 LLM-judge);
  используется sampling_test.ipynb.

## Запуск

**Единый вход валидации конфига** (максимум проверок при разумном ресурсе:
метрики R1–R7 + обогащение источников + реальная JS-сборка; первый прогон с
обогащением — до минуты, дальше кэш):

```bash
python validation/acceptance.py path/to/config.json --enrich live
# exit 0 = принят, exit 1 = нет; таблица PASS/FAIL по гейтам в stdout
```

Остальные входы — частные случаи того же пайплайна:

```bash
python validation/run.py config.json --out reports/        # только метрики R1-R7 (без Node-сборки), подробный отчёт
python validation/acceptance.py config.json                # без сети: R4/R5 частично pending
python validation/from_generator.py ../connections-gen/seeds --batch   # тот же acceptance для rich-файлов старого формата
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

## Метрики R1–R7: логика реализации

Контракт каждой метрики единый: `{value, pass, deterministic, requires, gameable, counter}`.
Метрика с невыполненным `requires` даёт `pass = None` (PENDING) и не валит вердикт, если её
требование не входит в `required_gates` (дефолт: R1, R2, R3, R5, R7). Пороги — в `thresholds.yaml`.

### R1 · Объём (`r1_volume.py`, детерминированно)

- `is_terms_count_ge_16` — уникальных терминов ≥ 16.
- `is_dedup` — дублей нормализованных имён терминов и дублей рёбер (термин, категория) ровно 0; контр-метрика к искусственному набиванию счётчиков.
- `is_base_categories_count_ge_4` — категорий с ≥ 4 терминами (`min_tag_size`) не меньше 4 (`min_base_tags`).
- `bonus_size3_pool` — справочно: категории ровно по 3 термина как пул естественных обманок; не гейт.
- Вердикт R1 = первые три метрики.

### R2 · Решаемость (`r2_solvability.py` + `solver.py`, CP-SAT, детерминированно)

Примитив `count_partitions(W)`: квадры = все 4-подмножества слов внутри одной категории;
CP-SAT-модель точного покрытия (каждое слово ровно в одной выбранной квадре, всего |W|/4 квадр).
Параметр `cap` — отсечка перечисления: solver останавливается, как только нашёл `cap`
разбивок, то есть функция возвращает min(истинное число, cap), а не точный счётчик.
По умолчанию `cap = 2`, потому что вердикту нужны только три исхода: 0 разбивок — доска
не решается; ровно 1 — пазл однозначен, это и сертифицируем (`is_unique = count == 1`);
2 и больше — у игрока есть второй «правильный» ответ, пазл невалиден, и уже неважно,
разбивок две или двести. Досчитывать после второй — трата ресурса: число разбивок растёт
комбинаторно, а `count_partitions` вызывается сэмплером сотни раз на конфиг. Если
понадобится точная степень неоднозначности (репортить «у кандидата 7 разбивок»), `cap`
вынесен параметром — поднимаешь его и получаешь точный счёт до новой отсечки.
Каждая мода = играбельное предусловие (счёт по множествам) + честная проверка solver'ом:

- **mode-1 (чистый пазл)** — доска 4×4, где каждый термин принадлежит ровно одной
  категории. Проверка устроена по принципу fail-fast: сначала дешёвый структурный
  фильтр (подсчёт множеств, микросекунды), и только если он пройден — дорогая проверка
  солвером (сотни запусков CP-SAT, секунды); третий шаг — бесплатная арифметика поверх
  результатов второго.
  Шаг 1, предусловие: для чистой доски нужны термины, не состоящие больше нигде, —
  solo-термины (|tags| = 1); если категорий с ≥ 4 solo-терминами меньше четырёх, доску
  физически не из чего собрать, solver не запускается. Это счёт по множествам, играбелен
  (категорию можно набить мусором) — поэтому только пред-фильтр.
  Шаг 2, сэмплер: все доски проверить нельзя (комбинаторно много), поэтому случайно
  тянем N = 400 досок (4 категории × 4 термина из всех членов категории; seed = 42 —
  воспроизводимость, R7). Замечание: спека req2 определяет кандидатов через solo-термины,
  код тянет из всех членов — доски, испорченные мультитеговым термином, отсеивает солвер,
  поэтому VPY дополнительно учитывает шум пересечений (расхождение со спекой, решение за
  владельцем)
  и каждую сертифицируем CP-SAT'ом → `exists_valid_puzzle_mode1` (нашлась ли хоть одна
  однозначная) и `good_puzzle_yield_mode1` = VPY — доля однозначных среди проверенных,
  оценка чистоты всего пула (0.9 = почти любая случайная доска валидна; порог 0.70,
  калибруемо).
  Шаг 3, extra (справочно, не гейт): `ceiling` — сколько всего досок теоретически можно
  собрать из конфига. Считается так: для каждой четвёрки категорий перемножаем число
  способов выбрать 4 термина из каждой (из категории в 5 терминов четвёрка выбирается
  5 способами) и складываем результат по всем четвёркам категорий. `effective =
  ceiling × VPY` — экстраполяция выборки на весь пул, то есть оценка числа ХОРОШИХ
  досок. Это ёмкость конфига как банка тестов — ответ на вопрос «не одна игра,
  а множество».
- **mode-2 (пересечения-ловушки).** Ловушка-пересечение — это термин, который
  принадлежит двум категориям сразу (у него ≥ 2 тегов): когда обе категории оказываются
  на одной доске, игрок законно может отнести его к любой из них, но правильная разбивка
  кладёт его только в одну. Это отличие от mode-3: там обманка лишь похожа на чужую
  категорию, членом её не являясь.
  Предусловие: есть хотя бы одна пара категорий, у которых не меньше четырёх общих
  терминов — то есть общего материала достаточно, чтобы пересечение могло образовать
  целую ложную четвёрку. Сэмплер: кандидат-доска обязана содержать хотя бы один термин
  с двумя тегами из выбранных четырёх категорий (живую ловушку), слова не
  переиспользуются; валидность = однозначность по солверу.
- **mode-3 (явные обманки).** Обманка (decoy) — термин, который похож на чужую
  категорию, но её членом не является. В конфиге это размечено полем `decoy_for_tags`:
  «этот термин выглядит как член таких-то категорий». Пример: термин из родной категории
  «Оптимизация» помечен как обманка для категории «Поиск» — на доске он тянется к чужой
  группе, но правильный ответ кладёт его в родную. «Явные» — потому что разметку даёт
  сама генерация конфигов; автоматический вывод обманок (по близости эмбеддингов) — в
  планах, пока pending.
  Предусловие: в конфиге есть не меньше 4 терминов с такой разметкой — иначе материала
  для режима недостаточно.
  Сэмплер: тянем доску как в mode-1, но требуем, чтобы на ней была хотя бы одна живая
  обманка — термин, чья целевая категория тоже присутствует на доске.
  Доска валидна, если выполнены два условия. Первое: правильная разбивка по настоящему
  членству ровно одна (однозначность по солверу). Второе: обманка действительно
  работает — если на минуту «поверить» разметке обманок и засчитать decoy-термин членом
  категории, на которую он похож, на доске появляется хотя бы одна ложная четвёрка,
  которой без обманок не существовало (в коде это `trap_quality` — число таких ложных
  четвёрок, требуется ≥ 1). Доска, где ни одна обманка не создаёт ложной четвёрки,
  формально решаема, но режим «с обманками» на ней не играется.
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
  S1 `resolves_on_arxiv` (red: фейк-ссылка) · S2 `retracted` (red; нет записи ≠ retracted) ·
  S3 `peer_review_venue` (позитив: journal_ref / не-arXiv DOI / regex по comment) ·
  S4 `field_match` (архив primary_category ∈ маппинга поля; red только если чужие ВСЕ источники) ·
  S5 citations (контекст, не red по нулю) · S6 `independent_source_groups` — union-find источников
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

# English version

An independent config-acceptance layer implementing the "Config requirements validation"
spec. It does NOT replace the config generation's own self-check (`connections_v2.validate`,
11 publishability gates) — it is the second, external line: config generation checks itself,
acceptance checks its output.

## What the input is

A single JSON config file. No format flag is needed — `loader.load_config` recognizes
the format automatically from the file's structure. Three formats are supported
(three generations of the pipeline; the validator accepts them all):

1. **v1 config** — the assembly-side working format, files in `configs/v1/`. Categories
   live in the `tags` field (older files call it `axes` — also accepted); each term has
   a name, its categories, and sources:

   ```json
   {"config_id": "ai-safety-v1",
    "specialty": {"field": "computer science", "subfield": "ai", "area": "ai safety"},
    "tags": [{"name": "Alignment Failures"}, ...],
    "terms": [{"name": "Reward Hacking", "tags": ["Alignment Failures"],
               "sources": [{"url": "https://arxiv.org/abs/2201.xxxxx"}]}, ...]}
   ```

2. **Input schema** — the same thing as emitted by the config-generation export
   (`connections_v2.export_input`): the only difference is that `specialty` is a single
   string (`"specialty": "diffusion text-to-image..."`) rather than an object.

3. **Native v2 pool** — the raw config-generation output (e.g.
   `connections-gen/pool.json`), no export step. Recognized by the
   `"schema_version": "connections_v2_config_pool"` field; categories there are objects
   with `id`/`label`, and terms carry `home_category` and `candidate_categories`.
   The loader normalizes it to the v1 shape itself (label names, home category first).

In practice: you can feed either a file from `configs/v1/` or `pool.json` straight from
the config-generation repository — both go through the same pipeline.

## Folder contents

- **R1–R7 engine**: `loader`, `textutil`, `solver` (CP-SAT: modes 1/2/3 + trap_quality),
  `r1_volume` … `r7_reproducibility`, `enrich` (arXiv+OpenAlex, live+cache+offline),
  `report`, `run` (validate/validate_full + CLI), `schema_check`, `thresholds.yaml` (thresholds, required_gates);
- **acceptance**: `acceptance.py` — the single verdict entry point (metric gates + assembly gates),
  `puzzle_assembly.py` + `repro_check.mjs` / `enumerate.mjs` (the Node harness around the real generator);
- **adapters**: `rich_to_v1.py`, `legacy_to_v1.py`, `from_generator.py` (E2E: config generation → verdict);
- **legacy**: `puzzle_eval.py` + `golden.json` — quality evaluation of the LIVE game's term bank
  (old configs/game/*.json formats; M1 assembly/baseline, M2–M4 structure, M5 LLM judge);

## Usage

**The single entry point — "is the config good or not?"** (maximum coverage at a
reasonable cost: R1–R7 metrics + source enrichment + the real JS assembly; the first
enriched run takes up to a minute, cached afterwards):

```bash
python validation/acceptance.py path/to/config.json --enrich live
# exit 0 = accepted, exit 1 = not; a PASS/FAIL gate table on stdout
```

The other entry points are special cases of the same pipeline:

```bash
python validation/run.py config.json --out reports/        # metrics R1-R7 only (no Node assembly), detailed report
python validation/acceptance.py config.json                # offline: R4/R5 partially pending
python validation/from_generator.py ../connections-gen/seeds --batch   # same acceptance for legacy rich files
```

```python
from validation import validate_full, load_acceptance
rep = validate_full("pool.json", load_acceptance("validation/thresholds.yaml"))
```

## Tests (pytest, from the repo root; tests/ holds tests only)

- `tests/test_validation_unit.py` — unit: textutil/solver/loader/R3/R4/R5 on synthetic data and fake clients (no network);
- `tests/test_acceptance.py` — acceptance.py gates (spec scenarios T1–T6, successor of test_config_metrics.py);
- `tests/test_validation_integration.py` — validate() on the repository configs, determinism, required_gates;
- `tests/test_validation_e2e.py` — v2 pool → (export_input | direct) → validation; CLI exit codes; from-generator batch;
- `tests/test_from_generator.py` — rich pool → rich_to_v1 → acceptance.

Dependencies: `validation/requirements.txt` (ortools — R2; nltk — R3 stemming, optional;
PyYAML — thresholds, optional). R4/R5 enrichment: arXiv + OpenAlex, cache in
`validation/enrich_cache/`, env `OPENALEX_MAILTO` / `OPENALEX_API_KEY`. Architecture and
open decision points — `spec_rework_plan_2026-06-11.md` in the research folder.

## Metrics R1–R7: implementation logic

Every metric follows one contract: `{value, pass, deterministic, requires, gameable, counter}`.
A metric whose `requires` is unmet yields `pass = None` (PENDING) and does not block the verdict
unless its requirement is in `required_gates` (default: R1, R2, R3, R5, R7). Thresholds live in `thresholds.yaml`.

### R1 · Volume (`r1_volume.py`, deterministic)

- `is_terms_count_ge_16` — at least 16 unique terms.
- `is_dedup` — exactly zero duplicates of normalized term names and of (term, category) edges; counter-metric against inflating the counts.
- `is_base_categories_count_ge_4` — at least 4 categories (`min_base_tags`) with ≥ 4 terms each (`min_tag_size`).
- `bonus_size3_pool` — informational: categories with exactly 3 terms as a pool of natural decoys; not a gate.
- R1 verdict = the first three metrics.

### R2 · Solvability (`r2_solvability.py` + `solver.py`, CP-SAT, deterministic)

Primitive `count_partitions(W)`: quads = all 4-subsets of words within one category;
a CP-SAT exact-cover model (each word in exactly one chosen quad, |W|/4 quads total).
The `cap` parameter is an enumeration cutoff: the solver stops as soon as it has found
`cap` partitions, so the function returns min(true count, cap) rather than an exact
counter. The default is `cap = 2` because the verdict only needs three outcomes:
0 partitions — the board is unsolvable; exactly 1 — the puzzle is unambiguous, which is
what we certify (`is_unique = count == 1`); 2 or more — the player has a second "correct"
answer, the puzzle is invalid, and it no longer matters whether there are two partitions
or two hundred. Counting past the second one wastes resources: the number of partitions
grows combinatorially, and the sampler calls `count_partitions` hundreds of times per
config. If you ever need the exact degree of ambiguity (reporting "this candidate has
7 partitions"), `cap` is exposed as a parameter — raise it to get an exact count up to
the new cutoff. Each mode = a gameable precondition (set counting)
plus an honest solver check:

- **mode-1 (clean puzzle)** — a 4×4 board where every term belongs to exactly one
  category. The check follows a fail-fast pattern: first a cheap structural filter
  (set counting, microseconds), and only if it passes — the expensive solver check
  (hundreds of CP-SAT runs, seconds); the third step is free arithmetic on top of
  the second one's results.
  Step 1, precondition: a clean board needs terms that belong nowhere else — solo terms
  (|tags| = 1); with fewer than four categories holding ≥ 4 solo terms there is nothing
  to build a board from, and the solver is not invoked. This is plain set counting and
  it is gameable (a category can be stuffed with junk) — hence a pre-filter only.
  Step 2, the sampler: checking every board is infeasible (combinatorially many), so we
  randomly draw N = 400 boards (4 categories × 4 terms drawn from all category members;
  seed = 42 for reproducibility, R7). Note: the req2 spec defines candidates via solo
  terms, while the code draws from all members — boards spoiled by a multi-tag term are
  rejected by the solver, so VPY additionally absorbs overlap noise (a spec deviation,
  owner's call) and certify each with CP-SAT → `exists_valid_puzzle_mode1` (was
  at least one unambiguous board found) and `good_puzzle_yield_mode1` = VPY — the share
  of unambiguous boards among those checked, an estimate of the whole pool's cleanliness
  (0.9 = almost any random board is valid; threshold 0.70, calibratable).
  Step 3, extras (informational, not a gate): `ceiling` — how many boards can
  theoretically be built from the config. Computed as follows: for every quadruple of
  categories, multiply the number of ways to pick 4 terms from each (a 5-term category
  yields 5 such picks), then sum the results over all category quadruples. `effective =
  ceiling × VPY` — the sample extrapolated to the whole pool, i.e. an estimate of the
  number of GOOD boards. This is the config's capacity as a bank of tests — the answer
  to "not one game but many".
- **mode-2 (overlaps as traps).** An overlap trap is a term that belongs to
  two categories at once (it carries ≥ 2 tags): when both categories land on one board,
  the player can legitimately assign it to either, yet the correct partition places it
  in exactly one. This differs from mode-3, where a decoy merely resembles a foreign
  category without being its member.
  Precondition: at least one pair of categories shares no fewer than four terms — i.e.
  there is enough common material for the overlap to form an entire false quad.
  Sampler: a candidate board must contain at least one term holding two tags within the
  chosen four categories (a live trap), no word is reused; validity = solver-certified
  uniqueness.
- **mode-3 (explicit decoys).** A decoy is a term that resembles a foreign category
  without being its member. In the config this is annotated via the `decoy_for_tags`
  field: "this term looks like a member of these categories". Example: a term whose home
  category is "Optimization" is marked as a decoy for "Search" — on the board it pulls
  toward the foreign group, yet the correct answer places it in its home category.
  "Explicit" because the annotation comes from config generation itself; automatic decoy
  inference (via embedding similarity) is planned but pending.
  Precondition: the config carries at least 4 terms with such annotations — otherwise
  there is not enough material for the mode.
  Sampler: draw a board as in mode-1, but require at least one live decoy — a term whose
  target category is also present on the board.
  A board is valid when two conditions hold. First: the correct partition by true
  membership is exactly one (solver-certified uniqueness). Second: the decoy actually
  works — if you momentarily "believe" the decoy annotations and count the decoy term as
  a member of the category it resembles, at least one false quad appears on the board
  that did not exist without the decoys (in code this is `trap_quality` — the number of
  such false quads, required ≥ 1). A board where no decoy creates a false quad is
  formally solvable, but the decoy mode is simply not playable on it.
- R2 verdict = mode-1 (pass_base); modes 2/3 are deepenings: reported, never block the gate.

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
  S1 `resolves_on_arxiv` (red: fake link) · S2 `retracted` (red; no record ≠ retracted) ·
  S3 `peer_review_venue` (positive: journal_ref / non-arXiv DOI / comment regex) ·
  S4 `field_match` (primary_category archive ∈ field mapping; red only when ALL sources are
  off-field) · S5 citations (context, never red for zero) · S6 `independent_source_groups` —
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
