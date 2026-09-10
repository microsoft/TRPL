# syntax=docker/dockerfile:1.7
FROM oven/bun:1.3.14-alpine@sha256:5acc90a93e91ff07bf72aa90a7c9f0fa189765aec90b47bdbf2152d2196383c0 AS dependencies
WORKDIR /app

COPY frontend/package.json frontend/bun.lock frontend/bunfig.toml ./
RUN mkdir -p public
RUN --mount=type=secret,id=npm_registry_url \
    bun install --frozen-lockfile --registry "$(cat /run/secrets/npm_registry_url)"

FROM dependencies AS build
COPY frontend/ ./
ENV PYTHON_RAG_API_URL=http://layer3-backend:8000 \
    RAG_API_KEY=build-only-placeholder
RUN bun run build

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]
