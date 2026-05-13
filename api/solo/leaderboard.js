import { getDb } from '../../lib/db.js';

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });
  const mode = req.query.mode === 'advanced' ? 'advanced' : 'normal';
  const limit = Math.min(Number(req.query.limit) || 20, 100);
  try {
    const db = await getDb();
    const result = await db.execute({
      sql: 'SELECT player_name AS playerName, score, time_seconds AS timeSeconds, mode, created_at AS createdAt FROM solo_results WHERE mode = ? ORDER BY score DESC, time_seconds ASC LIMIT ?',
      args: [mode, limit],
    });
    res.setHeader('Cache-Control', 'public, max-age=15, s-maxage=15, stale-while-revalidate=60');
    res.status(200).json(result.rows);
  } catch (e) {
    res.status(500).json({ error: String(e.message) });
  }
}
