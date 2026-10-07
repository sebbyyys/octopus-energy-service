# Supervisor builds with THIS folder as context, not the repository root.
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/service
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
# Keep pin and checksum together. Fetch the verified backend, not a moving branch.
RUN python -c 'import urllib.request; urllib.request.urlretrieve("https://codeload.github.com/sebbyyys/octopus-energy-service/tar.gz/c32e8fcd83e90b17a8bb140a23162ab896b57f6b", "/source.tar.gz")' \
    && printf '%s  %s\n' '7ae95a19f9e0593a1332f9992a467525beda50e73d2706aeadaf63e60af68bc3' '/source.tar.gz' | sha256sum --check --strict \
    && tar -xzf /source.tar.gz --strip-components=1 -C /opt/service \
    && rm /source.tar.gz \
    && uv sync --locked --no-dev --no-editable

FROM python:3.13-slim
ARG BUILD_VERSION=0.2.0
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}" \
      org.opencontainers.image.source="https://github.com/sebbyyys/octopus-energy-service" \
      org.opencontainers.image.revision="c32e8fcd83e90b17a8bb140a23162ab896b57f6b"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/service/.venv/bin:$PATH"
WORKDIR /opt/service
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 octopus \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin octopus
COPY --from=build /opt/service/.venv /opt/service/.venv
COPY entrypoint.py /entrypoint.py
EXPOSE 8080
# Root only initializes /data; entrypoint irreversibly drops UID/GID before exec.
USER root
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/service/.venv/bin/python", "/entrypoint.py"]
