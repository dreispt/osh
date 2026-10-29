-- Anonymize level 1: identity — names, emails, phones, logins and
-- identifier numbers. Deterministic, id-derived values so the result
-- stays coherent across runs and references. Every statement sits in a
-- DO block guarded by to_regclass()/column checks so one script serves
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
                WHERE table_name = 'res_partner' AND column_name = 'vat') THEN
        UPDATE res_partner SET vat = repeat('x', length(vat)) WHERE vat IS NOT NULL;
    END IF;
    -- OCA partner-contact's partner_firstname splits the name.
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'res_partner' AND column_name = 'lastname') THEN
        UPDATE res_partner SET lastname = repeat('x', length(lastname)) WHERE lastname IS NOT NULL;
        UPDATE res_partner SET firstname = repeat('x', length(firstname)) WHERE firstname IS NOT NULL;
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

-- Employee identity — guarded: hr may not be installed.
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
