-- Trim: old notification noise — keeps osh.keep_days days.
DO $$
BEGIN
    IF to_regclass('mail_notification') IS NULL THEN
        RETURN;
    END IF;
    DELETE FROM mail_notification
     WHERE create_date < now() - interval '1 day' * current_setting('osh.keep_days')::int;
END $$;
