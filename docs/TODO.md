# TODO

Scope and rationale are in `../CLAUDE.md`. This is just the running task list.

Starting fresh: every Google spike is answered, and so is every matte spike, so nothing on the source side or the matte side is blocked. **The TV wrapper under MVP is what to build next and everything else queues behind it** -- check `git worktree list` before starting on it, because a session may already be on that branch. Everything still open under Spikes needs the TV and somebody watching the panel, and only one process can hold the art channel at a time.

**If a `config.toml` predates the matte split it will fail to load, on purpose.** `art.matte` became `art.landscape_matte` plus `art.portrait_matte`, because the TV accepts six matte types on a landscape and only two on a portrait, so one value would be held to the intersection. The error names both replacements; `config.example.toml` has the block to copy.


## Scaffolding

- [x] `pyproject.toml` with `uv`, `samsungtvws`, `Pillow`, `click`, `pytest`
- [x] `README.md`, with the license recorded in a note at the end of it
- [x] `.gitignore` covering `config.toml`, the token file, and `.claude/`, since spike scratch under `.claude/tmp/` holds the album URL and owner details
- [x] `config.example.toml`
- [x] `src/` layout, `frame` entry point, and every MVP command stubbed out so `frame --help` shows the real interface
- [x] `git init`
- [x] License recorded as GPL-2.0-or-later in `pyproject.toml` and a README section. No `LICENSE` file, deliberately


## Spikes

Method and findings for each of these are in `spikes.md`. Do them before writing the code that depends on them, since each one can invalidate a design choice.

Google, needs no TV:

- [x] G1. Confirm a link-shared album's public page still embeds fetchable item JSON, and record its structure
- [x] G2. Find a stable per-item id, or decide to hash bytes instead
- [x] G3. Check whether the page holds the whole album or paginates -- it holds the whole thing. At 179 photos all 179 items were on the page and the continuation token was still empty, so a single fetch is enough. `uv run .claude/tmp/g_probe.py` prints that token, and the source refuses to return anything when it's non-empty, so a limit at some higher count would fail loudly
- [x] G4. Check whether the image URL takes size parameters
- [x] G4b. Close G4's two gaps, a Display P3 original and a source wider than 16:9. Seven of the 179 originals are wide-gamut, but they need no code: Google's resizer passes the pixels through and preserves the profile, so `_to_srgb()` converts them correctly. Nothing is wider than 16:9, which moved to `Later`

TV, needs the TV on the network, and `T2` before the rest:

