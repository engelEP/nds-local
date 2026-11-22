-- Stand-in account used to download a loan unit with one console.
-- Friend code 103979215105. Profile 900000001, game code YFEP.
-- The loan units for this profile are installed by fe11_loan_units.py.

INSERT INTO accounts (userid, created_at) VALUES (900000001, 1795298176)
ON CONFLICT DO NOTHING;

INSERT INTO profiles (
    profileid, userid, gamecd, uniquenick, lastname, firstname, nick, email, partnerid, updated_at
) VALUES (
    900000001, 900000001, 'YFEP', 'fe900000001', '', '', '', '', '', 1795298176
)
ON CONFLICT DO NOTHING;
