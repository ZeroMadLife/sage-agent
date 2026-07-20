ARG SAGE_DOCKER_REGISTRY=docker.io
ARG SAGE_IMAGE_TAG=development
FROM ${SAGE_DOCKER_REGISTRY}/library/node:24.13.0-alpine AS build

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build:public

FROM ${SAGE_DOCKER_REGISTRY}/library/caddy:2.10.2-alpine
ARG SAGE_IMAGE_TAG

LABEL org.opencontainers.image.revision=${SAGE_IMAGE_TAG}

RUN setcap -r /usr/bin/caddy \
    && test -z "$(getcap /usr/bin/caddy)"

COPY infra/proxy/Caddyfile.public /etc/caddy/Caddyfile
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
COPY --from=build /src/dist-public /srv

USER 65532:65532

EXPOSE 8081
