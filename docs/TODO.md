# TODO

Scope and rationale are in `../CLAUDE.md`. This is just the running task list.

Every Google spike is answered, every matte spike is answered, and every MVP command is built except `frame matte`. **What's left is nearly all on the wall rather than in the code:** picking a matte type and color by looking at the panel, which the matte test below is for, and then one full re-upload once they're chosen. Everything still open under Spikes needs the TV and somebody watching it, and only one process can hold the art channel at a time, so check `git worktree list` before starting anything that connects.

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
- [ ] T18. Find out why a long run of uploads knocked the Art app out of AMP. The first real sync did 93 back to back, the 94th never answered inside 120s, and the TV was afterward playing a live TV show that the Art picker couldn't get out of until a reboot, with nobody touching the remote. `frame sync` now times every upload and prints fastest, median, slowest and the mean of the last ten, so the next run answers the first question for free: times climbing toward the deadline mean the app wearing down under load, and a flat series ending in one hang means a single event. Only the first reading makes `tv.upload_pause` worth setting. The run's log is worth keeping either way, since it names the image each upload was carrying. Run it with `--debug` next time, which is what catches an `art_mode_changed` frame and so times the moment the TV left AMP against the upload it was on. The leading guess is the D2D socket: `api_version` is `5.0.0.0` and `get_api_version` goes unanswered, so `upload()` takes the socket path rather than the `0.97` binary one, and that opens and closes a fresh TCP connection to the TV for every single photo. 93 of those in a few minutes is the kind of thing that exhausts a listener and recovers on a reboot, which is exactly the shape of what happened. Sampling `netstat -an | grep <tv-ip>` during the next run says whether the sockets pile up.

  **The second run answered both halves and cleared the load theory.** 86 uploads back to back, all of them successful, at 3.8s fastest, 4.7s median, 6.5s slowest, and 4.4s over the last ten -- so the series ends slightly *faster* than its own median and there is no degradation to find. Connections to the TV held steady at about 8, one established and the rest in `TIME_WAIT`, and drained to nothing when the run ended, so nothing is piling up on this side either. No `art_mode_changed` frame appeared anywhere in the log. On that evidence the first run's death was a single event rather than the Art app wearing down, `tv.upload_pause` has nothing to fix and should stay at 0, and what is left to explain is what was particular about that one moment. Worth leaving open rather than closing, since one clean run doesn't prove the next one is
