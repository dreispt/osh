-- Osh fallback database neutralization script
-- Used when the installed Odoo version does not provide `odoo-bin neutralize`.

-- Disable outgoing mail servers.
UPDATE ir_mail_server SET active = false;

-- Insert a dummy outgoing mail server to prevent fallback to CLI-configured servers.
INSERT INTO ir_mail_server(
    name, smtp_port, smtp_host, smtp_encryption, active, smtp_authentication
)
VALUES (
    'neutralization - disable emails', 1025, 'invalid', 'none', true, 'login'
)
ON CONFLICT DO NOTHING;

-- Disable incoming mail servers (fetchmail).
UPDATE fetchmail_server SET active = false;

-- Disable scheduled actions except the autovacuum job.
UPDATE ir_cron
   SET active = false
 WHERE id NOT IN (
    SELECT res_id
      FROM ir_model_data
     WHERE model = 'ir.cron'
       AND name = 'autovacuum_job'
       AND module = 'base'
);

-- Anonymize partner emails.
UPDATE res_partner
   SET email = 'dev+' || id || '@example.local'
 WHERE email IS NOT NULL
   AND email NOT LIKE '%@example.local';

-- Mark the database as neutralized.
INSERT INTO ir_config_parameter (key, value)
VALUES ('database.is_neutralized', 'true')
    ON CONFLICT (key) DO
       UPDATE SET value = 'true';

-- Point the base URL to a local development address.
INSERT INTO ir_config_parameter (key, value)
VALUES ('web.base.url', 'http://localhost:8069')
    ON CONFLICT (key) DO
       UPDATE SET value = 'http://localhost:8069';
