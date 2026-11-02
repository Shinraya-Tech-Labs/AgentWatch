/** @type {import('next').NextConfig} */

// NOTE: rewrites() bake the destination URL at BUILD time, so any env var
// read here becomes a static string in routes-manifest.json.  We use a
// Next.js API route (/pages/api/v1/[...path].ts) instead, which reads
// AGENTWATCH_API_URL at REQUEST time from the live container environment.

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
};

module.exports = nextConfig;
