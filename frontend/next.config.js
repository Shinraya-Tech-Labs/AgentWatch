/** @type {import('next').NextConfig} */

// Two builds come from this app:
//
// - the server build (default, `output: 'standalone'`) for Docker Compose. rewrites() would
//   bake the destination URL at BUILD time, so the API routes under pages/api read
//   AGENTWATCH_API_URL at REQUEST time instead.
// - the static build (PANOPTIS_EXPORT=1, `output: 'export'`) that ships inside the Python
//   package and is served by the API itself (`panoptis up`). The browser talks to the API on
//   the same origin, so the proxy routes are not needed: pageExtensions ['tsx'] leaves out
//   pages/api (every route there is a .ts file, every page a .tsx file).
const isExport = process.env.PANOPTIS_EXPORT === '1';

const securityHeaders = [
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
];

const nextConfig = isExport
  ? {
      reactStrictMode: true,
      output: 'export',
      pageExtensions: ['tsx'],
      // /security/ -> security/index.html, which any static file server resolves
      trailingSlash: true,
      images: { unoptimized: true },
    }
  : {
      reactStrictMode: true,
      output: 'standalone',
      async headers() {
        return [{ source: '/:path*', headers: securityHeaders }];
      },
    };

module.exports = nextConfig;
