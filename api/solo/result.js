import { getDb } from '../../lib/db.js';

function parseBody(req) {
  if (req.body == null) return {};
  return typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') {
    res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    return res.status(200).end();
  }
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });
  try {
    const body = parseBody(req);
    const name = (body.playerName && String(body.playerName).trim()) || 'Anonymous';
    const mode = body.mode === 'advanced' ? 'advanced' : 'normal';
    const created_at = Date.now();
    const db = await getDb();
    await db.execute({
      sql: 'INSERT INTO solo_results (player_name, score, time_seconds, mode, created_at) VALUES (?, ?, ?, ?, ?)',
      args: [name.substring(0, 50), Number(body.score) || 0, Number(body.timeSeconds) || 0, mode, created_at],
    });
    res.status(200).json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: String(e.message) });
  }
}
