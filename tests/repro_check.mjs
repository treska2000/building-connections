// Metric 1 harness: drives the REAL puzzle generator (js/puzzle-generator.js)
// to check that a puzzle (a) assembles, (b) is reproducible from a seed,
// (c) keeps every concept in at most one solution category.
//
// Usage: node repro_check.mjs <configPath> <mode> <seed>
//   mode = normal | advanced
// Prints a single JSON object to stdout.
//
// Why a Node harness instead of re-implementing in Python: reproducibility is a
// property of the actual shipped code + JS engine. We must test the real thing.
// NOTE: the generator shuffles with `arr.sort(() => rand()-0.5)`, which is
// deterministic *within one JS engine* but can differ across engines
// (V8 / SpiderMonkey / JSC). So "reproducible: true" here means "stable in V8
// (Chrome, Edge, Node)". Cross-browser identity is NOT guaranteed — see report.

import fs from 'fs';
import crypto from 'crypto';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

const [, , configPath, mode = 'normal', seed = 'golden-seed-1'] = process.argv;

function pickLang(tagsOrName, lang = 'en') {
  if (tagsOrName && typeof tagsOrName === 'object' && !Array.isArray(tagsOrName)) {
    return tagsOrName[lang] ?? Object.values(tagsOrName)[0];
  }
  return tagsOrName;
}

// Normalise both config formats (flat {name,description,tags[]} and multilang
// {name:{en,ru},...}) into the flat shape the generator expects.
function loadTemplates(p, lang = 'en') {
  const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
  return raw
    .map(e => {
      // legacy {name, members[]} format -> expand to tagged terms is not this shape;
      // here we only handle term-entry formats.
      const name = pickLang(e.name, lang);
      const description = pickLang(e.description, lang) ?? '';
      let tags = e.tags ? pickLang(e.tags, lang) : [];
      if (!Array.isArray(tags)) tags = tags ? [tags] : [];
      return name ? { name: String(name), description: String(description), tags } : null;
    })
    .filter(Boolean);
}

const out = { config: configPath, mode, seed };

try {
  const mod = await import(path.join(ROOT, 'js', 'puzzle-generator.js'));
  const PuzzleGenerator = mod.default;
  // CONCEPT_DEFINITIONS is loaded from disk via fetch in the browser; in Node the
  // fetch fails silently, leaving it empty. Advanced mode draws decoys from it, so
  // we populate it from the config under test to mirror real (browser) behaviour.
  const cfg = await import(path.join(ROOT, 'js', 'config.js'));
  const templates = loadTemplates(configPath);
  for (const t of templates) cfg.CONCEPT_DEFINITIONS[t.name] = t.description;

  let a, b, assembleError = null;
  try {
    a = PuzzleGenerator.generatePuzzle(mode, seed, templates);
    b = PuzzleGenerator.generatePuzzle(mode, seed, templates);
  } catch (e) {
    assembleError = e.message;
  }

  out.assembles = !assembleError;
  out.assembleError = assembleError;

  if (a) {
    const reproducible = JSON.stringify(a) === JSON.stringify(b);
    const boardLen = a.board.length;
    const expectedLen = 16; // both modes should fill the 16-tile board
    // each board concept must appear in at most one solution category
    const memberCounts = {};
    for (const cat of Object.values(a.categories)) {
      for (const m of cat.members) memberCounts[m] = (memberCounts[m] || 0) + 1;
    }
    const overlaps = Object.entries(memberCounts).filter(([, n]) => n > 1).map(([k]) => k);

    out.reproducible = reproducible;
    out.boardLen = boardLen;
    out.boardComplete = boardLen === expectedLen;
    out.numCategories = a.numCategories;
    out.oneConceptOneCategory = overlaps.length === 0;
    out.overlappingConcepts = overlaps;
    out.golden_hash = crypto.createHash('sha256').update(JSON.stringify(a)).digest('hex').slice(0, 16);
    out.puzzle = a;
  }
} catch (e) {
  out.harnessError = e.message;
}

process.stdout.write(JSON.stringify(out));