- [x] T19. Find out whether uploading changes what the panel shows -- it doesn't. Across 86 uploads the panel stayed on the image it started on, so a sync is visually silent and a nightly job won't flicker the wall. The other half of that is the problem: nothing this tool does ever changes the displayed image, and T5 established that a slideshow can be turned off and never on, so the wall would sit on one photo forever. **T21 is what to try before writing any code for that**, because the TV can obviously do this itself and only the API refuses to start one
- [ ] T21. **Do this before building any rotation.** Turn the slideshow on by hand from the TV's own menu, then disconnect and leave it alone for a few hours, and see whether it keeps rotating. T5 proved only that the *API* refuses to start one; the TV plainly runs slideshows, since it rotates the Art Store by itself and the setting exists in its UI. This project is a CLI rather than a daemon precisely because the TV keeps its own state after the script disconnects, and a slideshow interval is exactly that kind of state. If a hand-set one persists, rotation costs no code at all and the `frame select` item below never gets built. Worth checking `get_slideshow_status` afterward too, since the getter does answer, so a persisting slideshow would be readable even though it can't be set
- [ ] T20. Find out why the Art app wedged when the picker was opened over 183 images. It went slow, drew partially, showed the art window for a second, and went black, and it has not recovered: REST reports `PowerState: on`, so the set is awake rather than in standby, while the art channel accepts a connection and then never completes the handshake, timing out on `connect` at 45s. No API call can help, because every one of them needs the channel that is stuck, so it takes a power cycle. What this shares with T18 is thumbnail work, the first crash during a bulk upload and this one on opening a picker that had 183 items to draw, and `get_thumbnail` already hangs on this firmware, which says thumbnails are a weak spot. Storage is not it: 86 uploads came to 54 MB, median 606 KB and 1.5 MB at the largest, so the whole album is about 110 MB against roughly 6 GB. The lever, if the count really is the problem, is `pipeline.jpeg_quality` at 85 rather than 95, which roughly halves the bytes and may make no difference to the TV at all
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
- [x] `frame sync`, including deletes scoped to the inventory. `syncer.py` runs a plan and `sync.py` stays pure. Photos are fetched and prepared into a spool before the channel is opened, because it closes itself after about 25 seconds of silence and a live fetch between two uploads would eventually outlast that. Uploads go before deletes, the inventory is saved after each upload, a delete is confirmed by re-reading `available()` rather than by trusting the call, and a run with no inventory refuses when the TV already holds something unless `--first-run` says none of it is this tool's. Verified against the TV over two runs: the first died at upload 94 and the second finished the remaining 86 clean, which between them exercised the resume path, the timing summary, and an abort's report
- [x] `frame art-mode on|off`, where `on` reads REST first and does the off-then-on toggle when the panel is dark. All three states confirmed: `off` leaves the panel lit showing the TV's own UI, `on` from lit is a plain call, and `on` from a dark panel does the toggle and flips REST back to `on`
- [x] `frame brightness N`, validated against the range `get_artmode_settings("brightness")` reports, which is 0-10 on this panel. Confirmed both ways: 11 is refused before anything is sent, and a real change reads back
- [x] `frame slideshow 0`, which is all the firmware accepts. T5 has why the `N`, `--ordered` and `--category` half isn't buildable, and they are off the interface now, with a non-zero argument refused before a connection is opened
- [x] `frame mattes`, listing the sets per orientation out of `mattes.py` rather than echoing `get_matte_list()`'s ten, with every color and its triple, and saying so if the TV reports a type or color that module has no record of
- [ ] `frame matte <matte_id> [--only <content_id>]` applying to the whole inventory by default. T14 killed the cheap version: `change_matte()` does nothing, so applying one means re-uploading the photo and rewriting its inventory entry. `FrameTv.upload()` takes the image's dimensions with the matte and validates the pair through `mattes.py`, so what's left is the command: read each image's shape from `available()`, since the inventory doesn't record it, then re-upload
- [x] `frame status`, reporting lit or dark off REST, art mode, brightness, what's showing, and what the inventory does and doesn't account for
- [x] `sync.short_run`, which mirrors the album down to the newest N photos of each orientation so that trying a matte on the wall costs a couple of minutes rather than an album. It still deletes what it leaves out, on purpose, since the point is to have only the handful under test on the panel. `sync.newest_per_orientation()` is the pure half and sorts on the shot time rather than the album's page order
- [x] Tests for the sync diff and the pipeline's size math
- [ ] Loud failures on auth and network errors, since a silent no-op is the realistic failure mode. The TV paths are done: every failure in `tv.py` raises a `TvError` subclass, `cli.py` prints it as one sentence and exits non-zero, a request the firmware never answers is cut at 30s rather than hanging, and the three connect failures that arrive as one exception are told apart by event name and elapsed time. The source and sync paths are what's left

