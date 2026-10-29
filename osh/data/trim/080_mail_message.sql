-- Trim: old chatter — keeps osh.keep_days days of recent context.
DO $$
BEGIN
    IF to_regclass('mail_message') IS NULL
        OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'mail_message'
                          AND column_name = 'create_date') THEN
        RETURN;
    END IF;
    DELETE FROM mail_message
     WHERE create_date < now() - interval '1 day' * current_setting('osh.keep_days')::int;
END $$;