- [x] T1. `curl -s http://<tv-ip>:8001/api/v2/ | jq`, and set a DHCP reservation
- [x] T2. Pair once by hand and confirm the token survives a second run -- both channels paired; only the remote channel issues a token, and neither re-prompts
- [x] T2b. Confirm the art channel answers from standby, since that's where the nightly `art-mode on` runs from -- it does, and waking needs the off-then-on toggle
- [x] T2c. Confirm an already-allowed client is accepted from another subnet -- it isn't. The TV refuses every client off its own subnet, allowed or not, so it can't be isolated on a VLAN of its own while the controlling machine stays on `Private`. It was moved onto the `Private` subnet on a dedicated `Fenced` SSID and fenced with an EAP ACL instead. `network.md` has the deployed design
- [ ] T2d. Find a remote-channel key sequence that puts the panel in standby -- parked; the channel is paired, but every `KEY_POWER` variant so far acts as a short press. `.claude/tmp/t2d_keys.py` has the untried candidates. What's missing is only the darkening: with the panel put into standby by hand, `frame art-mode on` wakes it reliably, so a nightly schedule already has its `on` half and this is what the `off` half waits on
- [x] T3. Dump `art.available()` as the baseline inventory, before uploading anything
- [x] T4. Upload and delete one throwaway image, since mirroring assumes deletes work -- both halves work. `available()` concatenates per-category listings, so dedupe on `content_id`
- [x] T5. Find out which of the two slideshow endpoints this firmware honors -- `slideshow`, but it only accepts `off`. Every non-zero duration is refused with -7, so `frame slideshow N` can't be built as scoped
- [x] T6. Record the matte types and colors this firmware offers -- `portrait_matte_id` settled by T9 and it is inert on this panel
- [x] T7. Confirm the reported `category_id` values, and whether `set_favourite` moves an item -- `MY-C0002` and `MY-C0008` confirmed; `set_favourite` fails on this firmware, so `MY-C0004` is unreachable and favourites can't be a playlist
- [x] T9. Find out whether the TV can frame a portrait photo itself, given a non-`1920x1080` upload -- with no matte it can't, and center-crops anything that isn't 16:9. T12 then showed a matte is what was missing, so the pipeline leaves a portrait alone rather than compositing it onto a canvas
- [x] T8. Check whether SmartThings offers a Google Photos cloud media app for this TV -- it doesn't, so it wasn't backported to the 2023 LS03C and scraping stays the only route
- [x] T10. Find out whether a non-`none` `matte_id` crashes Art Mode on a landscape image too, or only on a portrait -- only on a portrait. The landscape matted correctly, which makes the portrait crash look like a firmware bug rather than a missing capability
- [x] T12. **High priority.** Be certain the TV can't draw its own mat around a portrait before accepting a burned-in one -- it can, and it frames the portrait whole with mat around it, so nothing needs compositing in. The matte the TV chose is `flexible_black`, and `flexible` is a type the API can name. T16 measured where the mat sits, which is all four sides rather than only the left and right
- [x] T14. Confirm `change_matte()` can set `flexible_*` on a portrait from the API rather than only from the TV's UI -- it can't, and the portrait has nothing to do with it. `change_matte()` refuses every image with `error -7` and changes nothing, a landscape and a no-op request included, so `upload(matte=)` is the only place a matte can be set
- [x] T16. Find out whether `upload(matte="flexible_black")` mats a portrait, now that `change_matte()` is ruled out -- it does, with no crash. The image is framed whole and unstretched with mat on all four sides, at about 86% of the panel's height, because `flexible`'s aperture takes the image's own aspect ratio rather than imposing one
- [x] T15. Find which matte types are safe on a portrait -- `flexible` and `shadowbox`, and those two only. A landscape gets six: `none`, `modernthin`, `modern`, `modernwide`, `flexible`, `shadowbox`. `panoramic`, `triptych`, `mix` and `squares` are offered for neither, so all four are unusable. Read by applying each from the TV's picker and letting `available()` report the name, which risks no crash. It also settled T16's bevel: `flexible` draws a white line about 6 panel pixels wide inside its aperture
- [ ] T13. Settle what the mat colors actually look like on the panel, since choosing one from a name is the only way `frame matte` can offer them. Ready for a session that has nobody else waiting on it: `spikes.md` T13 has the whole thing written up cold, including what T16 already settled at the dark end, the exposure trap that makes sampled numbers untrustworthy across photographs, and the two test images left on the TV for it. The open question is whether `polar` reads as near-white through the anti-glare film in a lit room, which is what decides the default
- [ ] T17. Find out what deleting the image currently on the panel does, since a mirror will eventually do it and nobody has tried. Worth answering on the first real `frame sync`, by noting what the panel shows before the run and whether it changes: the plausible outcomes are that the TV moves to another image, that it falls back to the Art Store, and that it shows nothing until something else is selected
- [x] T11. Find the upload size ceiling, if there is one -- none found. 4.78 MB uploaded without complaint, well past what the pipeline produces


## MVP

