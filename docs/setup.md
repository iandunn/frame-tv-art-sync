# Setup

TL;DR: turn Access Notification on, give the TV a DHCP reservation, install with `uv tool install .`, fill in `config.toml`, run `frame pair`, then `frame sync --dry-run`.


## Requirements

* Python 3.11 or later, and [`uv`](https://docs.astral.sh/uv/).
* A Frame TV on the same network as the machine you run this from. Developed against a `QN32LS03CB`, the 2023 LS03C at 32". It may work on others but I haven't tested.
* A Google Photos album shared by link.


## Steps

1. On the TV, set Settings > General > External Device Manager > Device Connect Manager > Access Notification to `First Time` or `On`.
    1. Pairing fails silently if it's off, so do this before anything else.
1. Give the TV a DHCP reservation. Its MAC is under Settings > General > Network > Network Status > IP Settings.
    1. The reservation isn't optional. The TV keys its Device List on the client's address as well as its name, so this machine moving between Wi-Fi and Ethernet, or picking up a new lease, costs you another pairing prompt.
1. Clone this repository, then install the CLI from inside the clone.

    ```
    uv tool install .
    ```

    That puts `frame` on your `PATH`. To work on the code instead, skip the install and put `uv run` in front of every command, from inside the clone.

1. Copy `config.example.toml` to `config.toml` and fill in the TV's address and your album's share link.
    1. Store the whole link, `key` and all. The album id on its own gets you a 404.
    1. It's read from the working directory. Pass `frame --config <path>` to read it from somewhere else, which is what a scheduled job wants.
1. Pair with the TV, and accept the on-screen prompt within about 30 seconds.

    ```
    frame pair
    ```

1. See what a sync would do before you let it do it.

    ```
    frame sync --dry-run
    ```

    That prints what it would upload and delete, and the matte each photo would get, without touching anything. It reads the TV to work that out, so pair first.


## The files beside your config

`config.toml`, the token file, and `inventory.json` are gitignored, and they're the only files that hold anything account-specific. All three live next to each other, so pointing `--config` somewhere else moves the whole set. `frame.log` lands there too.

**Don't delete `inventory.json`.** It's the only record of which images on the TV came from here, and losing it doesn't cause stray deletes so much as stray uploads: every photo would read as new and go up a second time, with the first copies left on the TV that only `delete_added_by_hand` can then clean up. A sync that finds no inventory and a TV that already holds images stops and says so, and `--first-run` is how you tell it that none of them are its own.
