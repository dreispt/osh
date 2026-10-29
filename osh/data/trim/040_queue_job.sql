-- Trim: processed queue_job records.
DO $$
BEGIN
    IF to_regclass('queue_job') IS NOT NULL THEN
        EXECUTE 'TRUNCATE ONLY queue_job';
    END IF;
END $$;