- [x] **TV wrapper over `samsungtvws`.** `tv.py` holds it: `FrameTv` is a context manager over one art channel connection, `_Channel` puts a wall-clock deadline on every request, and `channel_lock` is the `fcntl.flock` on `/tmp/frame-tv-art-sync.lock`. What makes it more than a passthrough is the token, the lock, the deadline, one connection per invocation, and refusing by name the requests that wedge the channel or do nothing; CLAUDE.md's "Expensive things to know" has each one. Two deliberate non-features: `available()` returns raw rows, because the dedupe and the Art Store filter live in the tested `sync.tv_content_ids()` and splitting them across two places would split the one rule that decides what gets deleted, and no matte decision is made here, because that is `mattes.py`. Every path is verified against the TV, reads and writes both, waking a dark panel included, which is what pinned the `get_matte_list()` and `get_artmode_settings` payload shapes now in CLAUDE.md
- [x] Inventory module: read, write, and diff -- `inventory.py` and `sync.py`, both pure and tested, with nothing calling them until the TV wrapper exists
- [x] Image pipeline: crop a landscape to `1920x1080`, leave a portrait's shape alone for the TV to mat, sRGB, quality 95, highlight rolloff
- [x] Config loader for `config.toml`, with `--config` to point elsewhere
- [x] Matte rules: `mattes.py` holds the type sets per orientation, splits an id into its type and color, picks the key an image's shape calls for, and refuses anything the TV wouldn't accept. Pure and tested, and `load_config` validates through it, so a crashing matte fails at load rather than on the panel
- [x] Source interface, plus the Google shared-album implementation
- [x] `frame pair`, opening both channels in one run with a wait between them, since they share a client name. Re-running it on an already-paired client prompts for neither and writes no new token, and it says so rather than claiming it issued one
- [x] `frame sync --dry-run`, before the version that writes -- prints the three-way diff, the matte each photo would get, and what it would leave alone, and reaches for the TV only to read `available()`
- [x] `frame sync`, including deletes scoped to the inventory. `syncer.py` runs a plan and `sync.py` stays pure. Photos are fetched and prepared into a spool before the channel is opened, because it closes itself after about 25 seconds of silence and a live fetch between two uploads would eventually outlast that. Uploads go before deletes, the inventory is saved after each upload, a delete is confirmed by re-reading `available()` rather than by trusting the call, and a run with no inventory refuses when the TV already holds something unless `--first-run` says none of it is this tool's. **Never verified against the TV** -- the whole write path has only ever run against a fake
- [x] `frame art-mode on|off`, where `on` reads REST first and does the off-then-on toggle when the panel is dark. All three states confirmed: `off` leaves the panel lit showing the TV's own UI, `on` from lit is a plain call, and `on` from a dark panel does the toggle and flips REST back to `on`
- [x] `frame brightness N`, validated against the range `get_artmode_settings("brightness")` reports, which is 0-10 on this panel. Confirmed both ways: 11 is refused before anything is sent, and a real change reads back
- [x] `frame slideshow 0`, which is all the firmware accepts. T5 has why the `N`, `--ordered` and `--category` half isn't buildable, and they are off the interface now, with a non-zero argument refused before a connection is opened
- [x] `frame mattes`, listing the sets per orientation out of `mattes.py` rather than echoing `get_matte_list()`'s ten, with every color and its triple, and saying so if the TV reports a type or color that module has no record of
- [ ] `frame matte <matte_id> [--only <content_id>]` applying to the whole inventory by default. T14 killed the cheap version: `change_matte()` does nothing, so applying one means re-uploading the photo and rewriting its inventory entry. `FrameTv.upload()` takes the image's dimensions with the matte and validates the pair through `mattes.py`, so what's left is the command: read each image's shape from `available()`, since the inventory doesn't record it, then re-upload
- [x] `frame status`, reporting lit or dark off REST, art mode, brightness, what's showing, and what the inventory does and doesn't account for
- [x] Tests for the sync diff and the crop math
- [ ] Loud failures on auth and network errors, since a silent no-op is the realistic failure mode. The TV paths are done: every failure in `tv.py` raises a `TvError` subclass, `cli.py` prints it as one sentence and exits non-zero, a request the firmware never answers is cut at 30s rather than hanging, and the three connect failures that arrive as one exception are told apart by event name and elapsed time. The source and sync paths are what's left


## Later

- [ ] `launchd` plists for the nightly art mode on/off schedule. They pass `--retry`, which is what makes a scheduled run wait out the TV's ten seconds and reconnect once where a manual run fails immediately
- [ ] Bound the handshake window too, if it ever hangs. The deadline in `tv.py` works by cutting the socket, which needs the socket, and the library doesn't expose it until the handshake is done, so that one window is bounded only by the library's own socket timeout. Running the call on a daemon worker thread and joining it with the deadline would cover it, at the cost of a thread that can't be reclaimed. Nothing has been observed hanging there, so this is a contingency rather than a gap to close now
- [ ] Day and evening brightness swap, if it turns out to be worth it
- [ ] Local folder source
- [ ] Museum art source over IIIF (Art Institute of Chicago, Rijksmuseum, the Met)
- [ ] Cloudflare Worker for the fetch and prep half of sync, with a local job doing the push
- [ ] Album organization on the TV, if the spike says it's possible
- [ ] Notice a photo edited in Google Photos after it was uploaded, which today's inventory can't see. `inventory-and-sync.md` has the two candidate fingerprints and the few-minute probe that says whether the free one works
- [ ] Sources wider than 16:9, which no standard phone photo mode produces. `-n` and `-c` have only ever been observed trimming height, so the horizontal crop offset is unverified and a panorama's framing is unknown
