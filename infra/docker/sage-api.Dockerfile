ARG SAGE_DOCKER_REGISTRY=docker.io
FROM ${SAGE_DOCKER_REGISTRY}/library/docker:27.5.1-cli AS docker-cli

FROM ${SAGE_DOCKER_REGISTRY}/library/python:3.12.13-slim-bookworm

ARG SAGE_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG SAGE_DEBIAN_MIRROR=https://mirrors.aliyun.com/debian
ARG SAGE_DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${SAGE_PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app

COPY requirements.txt ./
COPY packages ./packages
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${SAGE_DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${SAGE_DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::ForceIPv4=true update \
    && apt-get -o Acquire::ForceIPv4=true install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

COPY agents ./agents
COPY api ./api
COPY config ./config
COPY core ./core
COPY data ./data
COPY db ./db
COPY mcp_servers ./mcp_servers
COPY models ./models
COPY infra/docker/sage-api-entrypoint.sh /usr/local/bin/sage-api-entrypoint
RUN chmod 0755 /usr/local/bin/sage-api-entrypoint

USER 0:0

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/sage-api-entrypoint"]
