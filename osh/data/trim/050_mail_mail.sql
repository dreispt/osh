-- Trim: outgoing mail queue.
DO $$
BEGIN
    IF to_regclass('mail_mail') IS NOT NULL THEN
        EXECUTE 'TRUNCATE mail_mail CASCADE';
    END IF;
END $$;
