/**
 * Runtime reverse proxy for /api/v3/* (AgentWatch v3 API).
 *
 * Reads AGENTWATCH_API_URL at request time (not build time). When AGENTWATCH_API_KEY is
 * set in the frontend's server environment it is attached as X-Api-Key, so the browser
 * never holds the key.
 */
import type { NextApiRequest, NextApiResponse } from 'next';

const API_BASE = (process.env.AGENTWATCH_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const rawSegments = Array.isArray(req.query.path) ? req.query.path : [req.query.path ?? ''];
  if (rawSegments.some((s) => String(s).includes('..') || String(s).startsWith('/'))) {
    return res.status(400).json({ error: 'invalid_path', detail: 'Path traversal is not permitted' });
  }
  const path = rawSegments.map((s) => encodeURIComponent(String(s))).join('/');
  const { path: _drop, ...rest } = req.query;
  const qs = new URLSearchParams(
    Object.entries(rest).flatMap(([k, v]) =>
      Array.isArray(v) ? v.map((val) => [k, val]) : [[k, String(v)]],
    ),
  ).toString();
  const upstream = `${API_BASE}/api/v3/${path}${qs ? `?${qs}` : ''}`;
  const isRead = req.method === 'GET' || req.method === 'HEAD';
  const headers: Record<string, string> = {};
  if (!isRead) headers['content-type'] = 'application/json';
  const key = process.env.AGENTWATCH_API_KEY ?? (req.headers['x-api-key'] as string | undefined);
  if (key) headers['x-api-key'] = key;
  try {
    const upstreamRes = await fetch(upstream, {
      method: req.method ?? 'GET',
      headers,
      body: isRead ? undefined : JSON.stringify(req.body ?? {}),
      // replay/branch requests re-execute programs and can take a while
      signal: AbortSignal.timeout(180_000),
    });
    res.status(upstreamRes.status);
    const ct = upstreamRes.headers.get('content-type');
    if (ct) res.setHeader('content-type', ct);
    res.send(await upstreamRes.text());
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[proxy-v3] ${req.method} ${req.url} →`, message);
    res
      .status(502)
      .json({ error: 'upstream_unavailable', detail: 'The AgentWatch v3 API is unavailable' });
  }
}
