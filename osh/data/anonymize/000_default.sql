-- Osh bundled anonymization profile.
-- Rewrites PII to deterministic, id-derived values so the result stays
-- coherent across runs and references. Every statement is wrapped in a
-- DO block guarded by to_regclass()/column checks, so one script serves
-- every Odoo version and module set.

DO $$
BEGIN
    IF to_regclass('res_partner') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'name') THEN
        UPDATE res_partner SET name = 'Partner ' || id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'email') THEN
        UPDATE res_partner SET email = 'partner' || id || '@example.local'
         WHERE email IS NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'phone') THEN
        UPDATE res_partner SET phone = repeat('x', length(phone)) WHERE phone IS NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'mobile') THEN
        UPDATE res_partner SET mobile = repeat('x', length(mobile)) WHERE mobile IS NOT NULL;
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
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'vat') THEN
        UPDATE res_partner SET vat = repeat('x', length(vat)) WHERE vat IS NOT NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('res_users') IS NULL THEN
        RETURN;
    END IF;
    -- login stays unique per record; passwords are left untouched so the
    -- developer is not locked out of the database.
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_users' AND column_name = 'login') THEN
        UPDATE res_users SET login = 'user' || id;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('res_partner_bank') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner_bank' AND column_name = 'acc_number') THEN
        UPDATE res_partner_bank SET acc_number = 'ACC' || id;
    END IF;
END $$;

-- Private employee data — guarded: hr may not be installed.
DO $$
BEGIN
    IF to_regclass('hr_employee') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'name') THEN
        UPDATE hr_employee SET name = 'Employee ' || id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'private_email') THEN
        UPDATE hr_employee SET private_email = 'employee' || id || '@example.local';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'private_phone') THEN
        UPDATE hr_employee SET private_phone = repeat('x', length(private_phone)) WHERE private_phone IS NOT NULL;
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
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'emergency_contact') THEN
        UPDATE hr_employee SET emergency_contact = repeat('x', length(emergency_contact)) WHERE emergency_contact IS NOT NULL;
        UPDATE hr_employee SET emergency_phone = repeat('x', length(emergency_phone)) WHERE emergency_phone IS NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'identification_id') THEN
        UPDATE hr_employee SET identification_id = repeat('x', length(identification_id)) WHERE identification_id IS NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'passport_id') THEN
        UPDATE hr_employee SET passport_id = repeat('x', length(passport_id)) WHERE passport_id IS NOT NULL;
    END IF;
END $$;

-- Mark the database as anonymized.
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