- [x] **The cropping is gone, so the matte is the only thing that frames a photo now.** Ian flagged `PXL_20240122_003131287.jpg` as cropped badly, which was the T9 framing decision outliving its reason: T12 and T16 had since shown the TV frames a matted image whole, and nobody went back and asked that of a landscape. Both places that cropped are changed. `google_album` asks for the plain `=w1920-h1080` for every item rather than `-n` for a landscape, so a 4032x3024 now arrives 1440x1080 with nothing trimmed, and `pipeline._fit_to_panel()` only bounds the frame to the panel. `pipeline.crop_box()` is deleted, and cropping is not coming back as a config key -- Ian settled that on 2026-09-04, so a render record has no crop field to carry.

  Every photo in the album is 4:3 or 3:4 and not one is 16:9, so there was never a framing that both filled the panel and kept the whole photo; the choice was only where to make it. It is made in config now instead of in the upload, which is the part that matters, because a crop baked into a JPEG is permanent while a matte can be changed by re-uploading.

  **What is not settled is which landscape matte to use, and that is the next thing on the wall.** `flexible` is the only type whose aperture is known to take the image's own shape, and it is what `config.toml` and `config.example.toml` now say. Whether `modern`'s is fixed at 16:9 is still unrecorded, and if it is then a 4:3 gets cropped at display time and nothing about this change would have been visible under it. `modernthin` and `shadowbox` are unmeasured too. The matte test below is what answers all of them.

  One observation, not yet a finding: two photographs on 2026-09-04 of `MY_F0189` and `MY_F0192`, both stored 1920x1080 under `modern_polar`, put the displayed image at roughly 2:1 against the panel's 1.78, which would mean `modern` crops even an image that already matches the panel. It is one hand-held photograph per image, of uploads that predate the no-crop change, with no reference in frame and no `flexible` shot of the same photo to compare against, so it is a reason to run the test rather than a reason to rule `modern` out. Don't repeat it as established until a fresh set says the same thing.

  A re-upload of everything already on the TV is what actually puts this on the wall, since sync has no change detection and the photos up there are the cropped ones. Do it after the matte and color are picked, not before, and use `sync.short_run` to try them out first: changing either the crop or the matte costs the same full re-upload, and there is no reason to pay it twice

- [ ] setup test run when 1 landscape photo and 1 portrate are chose from the latest chronolgical in ablum and then uploaded 1 time each for each matte. then i'll pick my favorite. start with light grey or white matte. after i pick my fav matte then run the same test w/ my fav mat but upload 1 of each color and i'll pick my fav color. that'll give me the final values to save in my config.toml update example config with those choices

  **This needs no code, and the rest of this item is written so a fresh session can start it cold.**

  **Why it takes a re-upload per variant.** A matte is set at upload time and nowhere else: `change_matte()` refuses every image with `error -7` (T14), and sync has no change detection, so editing `art.landscape_matte` and re-running does nothing to a photo already on the TV. Each variant is therefore its own upload of the same photo.

  **How to get variants to coexist.** Every config file carries its own `inventory.json` beside it, and deletes are scoped to what that inventory claims, so a config in its own directory uploads its own set and leaves every other set alone. One directory per variant, each holding a `config.toml` copied from the main one with a different matte and `sync.short_run` set low. `sync.short_run = 1` is one landscape plus one portrait, since it takes that many of *each* orientation. The token file named in a variant config need not exist, because the art channel is tokenless and only `frame pair` reads it. Verified against `plan_sync` on 2026-09-04: a fresh inventory plus `short_run = 1` gives 2 uploads, 0 deletes, and everything already up there reported as unmanaged.

  ```
  uv run frame --config <variant-dir>/config.toml sync --dry-run
  uv run frame --config <variant-dir>/config.toml sync --first-run
  ```

  `--first-run` is needed on a variant's first run, because an absent inventory against a non-empty TV is the shape of a lost inventory and sync refuses rather than uploading the album twice. Wait about twenty seconds between two runs, or pass `--retry`, since the TV takes roughly ten seconds to notice the last client left.

  **What to compare, in two rounds.** Types first, then colors with the winning type. A landscape has six types to choose from -- `none`, `modernthin`, `modern`, `modernwide`, `flexible`, `shadowbox` -- and a portrait has two, `flexible` and `shadowbox`, plus `none` at the cost of 58% of its height. Then sixteen colors, `polar` and `antique` being the light end Ian wants to start from. **Never name a type outside the offered set for an orientation:** `modernwide` on a portrait puts an error dialog on the panel and needs a power cycle. `load_config` refuses one before anything is sent, which is the guard, but don't lean on it as a reason to try.

  **Two things about seeing the result.** Uploading never changes what the panel shows (T19), so each image has to be selected from the TV's own picker to be looked at. And the picker is what wedged the Art app in T20 at 183 images, so keep the total on the TV small while this is running; a few dozen has been fine.

  **Cleanup is by hand today.** A variant's photos can only be deleted by that variant's own config, and only when they leave its album, so in practice they come off through the TV's picker. The delete-flags item below is what would turn that into a command.

  **State as of 2026-09-06:** the TV holds ten photos, `MY_F0201` through `MY_F0210`, all `flexible_polar`, uploaded by the main config with `sync.short_run = 5`. Landscapes are 1434x1080 and portraits 813x1080, which is the first set uploaded since the crop was removed

