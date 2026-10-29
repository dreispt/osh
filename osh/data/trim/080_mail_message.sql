-- Trim: old chatter — keeps osh.keep_days days of recent context.
DO $$
BEGIN
    IF to_regclass('mail_message') IS NULL THEN
        RETURN;
    END IF;
    DELETE FROM mail_message
     WHERE create_date < now() - interval '1 day' * current_setting('osh.keep_days')::int;
END $$;
