# One image for migrations, the API, and the job worker; compose picks the command.
FROM ghcr.io/astral-sh/uv:0.11.14@sha256:1025398289b62de8269e70c45b91ffa37c373f38118d7da036fb8bb8efc85d97 AS uv

# Build the virtual environment here, so uv and its cache never reach the final image.
FROM python:3.13-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
# Dependencies first, so source edits do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY LICENSE ./
COPY shelfsight_api shelfsight_api
COPY benchmark_tool benchmark_tool
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26

# The runtime never installs packages, and pip vendors libraries with known fixes.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes pip

COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY alembic.ini ./
COPY migrations migrations

# A new named volume copies this ownership, so the API can write media without root.
RUN useradd --system --uid 10001 --home-dir /app cvsight \
    && mkdir /data \
    && chown cvsight /data
USER cvsight

ENV SHELFSIGHT_API_HOST=0.0.0.0 \
    SHELFSIGHT_MEDIA_ROOT=/data/media \
    SHELFSIGHT_IMPORT_ROOT=/data/import
EXPOSE 8000
CMD ["python", "-m", "shelfsight_api"]
