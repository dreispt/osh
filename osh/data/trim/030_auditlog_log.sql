-- Trim: OCA audittrail logs — child table first.
DO $$
BEGIN
    IF to_regclass('auditlog_log') IS NOT NULL THEN
        EXECUTE 'TRUNCATE auditlog_log_line, auditlog_log';
    END IF;
END $$;
