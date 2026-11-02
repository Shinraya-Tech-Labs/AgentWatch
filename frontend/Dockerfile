# AgentWatch Dashboard (Next.js 16, Pages Router) — multi-stage Docker build.
# Uses Next.js's standalone output mode so the runtime image only carries
# the resolved dependencies + traced assets (typically <150MB total),
# not the entire frontend/node_modules tree.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 10001 -S panoptis \
    && adduser -S -u 10001 -G panoptis panoptis

COPY --from=builder --chown=panoptis:panoptis /app/public ./public
COPY --from=builder --chown=panoptis:panoptis /app/.next/standalone ./
COPY --from=builder --chown=panoptis:panoptis /app/.next/static ./.next/static

USER panoptis
EXPOSE 3000

# 127.0.0.1, not localhost: busybox wget resolves localhost to ::1, while the server listens
# on IPv4 (HOSTNAME=0.0.0.0), so a localhost probe is refused and the container never
# becomes healthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -q -O - http://127.0.0.1:3000/api/health || exit 1

CMD ["node", "server.js"]
