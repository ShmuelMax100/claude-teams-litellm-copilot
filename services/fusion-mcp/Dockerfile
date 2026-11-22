# syntax=docker/dockerfile:1
FROM python:3.11-slim@sha256:ef442c44cde6d7aec39ae63dad1ced44fa7290790d68c028686cdd2e31a76a95 AS sdk
WORKDIR /build
COPY requirements-build.lock /build/
COPY scripts/build_sdk.py scripts/stage_runtime.py /build/
RUN --mount=type=secret,id=pip_config,target=/etc/pip.conf \
    pip install --no-cache-dir --require-hashes -r requirements-build.lock
# Source-built official v2.0.1, with pinned commit/archive hash and release metadata.
# This avoids a dependency on wheel CDN availability without substituting a mock SDK.
RUN python /build/build_sdk.py /wheels
ARG TARGETARCH
RUN python /build/stage_runtime.py "$TARGETARCH" /opt/copilot-runtime

FROM python:3.11-slim@sha256:ef442c44cde6d7aec39ae63dad1ced44fa7290790d68c028686cdd2e31a76a95
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FUSION_PORT=8080 \
    FUSION_RUNTIME_PATH=/opt/copilot-runtime/copilot-runtime COPILOT_SKIP_CLI_DOWNLOAD=1
WORKDIR /app
COPY --from=sdk /wheels /wheels
COPY requirements.lock /app/requirements.lock
# The source build is verified against its pinned archive; remaining dependencies
# use published distribution hashes. Installing it first satisfies the SDK pin.
RUN --mount=type=secret,id=pip_config,target=/etc/pip.conf \
    pip install --no-cache-dir --no-deps /wheels/*.whl \
    && pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
    && python -c "from copilot._cli_version import CLI_VERSION; from importlib.metadata import version; assert CLI_VERSION == '1.0.114'; assert version('microsoft-copilot-sdk') == '2.0.1'" \
    && cp /wheels/sdk-provenance.json /app/sdk-provenance.json && rm -rf /wheels \
    && groupadd --gid 10001 fusion && useradd --uid 10001 --gid fusion --no-create-home fusion
COPY --from=sdk /opt/copilot-runtime /opt/copilot-runtime
COPY src/ /app/
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"
CMD ["python", "/app/server.py"]
