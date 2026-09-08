# Frame TV Art Sync

A CLI that mirrors a link-shared Google Photos album onto a Samsung Frame TV, and drives art mode, brightness, and mattes from your terminal.

Everything runs on the LAN, because the TV is the server and there's nothing to push to from outside the house. Nothing stays running either. The TV keeps its own state after the script disconnects, so each command is a short-lived invocation.

Get started with the [setup documentation](docs/setup.md), which ends in a dry run before you let anything delete.


## Commands

| Command | What it does |
| --- | --- |
| `frame pair` | First-run token handshake. Interactive, and you only run it once. |
| `frame sync` | Mirrors the album onto the TV. `--dry-run` prints the plan and touches nothing, and `--label` draws each photo's crop rule onto it. |
| `frame delete` | Deletes the images you name, or `frame delete by-hand` for every upload the inventory doesn't claim. It says what it's about to delete and asks first. |
| `frame mattes` | Lists the matte types the TV will draw around each image shape, and every color with its RGB triple. |
| `frame bakeoff` | Puts a photo on the wall once per matte, one photo per shape in your album, so you can choose a mat by looking at it. Empties the TV first. |
| `frame status` | Current artwork, art mode state, and an inventory summary. |
| `frame art-mode on\|off` | On also wakes a dark panel. Off drops the TV to its last input rather than darkening it, because nothing over this API darkens the panel. |
| `frame brightness N` | Sets the art mode brightness, within the range the TV reports. |
| `frame slideshow 0` | Turns a running slideshow off. Starting one isn't possible over this API, whatever the interval or category. |

Every command takes `--config <path>` to read a config somewhere other than the working directory, and `--debug` to print the TV's protocol frames as they arrive. `frame -h` lists them, and `frame <command> -h` has the options for one.

`frame sync` is a mirror, so a photo you remove from the album comes off the TV on the next run. Deletes are scoped to images this tool uploaded, tracked in `inventory.json`, so art you added by hand is never touched unless you ask for it.

Two keys in `[sync]` decide that, and they're independent. `delete_removed_from_album` is on by default and is what makes this a mirror; turn it off and a sync only ever adds. `delete_added_by_hand` is off by default and widens a run to images the inventory doesn't claim, which is the only way to reach a photo you added from your phone or one stranded by an upload that timed out. Samsung's own art is never a candidate either way. Run `--dry-run` first, because it names every image the second flag would delete.

`frame delete by-hand` reaches those same images one run at a time, if you'd rather read the list and answer a question than leave the flag on.


## Documentation

* [Setup](docs/setup.md) for what to turn on, what to install, and the first dry run.
* [Framing](docs/framing.md) for cropping, mattes, and `frame bakeoff`.
* [Network](docs/network.md) if you were planning to put the TV on a VLAN of its own, because that's the constraint that took the longest to work out.
* [Troubleshooting](docs/troubleshooting.md) for the log and for the failures that have actually happened.


## License

GPL-2.0-or-later.
