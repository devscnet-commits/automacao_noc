# Debian 12 (bookworm), o mesmo sistema da VM.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system noc \
    && useradd --system --gid noc --home-dir /app --no-create-home --shell /usr/sbin/nologin noc

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install .

# O código fica com dono root: o usuário da aplicação só lê.
USER noc

# Sem CMD por enquanto: ainda não existe ponto de entrada da aplicação.
