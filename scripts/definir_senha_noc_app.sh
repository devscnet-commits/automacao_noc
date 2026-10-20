#!/bin/sh
# Define (ou troca) a senha do papel noc_app a partir de secrets/noc_app_password.txt.
# Rode no host, na raiz do projeto, com o serviço db no ar. Pode rodar quantas vezes quiser.
# A senha vai pela entrada padrão do psql: nunca aparece na linha de comando nem em log.
set -eu
cd "$(dirname "$0")/.."

SENHA=$(cat secrets/noc_app_password.txt)
# A senha é interpolada no psql abaixo: só aceitamos letras e números (openssl rand -hex).
case "$SENHA" in
    "" | *[!A-Za-z0-9]*)
        echo "secrets/noc_app_password.txt vazio ou com caracteres fora de [A-Za-z0-9]" >&2
        exit 1
        ;;
esac

docker compose exec -T db psql -U noc -d noc -q -v ON_ERROR_STOP=1 <<SQL
-- Sem isto, um ALTER ROLE que falhe é gravado no log do Postgres com a senha.
SET log_min_error_statement = panic;
SELECT 'CREATE ROLE noc_app NOLOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'noc_app') \gexec
\set senha '$SENHA'
ALTER ROLE noc_app WITH LOGIN PASSWORD :'senha';
SQL

echo "Senha do noc_app definida."
