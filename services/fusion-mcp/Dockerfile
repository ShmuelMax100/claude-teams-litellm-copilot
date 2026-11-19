# syntax=docker/dockerfile:1
# Official Python and Copilot release artifacts are pinned and verified.
FROM python:3.11-slim@sha256:ef442c44cde6d7aec39ae63dad1ced44fa7290790d68c028686cdd2e31a76a95 AS cli
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN case "$TARGETARCH" in \
      amd64) arch=x64; digest=b91a96f2257297cfb67ed6784a2a658609a6b8c2ee2ded18af9c9abe8cc790ae ;; \
      arm64) arch=arm64; digest=a204154d4522a7c3aa73a65ba598e432f6aafe37d5fa31cf853163a27d98018e ;; \
      *) exit 1 ;; \
    esac \
    && curl --fail --location --retry 3 --output /tmp/copilot.tar.gz \
       "https://github.com/github/copilot-cli/releases/download/v1.0.114/copilot-linux-${arch}.tar.gz" \
    && echo "$digest  /tmp/copilot.tar.gz" | sha256sum --check - \
    && tar -xzf /tmp/copilot.tar.gz -C /usr/local/bin copilot \
    && chmod 755 /usr/local/bin/copilot

FROM python:3.11-slim@sha256:ef442c44cde6d7aec39ae63dad1ced44fa7290790d68c028686cdd2e31a76a95
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FUSION_PORT=8080
WORKDIR /app
COPY requirements.lock /app/requirements.lock
# Optional build-only pip configuration supports corporate package mirrors.
# It is never copied into an image layer; pinned hashes are always required.
RUN --mount=type=secret,id=pip_config,target=/etc/pip.conf \
    pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
    && groupadd --gid 10001 fusion && useradd --uid 10001 --gid fusion --no-create-home fusion
COPY --from=cli /usr/local/bin/copilot /usr/local/bin/copilot
COPY src/ /app/
USER 10001:10001
EXPOSE 8080
# Liveness stays healthy when capability readiness intentionally fails closed.
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"
CMD ["python", "/app/server.py"]
