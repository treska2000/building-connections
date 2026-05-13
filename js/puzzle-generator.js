import { CONCEPT_DEFINITIONS, CATEGORY_TEMPLATES, CONFIG } from './config.js';

const DIFFICULTIES = ['easy', 'medium', 'hard', 'harder'];

/** Seeded RNG (mulberry32) for deterministic puzzle from a seed string. */
function seededRandom(seedStr) {
    let h = 0;
    for (let i = 0; i < seedStr.length; i++) {
        h = Math.imul(31, h) + seedStr.charCodeAt(i) | 0;
    }
    return function next() {
        h = Math.imul(h ^ (h >>> 15), h | 0);
        h = Math.imul(h ^ (h >>> 7), h | 0);
        h = (h ^ (h >>> 13)) >>> 0;
        return h / 4294967296;
    };
}

class PuzzleGenerator {
    /**
     * @param {'normal'|'advanced'} mode - 'normal' = 4 categories (16 tiles), 'advanced' = 3 categories (12 + 4 decoys)
     * @param {string|null} seed - If provided, same seed produces same puzzle (for multiplayer rooms).
     * @param {Array|null} customTemplates - Optional custom category templates array. If null, uses default CATEGORY_TEMPLATES.
     */
    static generatePuzzle(mode = 'normal', seed = null, customTemplates = null) {
        const templates = customTemplates || CATEGORY_TEMPLATES;
        if (!Array.isArray(templates) || templates.length === 0) {
            throw new Error('Cannot generate puzzle: category templates not loaded. Check that category-templates-new.json is deployed.');
        }

        const rand = seed ? seededRandom(seed) : () => Math.random();
        const numCategories = mode === 'normal'
            ? CONFIG.TOTAL_CATEGORIES_NORMAL
            : CONFIG.TOTAL_CATEGORIES_ADVANCED;

        // Build map tag -> list of term names
        const tagMap = new Map();
        templates.forEach(entry => {
            if (!entry || !entry.name || !Array.isArray(entry.tags)) return;
            entry.tags.forEach(tag => {
                if (!tag) return;
                const key = String(tag);
                if (!tagMap.has(key)) tagMap.set(key, []);
                tagMap.get(key).push(entry.name);
            });
        });

        // Candidate tags must have at least 4 terms
        const candidateTags = Array.from(tagMap.entries())
            .filter(([, terms]) => terms.length >= 4)
            .map(([tag, terms]) => ({ tag, terms: [...terms] }));

        if (candidateTags.length < numCategories) {
            throw new Error('Not enough tags with at least 4 terms to generate a puzzle.');
        }

        // Shuffle tags and pick the needed number of categories
        candidateTags.sort(() => rand() - 0.5);
        const selected = candidateTags.slice(0, numCategories);

        const categories = {};
        const allPuzzleConcepts = new Set();

        selected.forEach((entry, i) => {
            const d = DIFFICULTIES[i];
            const terms = [...entry.terms].sort(() => rand() - 0.5).slice(0, CONFIG.CONCEPTS_PER_GROUP);
            terms.forEach(name => allPuzzleConcepts.add(name));
            categories[d] = {
                name: entry.tag,
                members: terms,
                difficulty: d
            };
        });

        // Fill remaining slots with decoy concepts (from CONCEPT_DEFINITIONS, excluding already used)
        const allConcepts = Object.keys(CONCEPT_DEFINITIONS);
        const neededConcepts = 16 - allPuzzleConcepts.size;
        if (neededConcepts > 0) {
            const availableDecoys = allConcepts.filter(concept => !allPuzzleConcepts.has(concept));
            const decoys = availableDecoys.sort(() => rand() - 0.5).slice(0, neededConcepts);
            decoys.forEach(decoy => allPuzzleConcepts.add(decoy));
        }

        const boardConcepts = Array.from(allPuzzleConcepts).sort(() => rand() - 0.5);

        return {
            board: boardConcepts,
            categories,
            numCategories
        };
    }
}

export default PuzzleGenerator;