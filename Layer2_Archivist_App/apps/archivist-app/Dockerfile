# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS build

WORKDIR /app

COPY apps/archivist-app/package.json apps/archivist-app/package-lock.json ./
RUN --mount=type=secret,id=npm_registry_url \
    npm config set registry "$(cat /run/secrets/npm_registry_url)" \
    && npm ci

COPY apps/archivist-app ./
ENV VITE_BACKEND_API_ENDPOINT=""
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

COPY apps/archivist-app/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