- [ ] setup a config var that either deletes or appends to the tv, off by default. deleting means delete evryt photo from the tv that isn't in the album being imported. items in album will be skipped if they're already on tv, items in album that arent already on tv will be added. items that arent in album will be deleted from tv. ill turn it on in my config

### When everything is working on the TV

- [ ] Run through a series of manual tests with ian
- [ ] Have Fable run a thorough check for bugs, correctness, etc
- [ ] Have Fable run a security review


## Fast follow

- [ ] **`frame select`, and a scheduled job to rotate the wall.** T19 found that nothing here ever changes the displayed image and T5 found a slideshow can be turned off and never on, so as things stand the TV shows one photo indefinitely. `select_image(content_id)` is the way out: a wrapper method, a command, a `launchd` job, and a choice about what it picks, random or oldest-shown-first, the second of which means the inventory has to start recording what has been displayed. **T21 comes first**, because a slideshow set by hand may persist, and if it does then rotation at the TV's own three-minute floor costs no code at all. That only settles three minutes, though. Anything shorter is unreachable from both the TV's menu and the API, since `set_slideshow_status` takes whole minutes and refuses every non-zero one, so a 30 second interval needs `select_image` on a timer whatever T21 says
- [ ] **Rotate over a subset, which is organization done locally rather than on the TV.** Nothing on the TV can be organized: uploads all land in `MY-C0002`, nothing in `samsungtvws` creates a category, and `set_favourite` fails on this firmware. That stops mattering once `frame select` exists, because whatever picks the next image picks it locally and the inventory already records the source behind each `content_id`. What's missing is a way to say which subset -- a per-source grouping, a tag in the inventory, or several Google albums named in config. Worth the T8-sized phone check first, to see whether SmartThings can create an album on this model, since a real category would change the design




## Later

- [ ] `launchd` plists for the nightly art mode on/off schedule. They pass `--retry`, which is what makes a scheduled run wait out the TV's ten seconds and reconnect once where a manual run fails immediately
- [ ] Bound the handshake window too, if it ever hangs. The deadline in `tv.py` works by cutting the socket, which needs the socket, and the library doesn't expose it until the handshake is done, so that one window is bounded only by the library's own socket timeout. Running the call on a daemon worker thread and joining it with the deadline would cover it, at the cost of a thread that can't be reclaimed. Nothing has been observed hanging there, so this is a contingency rather than a gap to close now
- [ ] Day and evening brightness swap, if it turns out to be worth it
- [ ] Local folder source
- [ ] Museum art source over IIIF (Art Institute of Chicago, Rijksmuseum, the Met)
- [ ] Cloudflare Worker for the fetch and prep half of sync, with a local job doing the push
- [ ] Notice a photo edited in Google Photos after it was uploaded, which today's inventory can't see. `inventory-and-sync.md` has the two candidate fingerprints and the few-minute probe that says whether the free one works
- [ ] Sources wider than 16:9, which no standard phone photo mode produces. `-n` and `-c` have only ever been observed trimming height, so the horizontal crop offset is unverified and a panorama's framing is unknown
