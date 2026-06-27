# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install third-party dependencies in a layer keyed only on project metadata, so routine
# source changes don't reinstall pandas/numpy/etc. (very slow on the free-tier VM). A minimal
# package stub lets the resolver read dependencies from pyproject without the real source.
# BuildKit keeps the pip download/build cache outside the final image, so dependency-layer
# rebuilds get faster without bloating runtime layers on the small persistent disk.
COPY pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && mkdir -p src/poma \
    && : > src/poma/__init__.py \
    && pip install . \
    && rm -rf src build ./*.egg-info

# Install the real package on top, without re-resolving the cached dependency layer.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --force-reinstall .

ARG APP_UID=1000
ARG APP_GID=1000

RUN groupadd --gid "${APP_GID}" appuser \
    && useradd \
      --uid "${APP_UID}" \
      --gid "${APP_GID}" \
      --create-home \
      --shell /usr/sbin/nologin \
      appuser
USER appuser

ENTRYPOINT ["python", "-m", "poma.cli"]
CMD ["monitor"]
