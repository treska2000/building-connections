# configs/

- `game/` — банк терминов ЖИВОЙ игры (плоский и мультиязычный форматы; `category-templates-new.json`
  загружается игрой в рантайме — пути зашиты в `js/config.js`, после переноса обнови деплой).
  / The LIVE game's term bank (flat and multilang formats; `category-templates-new.json` is fetched
  at runtime — paths live in `js/config.js`; redeploy after moving anything here).
- `v1/` — конфиги пайплайна валидации/сборки (схема `v1/schema.json`; `from-generator/` — выходы
  генерации конфигов, адаптированные в v1; `_legacy_*.json` — старые банки, сконвертированные в v1).
  / Validation/assembly pipeline configs (schema `v1/schema.json`; `from-generator/` — config-generation
  outputs adapted to v1; `_legacy_*.json` — old banks converted to v1).
