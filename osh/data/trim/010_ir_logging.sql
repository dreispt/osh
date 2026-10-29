-- Trim: request logs.
DO $$
BEGIN
    IF to_regclass('ir_logging') IS NOT NULL THEN
        EXECUTE 'TRUNCATE ONLY ir_logging';
    END IF;
END $$;
