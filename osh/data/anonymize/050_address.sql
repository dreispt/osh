-- Anonymize level 2: addresses — street, city and zip fields, masked at
-- their original length. Same guard pattern as the identity level.

DO $$
BEGIN
    IF to_regclass('res_partner') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'street') THEN
        UPDATE res_partner SET street = repeat('x', length(street)) WHERE street IS NOT NULL;
        UPDATE res_partner SET street2 = repeat('x', length(street2)) WHERE street2 IS NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'city') THEN
        UPDATE res_partner SET city = repeat('x', length(city)) WHERE city IS NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'zip') THEN
        UPDATE res_partner SET zip = repeat('x', length(zip)) WHERE zip IS NOT NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('hr_employee') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'private_street') THEN
        UPDATE hr_employee SET private_street = repeat('x', length(private_street)) WHERE private_street IS NOT NULL;
        UPDATE hr_employee SET private_street2 = repeat('x', length(private_street2)) WHERE private_street2 IS NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'private_city') THEN
        UPDATE hr_employee SET private_city = repeat('x', length(private_city)) WHERE private_city IS NOT NULL;
        UPDATE hr_employee SET private_zip = repeat('x', length(private_zip)) WHERE private_zip IS NOT NULL;
    END IF;
END $$;

-- Mark the database as anonymized (runs last in the full set).
DO $$
BEGIN
    IF to_regclass('ir_config_parameter') IS NULL THEN
        RETURN;
    END IF;
    INSERT INTO ir_config_parameter (key, value)
    VALUES ('database.is_anonymized', 'true')
        ON CONFLICT (key) DO
           UPDATE SET value = 'true';
END $$;
