-- migrate:up

-- Inventário da rede óptica lido das OLTs, linha de base de sinal e divergências.
-- Regras gerais:
--   * nenhuma credencial é gravada no banco (elas vêm do ambiente);
--   * chaves estrangeiras com RESTRICT (padrão): apagar OLT/PON/ONU nunca apaga histórico;
--   * datas sempre em timestamptz.

CREATE TABLE olts (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Nome usado como chave em NOC_OLT_SOBRESCRITAS (credencial por OLT).
    nome        text        NOT NULL UNIQUE,
    fabricante  text        NOT NULL CHECK (fabricante IN ('huawei', 'datacom')),
    modelo      text,
    endereco_ip inet        NOT NULL UNIQUE,
    snmp_porta  integer     NOT NULL DEFAULT 161 CHECK (snmp_porta BETWEEN 1 AND 65535),
    ssh_porta   integer     NOT NULL DEFAULT 22  CHECK (ssh_porta BETWEEN 1 AND 65535),
    ativa       boolean     NOT NULL DEFAULT true,
    -- PENDENTE: validar com NOC/bancada. Código da OLT no MK ERP (nome e formato do campo
    -- na API do MK ainda não confirmados); a coluna será criada quando soubermos.
    criado_em   timestamptz NOT NULL DEFAULT now()
);

-- Porta PON: chassi/slot/porta. Huawei chama o primeiro nível de "frame" (0/1/0);
-- Datacom DmOS, de chassi (1/1/1).
CREATE TABLE pons (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    olt_id    bigint      NOT NULL REFERENCES olts (id),
    chassi    integer     NOT NULL CHECK (chassi >= 0),
    slot      integer     NOT NULL CHECK (slot >= 0),
    porta     integer     NOT NULL CHECK (porta >= 0),
    descricao text,
    criado_em timestamptz NOT NULL DEFAULT now(),
    UNIQUE (olt_id, chassi, slot, porta)
);

-- Caixa de atendimento (CTO).
CREATE TABLE caixas (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Identificador como aparece no nome da ONU / no campo.
    -- PENDENTE: validar com NOC/bancada o escopo de unicidade (provavelmente único só
    -- dentro de POP/ramal). Até lá, sem UNIQUE: só um índice comum.
    identificador text        NOT NULL,
    -- PENDENTE: validar com NOC/bancada se uma caixa é sempre atendida por uma única PON.
    pon_id        bigint      REFERENCES pons (id),
    -- PENDENTE: validar com NOC/bancada a capacidade das caixas.
    total_portas  integer     CHECK (total_portas > 0),
    criado_em     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX caixas_identificador_idx ON caixas (identificador);
CREATE INDEX caixas_pon_id_idx ON caixas (pon_id);

-- ONU como vista na OLT (inventário). Não guarda dados de cliente nem de ativação.
CREATE TABLE onus (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pon_id      bigint      NOT NULL REFERENCES pons (id),
    -- Índice da ONU dentro da PON (ONT ID).
    onu_id      integer     NOT NULL CHECK (onu_id >= 0),
    -- Sem UNIQUE de propósito: se uma ONU muda de PON, o registro antigo pode existir até
    -- a próxima sincronização. Serial repetido vira divergência, não erro de gravação.
    serial      text,
    -- Nome/descrição exatamente como está na OLT.
    nome_na_olt text,
    -- Caixa e porta extraídas do nome (app/coleta/nomes.py). NULL se o nome não foi
    -- interpretado. Sem UNIQUE em (caixa_id, porta_caixa) pelo mesmo motivo do serial.
    caixa_id    bigint      REFERENCES caixas (id),
    porta_caixa integer,
    -- Última vez que a ONU apareceu numa leitura da OLT.
    vista_em    timestamptz,
    criado_em   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pon_id, onu_id)
);

CREATE INDEX onus_serial_idx ON onus (serial);
CREATE INDEX onus_caixa_id_idx ON onus (caixa_id);

-- Leitura de sinal óptico. Só inserção: uma linha por ONU por leitura.
-- tipo: 'linha_de_base' (coleta noturna), 'ativacao' (leitura da instalação, a que é
-- comparada com a linha de base) e 'manual' (pedida por alguém, fora desses fluxos).
-- Potências já convertidas para dBm. NULL = não lida ou ONU offline.
-- PENDENTE: validar com NOC/bancada a escala dos valores brutos, o valor sentinela de
-- ONU offline e quais destas três medidas cada fabricante expõe.
CREATE TABLE leituras_sinal (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    onu_id     bigint       NOT NULL REFERENCES onus (id),
    lido_em    timestamptz  NOT NULL DEFAULT now(),
    tipo       text         NOT NULL CHECK (tipo IN ('linha_de_base', 'ativacao', 'manual')),
    rx_onu_dbm numeric(5, 2),
    tx_onu_dbm numeric(5, 2),
    rx_olt_dbm numeric(5, 2)
);

COMMENT ON COLUMN leituras_sinal.rx_onu_dbm IS
    'Potência recebida pela ONU, em dBm (descida, 1490 nm). Comparável ao medidor do técnico.';
COMMENT ON COLUMN leituras_sinal.rx_olt_dbm IS
    'Potência recebida pela OLT vinda desta ONU, em dBm (subida, 1310 nm).';
COMMENT ON COLUMN leituras_sinal.tx_onu_dbm IS
    'Potência transmitida pela ONU, em dBm.';

CREATE INDEX leituras_sinal_onu_lido_em_idx ON leituras_sinal (onu_id, lido_em DESC);

-- Divergência encontrada na coleta (ex.: nome fora do padrão, serial repetido).
CREATE TABLE divergencias (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- PENDENTE: validar com NOC/bancada a lista de tipos; por isso sem CHECK por enquanto.
    tipo         text        NOT NULL,
    olt_id       bigint      REFERENCES olts (id),
    onu_id       bigint      REFERENCES onus (id),
    detalhes     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    detectada_em timestamptz NOT NULL DEFAULT now(),
    resolvida_em timestamptz,
    CHECK (resolvida_em IS NULL OR resolvida_em >= detectada_em)
);

CREATE INDEX divergencias_abertas_idx ON divergencias (detectada_em) WHERE resolvida_em IS NULL;
CREATE INDEX divergencias_olt_id_idx ON divergencias (olt_id);
CREATE INDEX divergencias_onu_id_idx ON divergencias (onu_id);

-- migrate:down

DROP TABLE divergencias;
DROP TABLE leituras_sinal;
DROP TABLE onus;
DROP TABLE caixas;
DROP TABLE pons;
DROP TABLE olts;
