ARG SAGE_DOCKER_REGISTRY=docker.io
FROM ${SAGE_DOCKER_REGISTRY}/library/node:24.13.0-alpine AS build

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN VITE_CLOUD_AUTH_REQUIRED=true npm run build

FROM ${SAGE_DOCKER_REGISTRY}/library/caddy:2.10.2-alpine

COPY infra/proxy/Caddyfile.private /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv

EXPOSE 8080
