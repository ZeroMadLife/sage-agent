# syntax=docker/dockerfile:1

ARG SAGE_DOCKER_REGISTRY=docker.io
FROM ${SAGE_DOCKER_REGISTRY}/library/python:3.12.13-slim

ARG SAGE_PIP_INDEX_URL=https://pypi.org/simple/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_INDEX_URL=${SAGE_PIP_INDEX_URL} \
    PIP_RETRIES=10 \
    SAGE_PUBLIC_PACKAGE=/app/data/public/sage-public-v1.json

WORKDIR /app
COPY requirements-public-agent.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --requirement requirements-public-agent.txt \
    && groupadd --gid 65532 sage \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin sage

ARG SAGE_IMAGE_TAG=development
LABEL org.opencontainers.image.revision=${SAGE_IMAGE_TAG}

COPY public_agent ./public_agent
COPY data/public ./data/public

USER 65532:65532
EXPOSE 8082

CMD ["uvicorn", "public_agent.app:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "1", "--no-access-log"]
