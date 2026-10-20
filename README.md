# automacao_noc

Automação da ativação de clientes FTTH do provedor.

Hoje a ativação é manual: o técnico manda os dados em texto livre no Slack, o NOC
confere o sinal óptico no olho, provisiona a ONU no MK ERP, cria a conexão, confere
a sessão PPPoE e configura o roteador do cliente à mão. Este projeto vai automatizar
esse fluxo por etapas.

## Princípios

- **O sistema LÊ as OLTs diretamente (SNMP/SSH), mas NUNCA escreve nelas.**
  Toda escrita em OLT passa pelo MK ERP, que é quem executa os scripts na OLT.
- **Credenciais nunca no código, no banco ou em log.** Tudo vem do ambiente.
- Nenhuma escrita (MK, roteador) acontece sem que os portões de validação passem.
- Monólito simples: um único pacote `app/`.

## Estado atual

Somente a **fundação**: estrutura, banco, configuração, interfaces de leitura das
OLTs, walk SNMP genérico e cliente de leitura do MK. Não há lógica de negócio.

Tudo o que depende de informação ainda não confirmada está marcado no código com
`# PENDENTE: validar com NOC/bancada`. Para listar:

```bash
grep -rn "PENDENTE" app/ db/ .env.example
```

Pendências conhecidas:

- OIDs SNMP de potência, serial, nome e status das ONUs (Huawei e Datacom);
- comandos de CLI para autofind;
- escala dos valores de potência e valor sentinela de ONU offline;
- convenção de nome das ONUs na OLT (caixa/porta);
- release do MK ERP do provedor e quais APIs especiais estão contratadas;
- campos de resposta das APIs MK além dos já documentados;
- versão do SNMP (v2c/v3) e se a credencial é única ou por OLT.

## Estrutura

```
app/
  olt/        leitura das OLTs (drivers Huawei/Datacom, walk SNMP)
  mk/         cliente HTTP do MK ERP
  coleta/     interpretação dos dados lidos (ex.: nome da ONU -> caixa/porta)
db/
  migrations/ migrações SQL (dbmate)
tests/        testes (pytest)
secrets/      senhas usadas pelo docker-compose (fora do git)
```

## Requisitos

- Docker + Docker Compose (PostgreSQL 16 e, mais adiante, a própria aplicação)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) para desenvolvimento local
  (ele baixa o Python 3.12 sozinho; o Debian 12 vem com 3.11)
- [dbmate](https://github.com/amacneil/dbmate) para as migrações

## Configuração

Toda configuração vem de variáveis de ambiente (veja `.env.example`). Padrão de nomes:
prefixo `NOC_`, exceto `DATABASE_URL`, que é o nome fixo lido pelo dbmate.

Credenciais das OLTs: há um padrão global (`NOC_OLT_SNMP_*`, `NOC_OLT_SSH_*`) e cada OLT
pode sobrescrever qualquer campo em `NOC_OLT_SOBRESCRITAS`, um JSON indexado pelo nome
da OLT. A versão do SNMP também é por OLT. SNMPv3 ainda não está implementado.

## Banco de dados

A senha do PostgreSQL fica num arquivo fora do git, montado como secret do Docker.
Use só caracteres hexadecimais para não precisar escapar nada na `DATABASE_URL`:

```bash
mkdir -p secrets
openssl rand -hex 24 > secrets/postgres_password.txt
chmod 600 secrets/postgres_password.txt
cp .env.example .env   # coloque a mesma senha na DATABASE_URL
docker compose up -d db
docker compose ps      # db deve ficar "healthy"
```

A porta 5432 fica ligada só em `127.0.0.1`.

## Desenvolvimento local

```bash
uv venv --python 3.12
. .venv/bin/activate
uv pip install -e ".[dev]"
```

## Comandos

```bash
ruff check .            # lint
ruff format --check .   # formatação
pytest                  # testes
```

## Container da aplicação

O `Dockerfile` (python:3.12-slim, usuário não-root `noc`) já existe, mas o serviço `app`
do compose fica no profile `app` e só sobe quando pedido, porque ainda não há ponto de
entrada:

```bash
docker compose --profile app build app
```
