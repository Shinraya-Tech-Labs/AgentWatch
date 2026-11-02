/**
 * Runtime reverse proxy for all /api/v1/* calls.
 *
 * Replaces the next.config.js rewrite, which was evaluated at BUILD time and
 * therefore had AGENTWATCH_API_URL baked in as "http://localhost:8000" (the
 * env var is not present during `docker build`).  This route runs on every
 * request and reads the env var from the live container environment.
 */
import type { NextApiRequest, NextApiResponse } from 'next';

// Reads from the container env at request time — never baked in at build time.
const API_BASE = (process.env.AGENTWATCH_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

// No externalResolver: true — keeping Next.js's default error safety net so
// that any uncaught exception in this handler results in a 500 response rather
// than a silent connection close that Render's proxy reports as 502.
export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  try {
    const segments = Array.isArray(req.query.path) ? req.query.path : [req.query.path ?? ''];
    const path = segments.join('/');

    // Forward query params (except the internal Next.js `path` param)
    const { path: _drop, ...rest } = req.query;
    const qs = new URLSearchParams(
      Object.entries(rest).flatMap(([k, v]) =>
        Array.isArray(v) ? v.map((val) => [k, val]) : [[k, String(v)]],
      ),
    ).toString();

    const upstream = `${API_BASE}/api/v1/${path}${qs ? `?${qs}` : ''}`;

    const isReadMethod = req.method === 'GET' || req.method === 'HEAD';
    const requestBody: string | undefined = isReadMethod ? undefined : JSON.stringify(req.body);

    const forwardHeaders: Record<string, string> = {};
    if (!isReadMethod && requestBody !== undefined) {
      forwardHeaders['content-type'] = 'application/json';
    }
    // the caller's credentials go upstream unchanged: the API decides the tenant
    for (const h of ['authorization', 'x-api-key', 'x-tenant-id']) {
      const v = req.headers[h];
      if (typeof v === 'string' && v) forwardHeaders[h] = v;
    }

    const upstreamRes = await fetch(upstream, {
      method: req.method ?? 'GET',
      headers: forwardHeaders,
      body: requestBody,
      signal: AbortSignal.timeout(30_000),
    });

    res.status(upstreamRes.status);

    const ct = upstreamRes.headers.get('content-type');
    if (ct) res.setHeader('content-type', ct);

    const payload = await upstreamRes.text();
    res.send(payload);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[proxy] ${req.method} ${req.url} →`, message);
    // no internal address or error text in the response
    res
      .status(502)
      .json({ error: 'upstream_unavailable', detail: 'The AgentWatch API is unavailable' });
  }
}
