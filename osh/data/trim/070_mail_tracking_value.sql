-- Trim: old tracked-field values — keeps osh.keep_days days.
DO $$
BEGIN
    IF to_regclass('mail_tracking_value') IS NULL
        OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'mail_tracking_value'
                          AND column_name = 'create_date') THEN
        RETURN;
    END IF;
    DELETE FROM mail_tracking_value
     WHERE create_date < now() - interval '1 day' * current_setting('osh.keep_days')::int;
END $$;
