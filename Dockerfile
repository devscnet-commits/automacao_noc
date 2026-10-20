# Debian 12 (bookworm), o mesmo sistema da VM.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# UID/GID fixos: o secret da senha do banco, no host, tem grupo 10001 e modo 640 (veja o README).
RUN groupadd --system --gid 10001 noc \
    && useradd --system --uid 10001 --gid noc --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin noc

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install .

# O código fica com dono root: o usuário da aplicação só lê.
USER noc

# Sem CMD por enquanto: ainda não existe ponto de entrada da aplicação.
