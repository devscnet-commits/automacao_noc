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

## Decisões

- **Aplicação assíncrona (asyncio).** O walk SNMP (pysnmp 7) e o pool do banco
  (`AsyncConnectionPool`) são assíncronos, e o cliente MK também será. Consequências:
  - o agendador é o `AsyncIOScheduler` do APScheduler 3, rodando no mesmo laço de eventos;
  - chamadas síncronas, como Netmiko/SSH, rodam via `asyncio.to_thread`, nunca direto numa
    corrotina (travariam o laço inteiro enquanto esperam a OLT).
- **A aplicação conecta como `noc_app`, nunca como `noc`**, e não lê a `DATABASE_URL`
  (detalhes em Configuração).
- **Variável `NOC_` desconhecida é erro**: a aplicação não sobe se encontrar no ambiente ou
  no `.env` uma variável com esse prefixo que não reconhece (pega erro de digitação).
  Variáveis de outros prefixos (`DATABASE_URL`, `DBMATE_*`) são ignoradas.
- **Credenciais do MK e das OLTs são opcionais na carga**: quem vai usar valida. A coleta das
  OLTs roda sem o MK configurado.
- **Drivers de OLT: sondagem antes dos walks.** Antes de ler as tabelas de uma OLT, o driver
  faz uma consulta barata (sysUpTime ou sysDescr). Se ela falhar, a OLT inteira é marcada
  `OltInacessivel` sem tentar as demais tabelas (evita esperar um timeout por tabela).
- **Serial da ONU: uma única função de normalização**, usada para o que vem da OLT, o que o
  técnico digita e o que vai para o MK. Formato canônico: PENDENTE (validar em bancada e com
  o MK).
- **Cliente MK:**
  - só HTTPS, com verificação de certificado sempre ligada; se o MK usar certificado próprio,
    aponte `NOC_MK_CA_BUNDLE` para o bundle da CA dele;
  - o token é cacheado e renovado 2 minutos antes do `Expire`; com chamadas concorrentes, só
    uma reautentica e as outras usam o token novo;
  - chamada de leitura que recebe "token expirado" reautentica e repete uma vez; chamada de
    escrita nunca é repetida automaticamente;
  - a decisão de sucesso de uma resposta fica num lugar só (`verificar_sucesso`); na dúvida,
    não é sucesso.

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

Banco de dados: dois papéis.

| Papel     | Quem usa            | Permissões                                                  | Credencial                                    |
|-----------|---------------------|-------------------------------------------------------------|-----------------------------------------------|
| `noc`     | só o dbmate (host)  | superusuário, dono das tabelas                              | `DATABASE_URL` + `secrets/postgres_password.txt` |
| `noc_app` | a aplicação         | SELECT/INSERT/UPDATE nas tabelas de rede; `leituras_sinal` só SELECT/INSERT; nenhum DELETE | `NOC_DB_*` + `secrets/noc_app_password.txt` |

A aplicação monta a conexão com `NOC_DB_HOST`, `NOC_DB_PORTA`, `NOC_DB_USUARIO`,
`NOC_DB_NOME` e a senha lida do arquivo em `NOC_DB_PASSWORD_FILE`. Ela **não lê** a
`DATABASE_URL` e se recusa a operar se o papel conectado for superusuário.

Credenciais das OLTs: há um padrão global (`NOC_OLT_SNMP_*`, `NOC_OLT_SSH_*`) e cada OLT
pode sobrescrever qualquer campo em `NOC_OLT_SOBRESCRITAS`, um JSON indexado pelo nome
da OLT. A versão do SNMP também é por OLT. SNMPv3 ainda não está implementado.

## Banco de dados

As senhas ficam em arquivos fora do git, montados como secrets do Docker. Use só
caracteres hexadecimais (`openssl rand -hex`), para não precisar escapar nada.

