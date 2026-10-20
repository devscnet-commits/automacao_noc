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
```

## Requisitos

- Python 3.12
- Docker + Docker Compose (PostgreSQL 16)
- [dbmate](https://github.com/amacneil/dbmate) para as migrações

## Instalação para desenvolvimento

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # e preencha
```

## Comandos

```bash
ruff check .            # lint
ruff format --check .   # formatação
pytest                  # testes
```
