-- Stand-in account used to download a loan unit with one console.
-- Friend code 103979215105. Profile 900000001, game code YFEP.
-- The profile stays empty: no buddy row and no uploaded record.

INSERT OR IGNORE INTO accounts (userid, created_at) VALUES (900000001, 1795298176);

INSERT OR IGNORE INTO profiles (
    profileid, userid, gamecd, uniquenick, lastname, firstname, nick, email, partnerid, updated_at
) VALUES (
    900000001, 900000001, 'YFEP', 'fe900000001', '', '', '', '', '', 1795298176
);
