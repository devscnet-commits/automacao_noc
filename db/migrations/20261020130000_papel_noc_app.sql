-- migrate:up

-- Papel da aplicação, com o mínimo necessário. O dono das tabelas (noc) fica só para migrações.
-- Nasce sem login e sem senha: a senha é definida fora do Git por
-- scripts/definir_senha_noc_app.sh, que lê secrets/noc_app_password.txt.
-- Sem ALTER DEFAULT PRIVILEGES de propósito: cada migração concede o acesso às suas tabelas.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'noc_app') THEN
        CREATE ROLE noc_app NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO noc_app;
GRANT SELECT, INSERT, UPDATE ON olts, pons, caixas, onus, divergencias TO noc_app;
-- Só inserção garantida pelo banco: sem UPDATE e sem DELETE.
GRANT SELECT, INSERT ON leituras_sinal TO noc_app;

-- migrate:down

REVOKE ALL ON olts, pons, caixas, onus, divergencias, leituras_sinal FROM noc_app;
REVOKE USAGE ON SCHEMA public FROM noc_app;
