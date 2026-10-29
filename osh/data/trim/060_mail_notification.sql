-- Trim: old notification noise — keeps osh.keep_days days.
-- mail_notification has no create_date in versions where
-- _log_access=False; old rows still die via cascade on mail_message.
DO $$
BEGIN
    IF to_regclass('mail_notification') IS NULL
        OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'mail_notification'
                          AND column_name = 'create_date') THEN
        RETURN;
    END IF;
    DELETE FROM mail_notification
     WHERE create_date < now() - interval '1 day' * current_setting('osh.keep_days')::int;
END $$;
