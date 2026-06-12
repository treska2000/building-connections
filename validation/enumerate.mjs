// Перебор пазлов: гоняет НАСТОЯЩИЙ генератор по seeds = "0".."N-1" и собирает
// различные пазлы в трёх канонических представлениях:
//   contentSig  — набор категорий {набор членов}; игнорит порядок, сложность, доску
//   labeledSig  — сложность -> набор членов; игнорит только порядок доски/тайлов
//   exactHash   — точный выход (доска + всё), как видит ученик
// Usage: node enumerate.mjs <configPath> <mode> <nSeeds>
import fs from 'fs';
import crypto from 'crypto';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const [, , configPath, mode = 'normal', nSeedsStr = '20000'] = process.argv;
const nSeeds = parseInt(nSeedsStr, 10);

function pick(v, lang = 'en') {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v[lang] ?? Object.values(v)[0];
  return v;
}
function loadTemplates(p) {
  return JSON.parse(fs.readFileSync(p, 'utf8')).map(e => {
    const name = pick(e.name); if (name == null) return null;
    let tags = e.tags ? pick(e.tags) : [];
    if (!Array.isArray(tags)) tags = tags ? [tags] : [];
    return { name: String(name), description: String(pick(e.description) ?? ''), tags };
  }).filter(Boolean);
}
const h = s => crypto.createHash('sha256').update(s).digest('hex').slice(0, 16);

const PuzzleGenerator = (await import(path.join(ROOT, 'js', 'puzzle-generator.js'))).default;
const cfg = await import(path.join(ROOT, 'js', 'config.js'));
const templates = loadTemplates(configPath);
for (const t of templates) cfg.CONCEPT_DEFINITIONS[t.name] = t.description;

const content = new Set(), labeled = new Set(), exact = new Set();
const catUsage = new Map();           // какие категории вообще встречались
let lastNewSeed = 0, lastNewLabeledSeed = 0;

for (let i = 0; i < nSeeds; i++) {
  const p = PuzzleGenerator.generatePuzzle(mode, String(i), templates);
  const cats = Object.values(p.categories);
  const contentSig = cats
    .map(c => [...c.members].sort().join('|'))
    .sort().join(' || ');
  const labeledSig = ['easy', 'medium', 'hard', 'harder']
    .map(d => p.categories[d] ? d + ':' + [...p.categories[d].members].sort().join('|') : '')
    .join(' || ');
  const exactHash = h(JSON.stringify(p));
  const before = content.size, beforeL = labeled.size;
  content.add(contentSig); labeled.add(labeledSig); exact.add(exactHash);
  if (content.size > before) lastNewSeed = i;
  if (labeled.size > beforeL) lastNewLabeledSeed = i;
  for (const c of cats) catUsage.set(c.name, (catUsage.get(c.name) || 0) + 1);
}

console.log(JSON.stringify({
  config: path.basename(configPath), mode, nSeeds,
  distinct_content: content.size,      // разные пазлы по СМЫСЛУ (группировка)
  distinct_labeled: labeled.size,      // + привязка сложностей
  distinct_exact: exact.size,          // + порядок доски (что реально видит ученик)
  last_new_content_at_seed: lastNewSeed,
  last_new_labeled_at_seed: lastNewLabeledSeed,
  categories_ever_used: catUsage.size,
}, null, 2));
