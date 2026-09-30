# Sandbox image for the team profile's terminal (terminal.backend: docker).
# Runs as the host uid via docker_run_as_host_user; root fs is --read-only, so
# pip/npm installs land in $HOME (/root) or /workspace. Both are per-session tmpfs mounts,
# and Docker gives a tmpfs the mode of the image dir it covers, hence the chmod 0777 below.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

# Check-Date=false: this host's clock ran ~5h behind the Debian mirror on 2026-09-30.
RUN apt-get -o Acquire::Check-Date=false update \
 && apt-get install -y --no-install-recommends git nodejs npm curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pandas==2.2.3 openpyxl==3.1.5 numpy==2.2.1 xlrd==2.0.1 tabulate==0.9.0 \
 && useradd --uid 1000 --home-dir /root --no-create-home --shell /bin/bash agent \
 && mkdir -p /workspace && chmod 0777 /root /workspace

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_USER=1 \
    PYTHONUSERBASE=/root/.local \
    PATH=/root/.local/bin:$PATH \
    NPM_CONFIG_PREFIX=/root/.npm-global \
    NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /workspace
