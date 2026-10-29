-- Trim: old tracked-field values — keeps osh.keep_days days.
DO $$
BEGIN
    IF to_regclass('mail_tracking_value') IS NULL THEN
        RETURN;
    END IF;
    DELETE FROM mail_tracking_value
     WHERE create_date < now() - interval '1 day' * current_setting('osh.keep_days')::int;
END $$;