```bash
mkdir -p secrets
# noc (superusuário, migrações): só o Postgres lê, como root
openssl rand -hex 24 > secrets/postgres_password.txt
chmod 600 secrets/postgres_password.txt
# noc_app (aplicação): o container da aplicação lê pelo grupo 10001
openssl rand -hex 24 > secrets/noc_app_password.txt
sudo chgrp 10001 secrets/noc_app_password.txt
chmod 640 secrets/noc_app_password.txt

cp .env.example .env && chmod 600 .env
# coloque no .env a senha do noc dentro da DATABASE_URL (no lugar de TROCAR)

docker compose up -d db
docker compose ps                     # db deve ficar "healthy"
dbmate up                             # tabelas + papel noc_app (sem login)
./scripts/definir_senha_noc_app.sh    # ativa o login do noc_app com a senha do arquivo
```

Para trocar a senha do `noc_app`, gere um novo `secrets/noc_app_password.txt` (mesma
permissão) e rode o script de novo.

Permissão do secret do `noc_app`: dono = seu usuário no host, grupo = GID 10001, modo 640.
Fora do Swarm, o Docker monta o arquivo com o dono e a permissão que ele tem no host; o
usuário `noc` do container da aplicação (UID/GID 10001) lê pelo grupo, e você continua
lendo sem sudo. O `chgrp` precisa de sudo porque seu usuário não faz parte do grupo 10001
(que nem precisa existir no host). O secret do `noc` fica 600: só o Postgres o lê, como
root, na inicialização.

A porta 5432 fica ligada só em `127.0.0.1`.

### Migrações (dbmate)

O dbmate roda no host e lê a `DATABASE_URL` do `.env` sozinho:

```bash
dbmate up        # aplica as migrações pendentes
dbmate status    # lista aplicadas/pendentes
dbmate rollback  # desfaz a última
```

Não geramos `db/schema.sql` (`DBMATE_NO_DUMP_SCHEMA=true`), porque isso exigiria
`pg_dump` 16 no host e o Debian 12 traz o 15. Para ver o esquema:
`docker compose exec db psql -U noc -d noc -c '\d+ onus'`.

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
pytest -rs              # testes (-rs mostra o motivo de cada teste pulado)
```

### Testes de integração

Dois grupos de teste precisam de serviço de verdade e, fora do CI, só rodam quando as
variáveis abaixo existem (sem elas, aparecem como pulados). No CI eles são obrigatórios:
com `CI=true`, variável faltando faz o teste falhar em vez de pular.

- `banco` (`tests/test_db.py`): recusa de superusuário e privilégios do `noc_app`. Use um
  banco **descartável**, com as migrações aplicadas e a senha do `noc_app` definida. Nada é
  gravado (rollback), mas não aponte para o banco de produção.

  ```bash
  TESTE_PG_HOST=127.0.0.1 TESTE_PG_PORTA=5432 TESTE_PG_NOME=noc \
  TESTE_PG_SUPERUSUARIO=noc TESTE_PG_SUPERUSUARIO_SENHA="$(cat secrets/postgres_password.txt)" \
  TESTE_PG_APP_SENHA="$(cat secrets/noc_app_password.txt)" \
  pytest -m banco
  ```

- `snmpsim` (`tests/test_snmp_integracao.py`): walk SNMP contra um agente simulado (OIDs
  fictícios). O teste sobe e derruba o agente sozinho; só precisa do snmpsim instalado num
  venv separado:

  ```bash
  python3.12 -m venv /tmp/snmpsim && /tmp/snmpsim/bin/pip install "snmpsim==1.2.2" "pysmi==2.0.0"
  TESTE_SNMPSIM_BIN=/tmp/snmpsim/bin/snmpsim-command-responder pytest -m snmpsim
  ```

## Container da aplicação

O `Dockerfile` (python:3.12-slim, usuário não-root `noc`) já existe, mas o serviço `app`
do compose fica no profile `app` e só sobe quando pedido, porque ainda não há ponto de
entrada:

```bash
docker compose --profile app build app
```
