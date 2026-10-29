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
        UPDATE res_partner SET phone = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'mobile') THEN
        UPDATE res_partner SET mobile = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'street') THEN
        UPDATE res_partner SET street = NULL, street2 = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'city') THEN
        UPDATE res_partner SET city = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'zip') THEN
        UPDATE res_partner SET zip = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'vat') THEN
        UPDATE res_partner SET vat = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'website') THEN
        UPDATE res_partner SET website = NULL;
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
        UPDATE hr_employee SET private_phone = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'private_street') THEN
        UPDATE hr_employee SET private_street = NULL, private_street2 = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'private_city') THEN
        UPDATE hr_employee SET private_city = NULL, private_zip = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'emergency_contact') THEN
        UPDATE hr_employee SET emergency_contact = NULL, emergency_phone = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'identification_id') THEN
        UPDATE hr_employee SET identification_id = NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hr_employee' AND column_name = 'passport_id') THEN
        UPDATE hr_employee SET passport_id = NULL;
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
