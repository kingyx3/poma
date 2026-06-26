FROM python:3.11-slim AS runtime

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

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
