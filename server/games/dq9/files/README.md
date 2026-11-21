# Dragon Quest IX files

Sellma asks the server for files that are already prepared. This program does not build them.

These files belong to the `server.games.dq9` package. The server loads them from here; there is no separate content directory.

```
files/YDQE/       USA, English, French, Spanish
files/YDQP/       Europe
files/YDQJ/       Japan
files/weeks/      one shop week per folder
```

The usual names are:

- `quest` or `quest.bin` — the 64 quests and the 23 guests
- `auction` or `auction.bin` — one shop week, six items and their stock
- `mes` or `mes.bin` — the message Sellma shows

The game requests a specific filename. The server log prints that name when the file is missing. Rename your file to match, or add a `_list.txt` that names the files and their attributes. A list line has six tab-separated fields:

```
filename		attr1	attr2	attr3	size
```

`_list.txt` itself is never sent as a download. Files whose names start with `.` or `_` are hidden from the generated list.

The Christmas cake is a shop item, internal number 22031. It is inside the auction file for a few weeks (New Year's Sale at 2,400 gold, and several December weeks at 4,800). Unlocking quest 155 does not put the cake in the bag. After that week's auction file is the one Sellma received, buy it at her counter with the party's gold. One visit stocks six items drawn from that week.
