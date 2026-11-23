# syntax=docker/dockerfile:1
# web: Next.js (App Router). `next start` on 0.0.0.0:$PORT (3000). Same image for local and demo; the
# browser only calls relative /api/* paths, so no build-time client config is needed (see docs/deploy.md).

FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# next.config.ts reads API_ORIGIN when `next build` writes the rewrite table, so it must exist at build time too.
ARG API_ORIGIN=http://api:8000
ENV API_ORIGIN=${API_ORIGIN}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    API_ORIGIN=http://api:8000
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/.next ./.next
COPY --from=build --chown=node:node /app/package.json /app/next.config.ts ./
USER node
EXPOSE 3000
CMD ["npm", "start"]
