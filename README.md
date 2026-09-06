# frame-tv-art-sync

A CLI that mirrors a link-shared Google Photos album onto a Samsung Frame TV, and drives art mode, brightness, the slideshow, and mattes from your terminal.

_Status: early._ Every command is written except `frame matte`, and `frame sync` has mirrored a 179 photo album onto a real TV. Two things are still missing before it does what it says on the tin: the TV shows one photo indefinitely, because a slideshow can't be started over this API and nothing here selects an image, and the Art app has twice wedged hard enough to need a power cycle. `docs/TODO.md` tracks both. Run `--dry-run` before your first real sync regardless, since sync is the command that deletes.

Everything runs on the LAN, because the TV is the server and there's nothing to push to from outside the house. Nothing stays running either. The TV keeps its own state after the script disconnects, so each command is a short-lived invocation.


## Requirements

* Python 3.11 or later, and [`uv`](https://docs.astral.sh/uv/).
* A Frame TV on the same network as the machine you run this from. Developed against a `QN32LS03CB`, the 2023 LS03C at 32".
* A Google Photos album shared by link.


## Setup

1. On the TV, set Settings > General > External Device Manager > Device Connect Manager > Access Notification to `First Time` or `On`.
    1. Pairing fails silently if it's off, so do this before anything else.
1. Give the TV a DHCP reservation. Its MAC is under Settings > General > Network > Network Status > IP Settings.
1. Install the CLI.

    ```
    uv tool install .
    ```

1. Copy `config.example.toml` to `config.toml` and fill in the TV's address and your album's share link.
    1. Store the whole link, `key` and all. The album id on its own gets you a 404.
    1. It's read from the working directory. Pass `frame --config <path>` to read it from somewhere else, which is what a scheduled job wants.
    1. A scheduled job also wants `--retry`. The TV needs about ten seconds to notice the last client left, so a command run right after another one fails; `--retry` waits and reconnects once, where a run you're watching fails immediately so you can just run it again.
1. Pair with the TV, and accept the on-screen prompt within about 30 seconds.

    ```
    frame pair
    ```

1. See what a sync would do before you let it do it.

    ```
    frame sync --dry-run
    ```

    That prints what it would upload and delete, and the matte each photo would get, without touching anything. It reads the TV to work that out, so pair first.

`config.toml`, the token file, and `inventory.json` are gitignored, and they're the only files that hold anything account-specific. All three live next to each other, so pointing `--config` somewhere else moves the whole set.


## Commands

| Command | What it does |
| --- | --- |
| `frame pair` | First-run token handshake. Interactive, and you only run it once. |
| `frame sync` | Mirrors the album onto the TV. `--dry-run` prints the plan and touches nothing. |
| `frame art-mode on\|off` | On also wakes a dark panel. Off drops the TV to its last input rather than darkening it, because nothing over this API darkens the panel. |
| `frame brightness N` | Sets the art mode brightness, within the range the TV reports. |
| `frame slideshow 0` | Turns a running slideshow off. Starting one isn't possible over this API, whatever the interval or category. |
| `frame mattes` | Lists the matte types the TV offers for each orientation, and every color with its RGB triple. |
| `frame matte <matte_id>` | Applies a matte to everything in the inventory. `--only <content_id>` narrows it to one image. |
| `frame bakeoff` | Puts one photo on the wall once per matte, so you can choose a mat by looking at it. Empties the TV first. |
| `frame status` | Current artwork, art mode state, and an inventory summary. |

`frame sync` is a mirror, so a photo you remove from the album comes off the TV on the next run. Deletes are scoped to images this tool uploaded, tracked in `inventory.json`, so art you added by hand is never touched unless you ask for it.

Two keys in `[sync]` decide that, and they're independent. `delete_removed_from_album` is on by default and is what makes this a mirror; turn it off and a sync only ever adds. `delete_added_by_hand` is off by default and widens a run to images the inventory doesn't claim, which is the only way to reach a photo you added from your phone or one stranded by an upload that timed out. Samsung's own art is never a candidate either way. Run `--dry-run` first, because it names every image the second flag would delete.

Nothing is cropped on the way up. Every photo keeps its own shape and the TV frames it inside the mat you configured, so the matte is what decides how much of the panel the photo fills and whether any of it is cut off. A phone photo is 4:3 and the panel is 16:9, so there is no setting that both fills the screen and keeps the whole photo; `flexible` keeps the photo and gives up the screen area, and `none` does the opposite by letting the TV center-crop. `config.example.toml` has the rest, including `sync.short_run`, which mirrors just the newest few photos of each orientation so you can try a matte on the wall without uploading the album.

Choosing which matte, though, is what `frame bakeoff` is for. A matte can only be set as a photo is uploaded, so seeing sixteen mat colors means uploading the same photo sixteen times, and the TV's picker shows thumbnails and no names. A round handles both: it puts the newest photo of one orientation up once per matte with the variant's number drawn across the middle, and prints a roster saying which number is which. Compare colors first and then types, one orientation at a time.

```
frame bakeoff --compare=colors --orientation=landscape --dry-run
frame bakeoff --compare=colors --orientation=landscape
frame bakeoff --compare=types --orientation=landscape --color=polar
frame bakeoff --clear
```

**This is the other command that deletes, and it deletes more than `frame sync` ever does.** A round starts by emptying the TV, so that nothing sits between the variants in the picker, and that reaches every uploaded image whether or not the inventory claims it. Samsung's own art is never touched. It names what it is about to delete and asks first, unless you pass `--yes`. Nothing here changes what the panel shows, so open the TV's own picker to look at the variants, and put the winner in `config.toml` yourself. `--clear` on its own takes the last round down, and a plain `frame sync` then restores the album.

Keep `inventory.json` alongside `config.toml` and don't delete it. It's the only record of which images on the TV came from here, and losing it doesn't cause stray deletes so much as stray uploads: every photo would read as new and go up a second time, with the first copies left on the TV that only `delete_added_by_hand` can then clean up. A sync that finds no inventory and a TV that already holds images stops and says so, and `--first-run` is how you tell it that none of them are its own.


## The log

Every run appends to `frame.log` beside your config, rotating at 5 MB and keeping three generations. It holds this tool's own progress and every frame the TV sent, which is the only way to see the ones nothing asked for, such as the TV announcing that it has left art mode. That matters because the run worth having a log of is the one you didn't expect to fail.

The TV's token and address and the album's share key are masked going in, so the log is safe to attach to an issue. Read it before you do, all the same. `--debug` prints the frames as they arrive as well, which is only worth it when you're watching a run live.


## Troubleshooting

**The pairing prompt never appears.** The TV remembers a denial and won't ask twice. Clear the entry from Device List under Settings > General > External Device Manager > Device Connect Manager, then run `frame pair` again.

**A command fails right after another one worked.** The TV keys its Device List on the client name and takes about ten seconds to notice a client left, so a second connection inside that window gets silence. Wait twenty seconds and run it again, or pass `--retry`.

**Everything broke after a TV software update.** Tizen updates have flipped Access Notification back off and invalidated tokens. Check that setting, delete the token file, and re-pair.

**"No route to host" for an address you know is up.** On macOS, Local Network privacy gates the terminal app rather than the script, and a denial looks identical to the TV being absent. Grant your terminal access under System Settings > Privacy & Security > Local Network. Internet traffic keeps working while LAN traffic doesn't, so that symptom on its own doesn't tell you the TV is off.

**A sync run fails to read the album.** Google doesn't document the page this scrapes and can change it whenever they want, so treat this as expected maintenance rather than a surprise. `docs/spikes.md` records the structure the parser expects.

**"The album paginates."** Google's page carries every item up to at least 179 photos, so this shouldn't come up, but there's no telling where the limit sits. Redeeming the continuation token isn't implemented, and reading half an album would look like you'd deleted the other half, so `frame sync` refuses to run at all rather than mirror a partial list. If you hit it, take photos out of the album until it runs, and open an issue with the count that broke it.

**A sync run wants to re-upload the entire album.** That happens after the album is unshared and re-shared under a new link, if Google hands out new ids for the same photos. Nothing is lost. The run uploads everything again and deletes the copies it uploaded before, and every run after it is stable, so the cost is upload time. It's worth letting it finish rather than interrupting it, because a partial run leaves both copies on the TV.


## Design decisions

The reasoning behind the architecture, the sources and pipeline split, and the firmware quirks worth knowing are in `CLAUDE.md`. Panel specs, power measurements, and the physical build notes are in `docs/initial research.md`. If you were planning to put the TV on a VLAN of its own, read `docs/network.md` first, because that's the constraint that took the longest to work out.


## License

GPL-2.0-or-later.
