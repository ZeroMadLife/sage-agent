ARG POSTGRES_IMAGE=pgvector/pgvector:pg17
FROM ${POSTGRES_IMAGE}

ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && case "${TARGETARCH}" in \
         amd64) archive="pg-textsearch-v1.3.1-pg17-amd64.zip" ;; \
         arm64) archive="pg-textsearch-v1.3.1-pg17-arm64.zip" ;; \
         *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl --fail --silent --show-error --location --retry 5 \
         "https://github.com/timescale/pg_textsearch/releases/download/v1.3.1/${archive}" \
         --output /tmp/pg_textsearch.zip \
    && unzip -q /tmp/pg_textsearch.zip -d /tmp/pg_textsearch \
    && apt-get install -y --no-install-recommends /tmp/pg_textsearch/*.deb \
    && rm -f /tmp/pg_textsearch.zip \
    && rm -rf /tmp/pg_textsearch /var/lib/apt/lists/*

CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
