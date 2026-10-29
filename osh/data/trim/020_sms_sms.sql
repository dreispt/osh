-- Trim: SMS queue.
DO $$
BEGIN
    IF to_regclass('sms_sms') IS NOT NULL THEN
        EXECUTE 'TRUNCATE sms_sms CASCADE';
    END IF;
END $$;
