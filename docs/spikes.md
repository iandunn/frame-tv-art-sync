# Spikes

Questions to answer before writing the code that depends on them. Each one is throwaway: the deliverable is the finding written into this file, not the script. Put scratch scripts in `.claude/tmp/`.

Fill in the **Finding** block as each is answered, and note the date. If a finding invalidates something in `../CLAUDE.md`, correct CLAUDE.md too rather than leaving the two disagreeing.

None of these needed the project scaffolded, and a new one still doesn't: `uv run --with samsungtvws python <script>` is enough. Now that `pyproject.toml` exists, a script that only wants the project's own dependencies can be run with a plain `uv run <script>`.

Two independent groups. The Google ones need no TV. The TV ones need the TV powered on or in standby, on the same network, and `T2` done first because everything after it needs a token. Requests to the TV go to a LAN address, and two separate things block that on macOS. Claude Code's command sandbox refuses LAN sockets with `EPERM`, so TV commands need it disabled. Separately, macOS Local Network privacy gates the terminal's host application, VS Code or iTerm2, and a denial surfaces as `EHOSTUNREACH` / "No route to host" for every peer on the Mac's own subnet, while the gateway and anything routed through it keep working. Grant the host app under System Settings > Privacy & Security > Local Network and restart it before concluding anything about the network, and never read an empty port scan as evidence the TV is absent until a known-good LAN peer other than the gateway answers.


## Google shared album

`.claude/tmp/g_probe.py` re-runs all of these. Plain `uv run .claude/tmp/g_probe.py` prints the item table, the orientation-dependent suffix each item wants, and the G3 continuation token; `--crop <index>` re-derives the `-n` and `-c` offsets for one item. It reads the album URL out of `config.toml`, so it holds nothing account-specific and is the thing to run first if any of these findings ever stop matching reality.

### G1. Does a link-shared album's public page still embed fetchable item JSON?

This is the load-bearing assumption of the entire sync feature. If it's false, the project's only remaining options are the interactive Picker API or abandoning Google Photos as a source, and both are large enough to reopen the design.

Make a throwaway album with three or four photos, share it by link, then fetch the public URL with `curl` and no cookies and look at what comes back. The data has historically been in a JS array inside the HTML rather than a JSON endpoint, so expect to find it by searching the response for a known filename or for `https://lh3.googleusercontent.com`.

Record the shape of what you find, not just whether it worked. A future session needs to know which array index holds the id and which holds the URL.

**Finding (2026-09-01):** Yes. A plain `curl -sSL` with no cookies, no JavaScript, and no `User-Agent` gets the whole item list. The `photos.app.goo.gl` short link redirects to `https://photos.google.com/share/<album_id>?key=<key>`, and both halves are load-bearing: dropping `key` returns 404. Store the short link in config and follow the redirect, or store the resolved URL, but never store the album id alone.

The data is in an `AF_initDataCallback` block, not a JSON endpoint. Two blocks are present, `ds:0` is empty and `ds:1` is the album. Parse it by matching `AF_initDataCallback({key: 'ds:1', ... data:<JSON>, sideChannel: ...});` and running `json.loads` on the `data` value. It is real JSON, so no JS evaluation is needed.

`ds:1` is `[null, items, next_page_token, album, null, 0]`. Each entry in `items`:

```
0  "AF1Qip..."       media id, 40 chars
1  [ base_url, width, height, null x5, [...], [n] ]
2  1680452105564     shot time, ms
3  "Ga9a7emx..."     27-char per-item hash
4  -25200000         shot-time UTC offset, ms
5  1788277106538     added-to-album time, ms
6  [ owner_id ]
7  [ ... ]           feature flags
8  2
9  { ... }           feature map; "525000002" holds the album id
```

So the two fields sync needs are `item[0]` for the id and `item[1][0]` for the URL, with `item[1][1]` and `item[1][2]` giving the pixel dimensions as displayed, rotation already applied. The integer at `item[1][10][0]` looks like a byte size but does not match what any download actually returns, so don't treat it as one.

`item[2]`, the shot time, is read as well, because `sync.short_run` picks the newest photos of each orientation and that is the field that says which those are. The page listed its items in ascending shot-time order at the 2026-09-04 re-run, so the tail of the list is the newest, but nothing depends on that: the selection sorts on the value rather than trusting the order. `item[5]`, the added-to-album time, is not sorted and clusters by import batch, so it isn't a substitute.

`album` is a 43-element array. Index 0 is the album id, 1 the title, 19 the `key`, 21 the item count, and 32 the short link. Sync needs none of it; index 21 is worth reading only as a sanity check against `len(items)`.


### G2. Is there a stable per-item id on the public page?

Mirroring needs to recognize the same photo across runs. If the page exposes a durable media id, key the inventory on it. If the only stable thing is the image URL, check whether that URL survives a re-share of the album, because if it doesn't, fall back to hashing the downloaded bytes and accept that a re-encoded photo counts as new.

**Finding (2026-09-01):** Key the inventory on `item[0]`, the 40-character `AF1Qip...` media id. It is identical across repeated fetches, and so is the `lh3` base URL, so neither is session-scoped or rotated per request.

**Deliberately not verified:** whether the media id survives the album being unshared and re-shared under a new link. Testing it means breaking the current share link and re-pointing `config.toml`, which costs more than the answer is worth for an album that is shared once and left alone. It is a Photos media key rather than a share-scoped handle, which is the reason to expect it holds, but that is inference and it stays inference.

The failure is loud and self-correcting if the inference is wrong: every photo looks new on the first run after a re-share, so that run re-uploads the album and deletes the previous copies, and every run after it is stable again. That costs a few minutes of uploading and no data. It is written up in the README's troubleshooting section so it is recognizable when it happens. Byte hashing stays the fallback and is not needed now.

`item[3]`, the 27-character hash, is also stable, but there is no reason to prefer it over the id.

The album has since grown from three photos to 179. Of the three ids captured at the start, the two still in the album came back with the same id and the same base URL, and the third had been removed from the album, so adding and removing photos does not disturb the ones that stay. That is not the same as surviving a re-share, which is still the open half of this. `uv run .claude/tmp/g2_stability.py` re-checks it against the ids cached in `ds_1.json`.


### G3. Does the page contain the whole album, or only the first screenful?

Check with an album of 60 or more photos. If it paginates or lazy-loads, find out how the continuation works before designing sync around a single fetch.

**Finding (2026-09-03):** The page holds the whole album, so sync can be designed around a single fetch. At 179 photos the served HTML carried all 179 items, the album's own count field agreed, and `ds:1[2]`, the continuation token, was still `""`. It was also `""` at three photos and again at eight, so nothing about it has ever been observed populated.

That means the token's populated form and the request that redeems it are both still unknown, and 179 is a floor rather than a ceiling -- a limit could sit at some higher count. The page's HTML contains no `batchexecute` URL or rpc id, so the continuation call is built inside the JS bundle and can't be read off the served page; if a limit is ever hit, finding the redemption request means reading that bundle or watching the network panel.

The source refuses to return items at all when the token comes back non-empty, or when the album's own count disagrees with the number of items on the page. So a future limit fails loudly rather than quietly mirroring half the album and deleting the rest, which is why 179 is enough to build on.

Re-checked 2026-09-04, at 174 photos: the token is still `""` and the album's own count still agrees with the item list.


### G4. Can the image URL be size-parameterized?

Google's `lh3.googleusercontent.com` URLs have historically accepted suffixes like `=w1920-h1080`. If that still works, the pipeline can ask for something close to final size instead of downloading originals, which matters for a large album. Confirm what a parameterized URL actually returns, including whether it crops or letterboxes.

**Finding (2026-09-01):** Yes, and it is worth using. The bare base URL returns a 512x384 thumbnail, so a suffix is not optional.

| Suffix | Returns | Bytes |
| --- | --- | --- |
| none | 512x384 | 81 KB |
| `=w1920-h1080` | 1440x1080, fits inside the box, no crop | 328 KB |
| `=w1920-h1080-n` | 1920x1080, exact center crop | 376 KB |
| `=w1920-h1080-c` | 1920x1080, crop window at a quarter of the slack, not half | 355 KB |
| `=s1920` | 1920x1440, longest side | 499 KB |
| `=d` | 4032x3024, full size | 2.8 MB |

`-n` and `-c` were pinned down by scanning every vertical crop offset of the full-size image against what Google returned, across five photos at four frame sizes, including a 3:4 portrait. `-n` landed on `(h - crop_h) // 2` every time and `-c` on `(h - crop_h) // 4`, with the residual difference being JPEG noise. So `-c` is not the center crop its name suggests, and over that spread it isn't saliency-driven either; it is a fixed fraction of the frame, biased upward. Use `-n`. `g_probe.py --crop <index>` re-runs the scan.

The sized variants come back already rotated, with no EXIF orientation tag, and carry whatever ICC profile the original had; G4b has what that turns out to be across the album.

`=d` is the one to be careful with, because it keeps a fuller EXIF block and does not always bake rotation in. Its orientation tag was `1` on every photo in the original three, but the iPhone photos carry a real tag, landscape and portrait alike. A portrait is the case where that's obvious, because its pixel dimensions come back transposed against the dimensions reported in the JSON; a landscape needing a 180-degree flip has matching dimensions and looks fine. So apply the tag rather than trusting the pixels. That only affects anything comparing `=d` against a sized variant, since the sized ones are pre-rotated, and it is a quiet problem rather than a loud one: a misaligned comparison produces plausible-looking differences that have nothing to do with what was being measured.

Portrait originals obey the same rules, which is exactly the problem with them. `=w1920-h1080-n` on a 3024x4032 photo still returns 1920x1080, by keeping 1701 of 4032 rows, so 42% of the frame height. `-c` keeps the same slice a quarter of the way down instead. Neither is usable for portrait content. The plain `=w1920-h1080` is, because it fits inside the box rather than filling it and returns 810x1080 for a 3:4 photo: the whole frame at native resolution, no upscaling, and already bounded to the panel, so the pipeline passes it through untouched for the TV to mat.

**The plain suffix is the one to ask for, whatever the shape, and the cropping variants are not used at all (2026-09-04).** This finding originally had the source ask for `-n` on a landscape, on the strength of T9 having shown the TV center-crops an unmatted image at display time. T12 and T16 then showed that a matte whose aperture flexes makes the TV frame an image whole, which took the crop off the landscape as well as the portrait, so `-n` buys nothing and costs a quarter of a 4:3 photo's height permanently. The whole framing decision now lives in the configured matte, and the source asks for `=w1920-h1080` for every item. Re-measured on 2026-09-04 against the live album: 4032x3024 comes back 1440x1080 and 3024x4032 comes back 810x1080, both untouched by the pipeline, and the bare and `-n` forms still return 512x384 and 1920x1080 as the table says.

The `=s1920` row is worth remembering as the way to ask for more pixels than the panel: it returned 1920x1440 on a 4:3, and `=w1920-h1920` does the same. Neither is used, because the TV scales an image into an aperture smaller than the panel anyway, so anything past the panel's own bound is bytes for nothing.

Two gaps were left open here about color and about crop geometry. G4b settled both.


### G4b. Does the album ever hand the pipeline a wide-gamut original, or one wider than 16:9?

Both of G4's loose ends were about inputs it never saw. Read the ICC profile of every item's original to find out whether anything tags Display P3, and check whether any source is wider than 16:9, since `-n` and `-c` had only ever been observed trimming height.

`uv run .claude/tmp/g4b_icc.py` prints the profile of every original, range-fetching only the head of each because the profile sits in an APP2 marker near the start of the file. `uv run .claude/tmp/g4b_p3_convert.py <index>` is the follow-up that settles what the resizer does with a wide-gamut one.

**Finding (2026-09-03, 179 photos):** Wide-gamut originals are already in the album, so this is a live case rather than a future one, but it needs no code because the profile travels with the pixels.

Across all 179 originals: 87 are `sRGB IEC61966-2-1 black scaled`, 66 are `sRGB IEC61966-2.1`, 17 carry no profile at all, 6 are `Display P3`, 2 are plain `sRGB`, and 1 is `Apple Wide Color Sharing Profile`. So seven items are wide-gamut. All seven are 4032x3024 or smaller, and the Apple profile alongside them points to an iPhone rather than the Pixel; every 4080x3072 item, which is the Pixel 6's 50 MP binned down 4:1, is sRGB.

**Re-run 2026-09-04, at 174 photos:** 82 `black scaled`, 65 `IEC61966-2.1`, 17 untagged, 6 `Display P3`, 2 plain `sRGB`, 1 `Apple Wide Color Sharing Profile`, and 1 whose profile sits past the 384 KB head the script range-fetches, so it reads as truncated rather than as anything about the image. Still seven wide-gamut, and the conclusion is unchanged. The album is smaller than it was because photos have come out of it since, which is worth knowing before treating any count in this file as current.

What the resizer does with them is the part that mattered, since a resizer that re-tagged without converting would send oversaturated color to the TV. It does neither. It passes the pixel numbers through untouched and preserves the original's profile: `Display P3` in, `Display P3` out. Reconstructing both hypotheses locally from the full-size original and comparing against the served bytes puts the passthrough reconstruction at a luminance-weighted mean absolute difference of 0.11 to 0.12 out of 255, and the converted one at 1.8 to 3.9, and on the one item served at native size with no resampling at all the passthrough difference is exactly 0.00.

So the tag always describes the pixels honestly, which is what makes this safe. `_to_srgb()` in `pipeline.py` honors an embedded profile rather than retagging, so a P3 sized variant converts correctly on the way to the TV, and an untagged one falls back to assuming sRGB, which is the right reading of an untagged JPEG.

Two smaller observations came out of the same sweep. The resizer rewrites an sRGB source's profile to its own `black scaled` flavor while leaving the numbers alone, so a changed profile name is not evidence of a changed image. And 87 originals already carry that same `black scaled` profile, meaning Google re-encoded them at upload time rather than storing what the camera produced.

The aspect ratio half is settled by absence, and it turned out to matter for a reason this spike wasn't asking about. Everything in the album is 4:3 or 3:4 -- 121 landscape and 53 portrait at the 2026-09-04 re-run, and not one item at 16:9 -- so nothing is wider than 16:9 and the horizontal crop offset stays unmeasured. **The other consequence is that no photo in the album fits the panel,** so there is no framing that both fills the screen and keeps the whole photo, and which one to give up is a matte choice rather than a pipeline one. That is out of MVP scope rather than unreachable: a phone's standard photo modes don't produce one, but a panorama would, so the offset gets measured whenever the first such photo shows up.


## The TV

### T1. Does the TV answer, and does it report token auth?

```
curl -s http://<tv-ip>:8001/api/v2/ | jq
```

Unauthenticated, so it needs no setup. It returns the model, device id, and `TokenAuthSupport`. If this works, the network is fine and anything that fails later is auth or library level. Record the model string and firmware version, because the firmware is what the later spikes are really testing.

Set a DHCP reservation for the TV while you're here. The MAC is under Settings > General > Network > Network Status > IP Settings.

**Finding (2026-09-01):** The TV answers, and `TokenAuthSupport` and `FrameTVSupport` are both `"true"`. Every value in the document is a string, including the booleans, so anything reading it has to compare against `"true"` rather than trust a JSON bool.

```
modelName    QN32LS03CBFXZA        the retail model
model        23_KSUE_FTV_T09       2023, Frame TV, panel gen T09
resolution   1920x1080
duid/id/udn  uuid:<...>            all three are the same value
wifiMac      <the TV's MAC>        the address to reserve
networkType  wireless
version      2.0.25                the remote API version, not the firmware
```

`resolution` is the TV's own answer, and it confirms the panel is FHD rather than 4K, so the pipeline's `1920x1080` target is right.

The spike asked for a firmware version and the TV will not give one: `firmwareVersion` is the literal string `"Unknown"`. So there is no way to tell from this endpoint whether a Tizen update has landed, and the later spikes can't be pinned to a firmware build. `version` looks like a substitute but is not one, because it tracks the remote API rather than the firmware. If a future failure needs a build number, it has to come off the TV's own Settings > Support > About This TV screen.

If the TV is fenced off from the network the controlling machine is on, whatever rule permits `8001` and `8002` through is the first thing to check when a run that used to work stops answering.


### T2. Does pairing work, and does the token survive a second run?

Everything below depends on this. Before running it, set Settings > General > External Device Manager > Device Connect Manager > Access Notification to `First Time` or `On`, or the connection fails silently.

Connect on port `8002` with a `token_file` path and a fixed `name`, accept the on-screen prompt within about 30 seconds, then call `art.get_artmode()`. Run the same script a second time and confirm no prompt appears.

Don't use `art.supported()` as the check. It reads `FrameTVSupport` off the same unauthenticated REST document as T1 and never opens the WebSocket, so it returns `True` on an unpaired TV. `get_artmode()` is the first call that actually needs the art channel.

If the prompt never shows, clear the entry from Device List in that same TV menu before retrying, because the TV remembers a denial and won't ask again.

**Finding (2026-09-01):** Pairing works, but several things about it differ from what the research and the library assume. The first three headings below are the pairing findings; T2b and T2d after them are the panel-state work that grew out of it.

**The TV showed the pairing prompt only once the client was on its own subnet.** With the Mac and TV both on the `Private` subnet, the prompt appeared on the first connection, Allow worked, `frame-tv-art-sync` showed up in Device List, and the second connection skipped the prompt. From a different VLAN the same request was refused by the TV in 40ms with no prompt, every time, and nothing on the TV or the gateway changed that. The refusal was the TV's own decision rather than a network artifact, because the connection reached the TV's application layer and it answered with a JSON event, and macOS Local Network privacy, which confused everything else that night, never applied to routed traffic. What is not yet proven is that the subnet is the *reason* rather than something correlated with it. T2c settles that: from `Private`, with the TV back on a VLAN of its own, connect once as the now-allowed `frame-tv-art-sync` and once with a fresh name. If the fresh name gets the 40ms refusal, the subnet explanation stands.

To run T2c, from iTerm2 with the TV at its address on the other VLAN and in Art Mode: `uv run python .claude/tmp/t3_readonly.py <ip>` is the allowed-client test, and `uv run python .claude/tmp/diag_names.py <ip>` is the fresh-name probe, since it includes a `probe-<timestamp>` name the TV has never seen and prints how long each refusal took. Both scripts already exist.

**T2c finding (2026-09-02): the subnet is the reason, and Device List doesn't override it.** With the TV on a VLAN of its own and the `! Network Private` deny from `network.md` enabled, `t3_readonly.py` running from `Private` as the allowed `frame-tv-art-sync` got `ms.channel.timeOut` from the art channel before it could send a request. `diag_names.py` got the same answer on `8002` for the allowed name, `SamsungTvRemote`, and a never-seen probe name, each in 80-160ms, and no prompt appeared for any of them. REST on `8001` answered normally through the same path, so the gateway and the stateful deny aren't in the way; the TV's multiscreen service refuses every WebSocket client whose address is off its own subnet, and being in Device List doesn't change that. The likely mechanism is a source-address check against the TV's own subnet mask, which is what Home Assistant users on VLANs report hitting with these TVs, and their workaround is source NAT at the router so the client appears to come from the router's address on the TV's subnet. That's inference from those reports rather than anything observed here, though. What is observed is that the gateway has nothing to do with it: with the `! Network Private` deny disabled and both leftover allow rules enabled, the same allowed client from `Private` got the same `ms.channel.timeOut` in 40ms, and the Mac moving back to `Private` after a working session on the TV's VLAN flipped the answer from working to refused with nothing else changed.

The TV was ultimately moved onto the `Private` VLAN on its own `Fenced` SSID, behind an EAP ACL that permits only the controlling machine and the gateway, and `t3_readonly.py` runs clean from there. `network.md` has the network design. The rest of this finding is the earlier work that established the subnet rule.

Moving the Mac onto the TV's own SSID, so that it shared the TV's subnet, made the art channel work again: `get_artmode()` and `get_brightness()` answered in well under a second, four runs out of four once the conditions below were met. So the design consequence is firm: whatever runs `frame` needs an address on the TV's subnet, and `network.md` has the options.

Getting to those four clean runs took an hour, because three unrelated things each produce the same symptom, an accepted WebSocket upgrade followed by silence:

* **The TV was on the select-art screen**, which is the one foreground state already known to accept the connection and never send `ready`. Switching to Art Mode proper fixed that part.
* **The token file was stale.** Device List had been cleared on the TV, which invalidated the remote channel's token, and `samsungtvws` sends whatever is in `token_file` on the art channel too. A token the TV doesn't recognise gets silence rather than a refusal. Spaced 60s apart, `token_file=None` answered 2 of 2 and the stale file hung 2 of 2, so this is solid. The art channel never issues a token, so the wrapper should open it with no token at all and reserve the token file for the remote channel.
* **Back-to-back connections with the same client name interfere with each other.** The TV takes on the order of 10s to notice a client has gone, and a new connection with the same name in that window either gets silence or receives the previous session's `ms.channel.clientDisconnect` as its first frame, which `samsungtvws` treats as a failed connect. Probes fired a few seconds apart gave contradictory answers all night until they were spaced a minute apart. The wrapper should hold one connection per invocation rather than reconnecting per call, and anything that retries should back off by tens of seconds.

**The allow is tied to the client's address, not only its name.** With the TV later moved onto the `Private` VLAN and the controlling machine back on `Private` at a different address than the one T2 paired from, the first connection as `frame-tv-art-sync`, the name Device List already held, sat silent for 30s and ended in `ms.channel.timeOut`, which is the genuine missed-prompt pattern rather than the instant refusal. The next attempt got `connect` and `ready` 8s in, the moment the prompt on screen was accepted, and `t3_readonly.py` then ran clean. So a controlling machine that changes address, by moving between Wi-Fi and Ethernet for instance, should expect one more prompt, and a scheduled job can't recover from that on its own. Device List afterward held two `frame-tv-art-sync` entries, one per address, so the TV keys entries on name plus address rather than re-keying a single one. Both were cleared and both channels were paired again from the new address with `.claude/tmp/pair_both.py`, each prompting once and answering about 9s in when the prompt was accepted; the remote channel wrote a fresh 8-character token.

Deleting and re-adding the Device List entry was tried along the way and there's no evidence it helped; the failures before it are explained by the select-art screen, and the ones after it by the token it invalidated. The remote channel now needs re-pairing before T2d resumes, since its token is gone.

**There is no token on the art channel.** After Allow, the `com.samsung.art-app` channel's `ms.channel.connect` frame carries only the client list, with no `data.token`, so connecting to the art channel never writes the token file. Authorization there is the Device List entry, keyed on the client `name`, and a second connection with the same name and no token is accepted straight away. The `samsung.remote.control` channel is different: it needs its own Allow prompt, and once accepted it issues a token that the library writes to the token file. That channel turned out to be needed after all, for the power key in T2d, so `frame pair` has to open both channels, and the token file stays in the design for the remote one.

**The art channel only works while the TV is in Art Mode.** On the Art Mode settings screen, where you pick a photo, the connection is accepted but `ms.channel.ready` never arrives and no request is answered, and `samsungtvws` blocks forever on that event. The moment the TV was switched into Art Mode proper, `ready` came within 60ms of connecting and every request was answered. So `ms.channel.connect` without `ready` means "the Art app isn't in the foreground", not "not paired". The channel does answer with art mode off and with the panel dark, so the rule is narrower than "only in Art Mode"; the one state seen not answering is the Art Mode settings screen.

**T2b, the panel states (2026-09-01).** The Frame has three states the API sees differently, and the research had two of them wrong.

```
                          what you see         REST PowerState   get_artmode()   art channel
lit, showing art          artwork              on                on              answers
long-press "off"          black panel          standby           on              answers
set_artmode(False)        Tizen UI / input     on                off             answers
```

The dark panel is the remote's long-press. The TV is fully awake behind it, every art request is answered, and `get_artmode()` still says `on`, so REST `PowerState` is the only way to tell lit from dark. `set_artmode(False)` on a lit TV does not darken anything; it exits to the last input, which was the gallery picker in the test and would be live TV or Apple TV in normal use. There is no art-channel call that produces the dark state, and T2d below has not yet found a remote-channel key that does either.

Waking a dark panel from the network works, but only as a toggle. In `standby`, `set_artmode(True)` alone hung with no reply and changed nothing. `set_artmode(False)` was answered and left the panel dark, and `set_artmode(True)` three seconds later was answered with REST flipping to `on`. `select_image(..., show=True)` was also answered on the lit panel, and Wake-on-LAN from the same subnet was sent but the panel was already lit, so neither of those is known to wake anything. `frame art-mode on` should read REST first and do the off-then-on toggle when it says `standby`.

**T2d, darkening the panel over the network (2026-09-01, parked).** Parked on purpose after three attempts; pick it up when the sleep/wake schedule is next in scope, not before. The `samsung.remote.control` channel pairs separately from the art channel: it showed its own Allow prompt, accepted in about 9 seconds, and unlike the art channel it *does* issue a token, 8 characters, which `samsungtvws` wrote to the token file. So the token file isn't dead after all; it belongs to the remote channel, and the art channel ignores it either way. The remote channel's Device List entry appears to be the same one, since the name is the same.

Every `KEY_POWER` variant tried so far behaves as a short press, which on the Frame toggles between Art Mode and TV. `hold_key("KEY_POWER", 3)`, a bare `Press` with no `Release`, and `hold_key("KEY_POWER", 1.5)` all did the same thing: the panel went black for a moment, then landed on TV playing or back in Art Mode, and REST `PowerState` polled once a second never left `on`. The black was the input switch, not standby. So the earlier "darkened and then woke" reading of the 3-second hold was wrong; it never went to standby at all. The remaining candidates are `KEY_POWEROFF`, longer holds, and `Press` followed by a `Click` instead of a `Release`; `.claude/tmp/t2d_keys.py` tries them in sequence. Until one leaves REST at `standby`, the nightly `off` has no working mechanism, and the fallback design would be to leave the panel lit and only schedule brightness, or to accept the motion timer as the off.

For the record, the toggle wake was re-run on a lit TV showing TV input: `set_artmode(True)` alone worked there, since the panel was already lit, and only the dark-panel case needs the off-then-on pair.

Answers from the first working session, all with the TV in Art Mode:

```
api_version              5.0.0.0
get_api_version          not answered on this firmware; the library falls back to api_version
get_artmode_status       on
get_device_info          resolution_type FHD, tv_flash_size 16, support_brightness_sensor TRUE,
                         support_motion_sensor TRUE, support_color_tone TRUE, support_myshelf FALSE,
                         current_rotation_status 1
```

The TV also pushes unsolicited `d2d_service_message` frames on the same socket, such as `recently_set_updated`, so anything reading replies has to match on `request_id` rather than take the next frame. The library already does.

`get_artmode_settings()` gives the wrapper its validation ranges, and it is the one place they're published:

```
brightness                 5     range 0-10
color_temperature          0     range -5..5
motion_sensitivity         2     range 1-3
motion_timer               off   one of off 5 15 30 60 120 240
brightness_sensor_setting  on
```

Its `data` field is JSON inside a string, the same shape as `content_list` in T5, so it needs a second `json.loads`. `get_brightness()` and `get_color_temperature()` return the same two values as bare strings.

Two things to be careful of in the wrapper. The websocket `timeout` passed to `samsungtvws` does not end a hang: a blocked `recv` outlasted a 20s timeout by a minute in one run and a 45s one indefinitely in another, whether waiting for `ready` or for a reply the firmware never sends. The likely reason is that the TV keeps the socket busy with control frames, so it's never idle long enough to trip the timeout, but that's inference. Either way the wrapper needs its own deadline, a watchdog thread or a `select` with a wall-clock budget, and should fail loudly when it fires. And `tv_flash_size 16` is the flash chip, not the space available for art; the roughly 6 GB figure in `CLAUDE.md` is the one to plan around until `available()` says otherwise.

The cross-VLAN question is settled by the T2c finding above: a client already in Device List is refused from another VLAN exactly like an unknown one, so the TV and whatever drives it have to share a subnet.

**Cross-subnet detail, for the next time it looks like a missed prompt.** The TV answers `{"event": "ms.channel.timeOut"}` about 40ms after the WebSocket upgrade. It is not waiting 30 seconds for anyone; it is refusing outright, and no on-screen prompt is ever shown. So `ms.channel.timeOut` means "the TV declined to start the pairing flow", and only a delay of tens of seconds before that event would mean a genuinely missed prompt. Time the handshake before concluding anything about the prompt.

What the refusal is not:

* Not the client name. A name the TV had never seen got the same 40ms refusal, so it is not a remembered denial and clearing Device List does not help.
* Not art-specific. `samsung.remote.control` and `com.samsung.art-app` both refuse identically, so the multiscreen service is refusing rather than the Art app.
* Not Access Notification. It was off at first, and turning it on changed nothing.
* Not Remote Access, the separate toggle under Network > Expert Settings. That was also off, and turning it on changed nothing either.
* Not the gateway ACL in `network.md` that denied the TV's VLAN to `Private`. Disabling that rule changed nothing, which is a useful negative, because it means the TV really does not need to open anything back toward the controlling machine before it will show the prompt.
* Not the TV's foreground state. Refused identically on the Tizen home screen, with Apple TV playing, and with Device List holding no entry for this client.

REST is unaffected throughout: `/api/v2/` answers on both `8001` and `8002`, and the TLS handshake and WebSocket upgrade both succeed, so this is an application-level refusal rather than anything about the network path. On `8001` the two channels answer differently, `samsung.remote.control` with `ms.channel.unauthorized` and `com.samsung.art-app` with `ms.channel.timeOut`, which is only worth knowing so that `unauthorized` is not read as progress.

The same-subnet test was done by joining the TV to a second SSID on the `Private` network rather than by moving the Mac to the TV's VLAN, for two reasons that each cost an hour:

* **macOS Local Network privacy was blocking every same-subnet peer.** With the permission off for VS Code and iTerm2, the Mac could reach the gateway and anything routed through it, but every host on its own subnet failed with `No route to host` and an incomplete ARP entry. That looked exactly like AP client isolation and it isn't; enabling the two apps under System Settings > Privacy & Security > Local Network fixed it, and VS Code has to be restarted for its terminals to pick the grant up. The gateway being reachable is not evidence the permission is granted.
* **The TV won't join the `Private` SSID, and Omada logs it as a wrong password.** A second `WPA2-PSK` SSID on the same network, with a different set of symbols in its passphrase, joins fine. Whether the mixed `WPA2-PSK/WPA3-SAE` mode or a symbol in the passphrase is the cause hasn't been isolated; `network.md` has the state of it.


### T3. What is already on the TV?

Dump `art.available()` to JSON and keep it. This is the baseline inventory, and it's what tells your own future uploads apart from art that was already there. Do this before uploading anything.

Record which fields each item carries, since the inventory schema should follow the TV's rather than invent its own.

**Finding (2026-09-01):** The TV is effectively empty. `available()` returned one item, and it isn't a user upload:

```json
{
  "content_id": "SAM-S10004103",
  "category_id": "MY-C0008",
  "slideshow": "true",
  "matte_id": "NONE",
  "portrait_matte_id": "SHADOWBOX_ANTIQUE",
  "width": 3840,
  "height": 2160,
  "image_date": "",
  "content_type": "server"
}
```

`SAM-S...` ids with `content_type: server` are Art Store items, and `MY-C0008` is the Store category as documented. The raw dump is in `.claude/tmp/available.json`. A second run a few minutes later returned a different single item, `SAM-S1110685`, and in both runs it was the one `get_current()` said was on screen. So on this TV `available()` includes the live Art Store stream image, which changes as the stream rotates, and it is not a stored item at all. The baseline inventory therefore has no user content in it, and the first `frame sync` starts clean, but the sync diff has to filter on `content_type` or the `SAM-` prefix rather than trust the list, or it will see a new unknown item on every run and a vanished one on the next.

Fields per item are exactly those nine: `content_id`, `category_id`, `slideshow`, `matte_id`, `portrait_matte_id`, `width`, `height`, `image_date`, `content_type`. Two things about their values matter for the wrapper. `slideshow` is the string `"true"` rather than a bool, like everything else this API returns. And matte ids come back in upper case, `NONE` and `SHADOWBOX_ANTIQUE`, while `get_matte_list()` reports the same values in lower case, so every matte comparison has to be case-insensitive and the wrapper should pick one case to emit.

`get_current()` reported the same `SAM-S10004103` but with `category_id: ARTSTREAM` and `content_type: artstore`, so the currently displayed item describes itself differently from how `available()` lists it. Don't join the two on category.


### T4. Does an upload and delete round trip work?

The mirror design assumes deletes work. Verify it with one throwaway image: `upload()` it with an explicit `matte`, confirm the returned `content_id` shows up in `available()`, then `delete()` it and confirm it's gone.

Watch what `upload()` does about size and aspect ratio if you hand it something that isn't 1920x1080, because that tells you how much the pipeline has to do.

**Finding (2026-09-02, upload confirmed, delete still open):** Uploads work and are unfussy. Four generated test images went up in one connection: 1920x1080, 4032x3024, and two copies of an 810x1080 portrait. `.claude/tmp/t4_images.py` builds them, and each is labeled with its own dimensions and carries a yellow tick 5% in from every corner, which is what makes a crop visible on the panel.

The returned `content_id` is `MY_F0001` and upward, with an **underscore**. That matters because the category ids use a hyphen, so `MY_F0001` and `MY-C0002` are different shapes and a pattern written for one won't match the other.

Uploads land in `MY-C0002` on their own, with `content_type: mobile`. Nothing has to ask for a category and there is no way to choose one.

**`available()` is not a list of items, it's a concatenation of per-category listings, and the same `content_id` appears once per category it shows up in.** A later run returned `MY_F0001` and `MY_F0002` twice each, once under `MY-C0008` and once under `MY-C0002`, and the Store item twice under `MY-C0008` with different `slideshow` flags. So a `category_id` read off one row does not tell you where an item lives, and anything treating the response as a flat inventory will double-count. Dedupe on `content_id` before doing anything else, and don't use `category_id` from `available()` to decide whether something is yours -- that's what the inventory file is for.

No size ceiling was found. 2.76 MB and 4.78 MB both uploaded without complaint, well past the roughly 2 MB the image pipeline expects to produce at quality 95, so nothing has to be tuned down to fit.

`upload()` sends bytes and nothing else -- the source does no resampling -- so any resize is the TV's. It does resize, and it does not crop: 4032x3024 came back stored as **1920x1440**, the long edge capped at 1920 with the 4:3 aspect ratio preserved. The 810x1080 portraits came back untouched at 810x1080, so it doesn't upscale either. Storage is a size bound, not a framing decision. The framing happens at display time instead, and T9 has what that turns out to be.

`matte_id` and `portrait_matte_id` both round-trip exactly as sent, lowercased, and independently of each other.

Two requests wedge the art channel rather than failing: `set_favourite` (see T7) and `get_thumbnail`. Both hang until the watchdog kills the run, and neither ever returns. Losing `get_thumbnail` means `available()`'s reported dimensions are the only machine-readable account of what the TV kept, and anything about display has to be photographed off the panel.

Delete works. `delete()` returned `True` for all eight test uploads and re-reading `available()` confirmed they were gone, which is the check worth keeping since the bool alone proves nothing. `.claude/tmp/t4_cleanup.py` does it, and refuses any id this spike didn't upload.

Deleting the last user image made a `SAM-F0222` appear with `content_type: preinstall`, which is a fourth content type alongside `mobile`, `server` and `artstore`. So the TV falls back to bundled art when My Pictures empties, and anything filtering the TV's own content needs to cover `preinstall` too rather than just the rotating `server` stream.

Two traps worth writing down because both cost real time here. A watchdog that calls `os._exit` skips flushing stdout, so every line the run had already printed is thrown away and an ordinary stuck request looks like a channel that never answered at all -- flush before exiting or the diagnosis goes to the wrong place entirely. And the TV closes an idle art channel inside 25 seconds, so anything that holds a connection while waiting has to poll rather than sleep.


### T5. Which slideshow endpoint does this firmware honor?

`set_slideshow_status` and `set_auto_rotation_status` take the same arguments and reportedly vary by firmware. Try each with a short duration, then read `get_slideshow_status()` back and watch the TV to see whether it actually rotates. Record which one works so the wrapper doesn't have to guess.

**Finding (2026-09-02, set side): this firmware can turn a slideshow off and cannot turn one on.** `set_slideshow_status` accepts `duration=0` and returns a normal response. Every non-zero duration is refused with `error number -7`, the same code `set_favourite` gives.

Nine variants were tried and all nine failed: shuffled and ordered, `MY-C0002`, `MY-C0004` and `MY-C0008`, the library's default category, one minute and five minutes, and the raw request with `sub_category_id` supplied rather than omitted. The one that succeeded was `off`. So it isn't the category, the shuffle flag, the interval, or the library's argument handling -- the TV refuses to start a slideshow over this API at all.

That matters because the TV was separately observed running a 5-minute Art Store rotation of its own, so the capability plainly exists and is simply not reachable this way. It also means `frame slideshow N` as scoped in `../CLAUDE.md` cannot be built: `frame slideshow 0` works and nothing else does. Whether the remote channel can drive the Art Mode UI to start one is untested and is the only remaining avenue.

The other endpoint doesn't work either, so the stronger claim is earned: **this firmware has no reachable way to start a slideshow.** `set_auto_rotation_status` never answers at all, hanging until the watchdog killed the run, which is the same thing its getter does. So the auto-rotation pair is absent rather than merely unpreferred, and `set_slideshow_status` is present but write-limited to `off`.

Unlike `set_favourite`, a `-7` here is clean. The channel survived all nine refusals and kept answering, so `set_slideshow_status` can be probed freely. `set_auto_rotation_status` is the opposite and wedges the channel, so a run that calls it gets one attempt.

**Finding (2026-09-01, read side only):** `get_slideshow_status` is answered and `get_auto_rotation_status` is not, so the slideshow family is the one this firmware speaks and the wrapper should never call the auto-rotation pair. The unanswered request didn't error; it hung until the watchdog killed the run, which is what an unsupported request looks like on this API.

What `get_slideshow_status` returned, with the TV rotating the free Art Store stream:

```
value               "5"
type                "slideshow"
category_id         "ARTSTREAM"
sub_category_id     ""
current_content_id  "SAM-S10004103"
content_list        30 items as a JSON string, each with content_id, category_id, matte_id,
                    portrait_matte_id, width, height
```

`value` is presumably the interval in minutes, and `type: slideshow` is presumably the ordered mode with `shuffleslideshow` being the other, but neither has been confirmed by setting them. `content_list` is JSON inside a string, not a nested object, so it needs a second `json.loads`. The set side of this spike, and whether the TV visibly rotates on the interval given, is still to do.


### T6. What mattes does this firmware offer?

`get_matte_list()` returns types and colors separately. Record both lists verbatim, since the config's default matte has to be a real value and there's no way to validate it without this.

While you're here, settle what `portrait_matte_id` is for. `upload()` and `change_matte()` both store two matte ids per item, `matte_id` and `portrait_matte_id`, and the library passes them through without comment. The hypothesis worth testing is that the pair is keyed to the *panel's* orientation rather than the image's, for the models that take Samsung's auto-rotating wall mount, which would mean a fixed-landscape 32" never reads the portrait one at all. Upload one portrait image with two visibly different matte ids and see which one the TV draws.

That matters because if `portrait_matte_id` is inert here, the only way to frame a portrait photo well is to composite the mat into the image and upload with `matte=none`.

**Finding (2026-09-01, lists only):** Ten types and sixteen colors. A matte id is `<type>_<color>`, so `shadowbox_antique` is valid and so is `none` on its own.

```
types   none modernthin modern modernwide flexible shadowbox panoramic triptych mix squares
colors  black neutral antique warm polar sand seafoam sage burgandy navy apricot byzantine
        lavender redorange skyblue turquoise
```

`burgandy` is the TV's spelling, and it has to be sent that way. Each color also comes with an `R`, `G`, `B` triple: `polar` is `232,230,231`, `antique` is `224,219,210`, `black` is `34,34,33`. Those describe the mat the TV intends to draw, which is not the same as what the panel shows. `black` has since measured near its triple in daylight, so the numbers are not simply wrong; the light end is what T13 still has to check.

The TV also offers photo filters, `None Aqua ArtDeco Ink Wash Pastel Feuve`, via `get_photo_filter_list()`. Not in scope, noted so nobody has to ask.

Which of the ten types the TV will actually accept per orientation is not in this list, and `get_matte_list()` never says; T15 has the two sets. `portrait_matte_id` turned out to be inert on this panel, which T9 settled.


### T7. What category ids does the TV actually report?

`available()` puts a `category_id` on each item, and the documented values are `MY-C0002` for My Pictures, `MY-C0004` for Favourites, and `MY-C0008` for the Store. Confirm those are what this TV reports, then check whether `set_favourite()` moves an item into `MY-C0004` as seen by `available()`.

That's what decides whether favourites plus a category-scoped slideshow is a usable substitute for albums.

**Finding (2026-09-02): `set_favourite` does not work on this firmware, so favourites cannot be the playlist.** The library's `set_favourite` sends a `change_favorite` request. Its first ever call returned `error number -7` promptly; every call since has hung instead, taking the art channel down with it, so the failure isn't even consistent. Nothing was ever favourited, and `MY-C0004` has still never been observed. Treat favourites as unavailable until something proves otherwise, which removes the substitute for albums that `../CLAUDE.md` was counting on and leaves a slideshow over `MY-C0002` as the only scoping available.

`MY-C0002` is confirmed: every upload lands there on its own, reported with `content_type: mobile`.

**Finding (2026-09-01, partial):** The only category seen so far is `MY-C0008` on the one Store item, which matches the documented value. `MY-C0002` and `MY-C0004` can't be confirmed until something is uploaded and favourited, so that half waits on T4. One extra value did turn up: the running slideshow and `get_current()` both report `category_id: ARTSTREAM` for the same item that `available()` files under `MY-C0008`, so `ARTSTREAM` is a fourth id, apparently meaning the free Art Store rotation rather than a stored category.


### T9. Can the TV be told to frame a portrait photo itself?

Needs T4 first. Everything about how the pipeline handles portrait photos hangs on this, and it is the difference between the TV drawing a mat that can be restyled later and a mat baked into a JPEG forever.

Three things to find out, in order:

1. Does `art.upload()` accept an image that isn't `1920x1080`? Feed it the 810x1080 that `=w1920-h1080` returns for a 3:4 photo, and record what the *panel* shows: the photo at native size with the TV's own matte around it, an upscale, a stretch, or a rejection. Upload succeeding says nothing about how it's displayed, so this has to be answered by looking at the wall.
2. Does `portrait_matte_id` turn out to be about the image after all? T6 left this open on the hypothesis that it tracks the panel's orientation on an auto-rotating mount. Upload one portrait image twice, once with a visible `matte_id` and `portrait_matte` set to `none`, once the other way round, and see which the panel draws.
3. If the TV does frame it, is the mat width fixed or proportional? A mat sized for a 16:9 image may look wrong around a 3:4 one.

**Finding (2026-09-02, superseded in part): with no matte the TV center-crops to fill 16:9. With a matte it frames properly, portraits included, so the pipeline does not have to burn a mat in. Read the T12 finding below before acting on any of this.** Answered off photographs of the panel rather than from the API, since `get_thumbnail` hangs on this firmware and `available()` only reports what was stored.

The yellow corner ticks are what make this conclusive. They sit 5% in from each corner, and center-cropping 4:3 to 16:9 removes 378px from top and bottom, taking every tick with it. On the 1920x1080 control the ticks are plainly visible along all four edges. On the 4032x3024, stored as 1920x1440, there is not one yellow pixel anywhere on the panel, no black bars, and no horizontal distortion. That rules out letterbox and rules out stretch and leaves only a crop.

The 810x1080 portrait got the same treatment: displayed cropped, with most of the label gone. Center-cropping 810x1080 to 16:9 keeps a 810x456 band, 42% of the frame height, which matches what was on the wall. **No mat was drawn around it.**

So `portrait_matte_id` is inert on this panel, which is what T6's panel-orientation hypothesis predicted and this now confirms for a fixed-landscape 32". Question 3 doesn't arise, since there is no mat to measure.

The consequence looked like uploading a portrait at native size doesn't work and a mat has to be composited locally. T12 overturned that: every image above was uploaded with a matte the TV couldn't render, and the crop is what an unmatted image gets. Given a matte type it accepts, the TV frames a portrait whole.

**Resolved by T10: a non-`none` `matte_id` is safe on a landscape and crashes on a portrait.** The two portraits were identical but for which matte field held `modernwide_polar`. The one with `portrait_matte_id` set displayed, cropped, both times it was tried. The one with `matte_id` set crashed the TV both times, in two different orders, putting a Samsung dialog on the panel reading "An unexpected problem has occurred. Please turn off and on and then try again. (40000)" and killing the art channel from that point on. Recovery took a power cycle.

T10 ran that test. A 1920x1080 landscape uploaded with `matte_id=modernwide_polar` displayed correctly, held the panel, and the art channel stayed up. So the crash is specific to a matte on a portrait rather than to mattes at all, and `art.upload()`'s `shadowbox_polar` default is dangerous only on portrait input. Passing `matte="none"` explicitly is still right, but because the default looks wrong on a 32" panel rather than because it will take the TV down on every upload.

**That inverts what T9 appeared to say about portraits.** The TV demonstrably can draw a mat -- it did it on the landscape -- so "the TV cannot frame a portrait" is no longer the natural reading of T9. The likelier reading is that matting a portrait is broken on this firmware, which is a bug rather than a missing capability, and a bug might have a way around it. That is what makes T12 worth doing before any mat gets composited permanently into a JPEG.

Two things the photograph shows that the API never would. The mat is drawn *inside* the panel and the image is scaled down to fit its aperture, so a mat costs real display area on a 32" screen and the aperture is wider than 16:9 -- `modernwide` cropped the image top and bottom to fit. And the mat rendered as light blue rather than the near-white `polar` is supposed to be. That is unexplained. Candidates are the camera's white balance, Art Mode's own color-temperature setting, which `get_color_temperature` exposes, and the TV tinting the mat toward the image. It matters for T13, since the whole point of choosing a mat color is that it looks right on the wall, and it should be checked against a neutral test image before anyone trusts a color name.


### T12. Can the TV mat a portrait, if the matte is applied from its own UI?

The API crashed setting `matte_id` on a portrait, which made it look like the renderer couldn't mat a tall image. Applying one by hand separates that from the API sending a value the renderer chokes on.

**Finding (2026-09-02): yes, and it frames the portrait exactly right, so nothing needs compositing into the JPEG.** A portrait uploaded with `matte=none` and then matted through the TV's own Art Mode UI displays whole, with mat around it and nothing cropped. That is the layout a portrait on a landscape panel wants, and it is what the earlier findings said was impossible. T16 measured where the mat actually sits, which is on all four sides rather than only the left and right as this originally recorded.

`available()` reports the matte the UI chose as `flexible_black` on the portrait and `modern_black` on the landscape. **`flexible` is in `get_matte_list()`, so this is a value the API can name.** The type that crashed was `modernwide`, so the crash reads as `modernwide` having no portrait form rather than portraits refusing mats. Whether the API can set that value on an image already uploaded is T14, and the answer turned out to be no for any image at all.

The TV's mat picker offers **two** types for a portrait and **six** for a landscape, which is independent evidence that the TV knows some types don't apply to a tall image. `get_matte_list()` returns all ten regardless of the image, so the API gives no hint which are safe. Until the safe set is known by name, treat any type outside `flexible` as unproven on a portrait.

Two loose ends, one since closed. The areas beside the portrait photographed as medium blue while the recorded color is `black`, defined as RGB 34,34,33, which raised the possibility that `flexible` renders something other than a flat color or that what is visible beside the image is not the mat. T15 and T16 answered that: `flexible` draws a flat mat plus a white bevel about 6 panel pixels wide, and the same mat measures near its declared triple in daylight, so the blue was the dark room. What remains is the light end, which is T13. And `get_current()` reported `category_id: MY-C0009`, a fifth category value, alongside the known `MY-C0002`, `MY-C0004`, `MY-C0008` and `ARTSTREAM`.


### T14. Can `change_matte()` set a matte from the API?

T12 matted a portrait from the TV's own UI and it rendered correctly, which left one question between that and a working `frame matte`: whether the API can set the same value. `.claude/tmp/t14_run.py` sets `flexible_polar` on the portrait, reads `available()` back, and selects the image.

**Finding (2026-09-03): `change_matte()` does not work on this firmware, on any image.** It refuses with `error -7` and `available()` reports the same `matte_id` afterward, so nothing happens at all. Four calls, all refused:

* `flexible_polar` on the 810x1080 portrait, the value T14 set out to test.
* `modern_polar` on the 1920x1080 landscape -- a real change, of a type the TV itself had already applied to that image.
* `modern_black` on the same landscape, which is the value it already carried, so a request that asks for no change.
* `flexible_black` on the portrait, which is the value the TV's own UI chose for it.

The portrait was never the problem. **T10 matted a landscape through `upload(matte=)` rather than through `change_matte()`, so the call had never been observed working, and reading the portrait's -7 as a portrait limitation would have been the wrong conclusion.** `-7` is also what `set_favourite` returned and what every non-zero slideshow duration returns, so it looks like this firmware's generic refusal rather than anything specific.

**So `upload(matte=)` is the only place a matte can be set.** That reshapes `frame matte <matte_id>`: it can't retrofit a matte onto an image already on the TV, and restyling one means re-uploading it, which mints a new `content_id` and rewrites the inventory entry. `docs/TODO.md` T16 is whether a portrait can be matted that way at all, and until it's answered the only portrait mat anyone has seen came from the TV's UI.

Two things worth not rediscovering:

* **The first attempt hung, and the token was the whole cause.** The art channel completed its TLS handshake and then sat silent until the socket timed out. Two things were different from the last working run -- the connection sent the token file, and the controlling machine had changed address since T2 recorded the working pairing -- so the address change looked like a candidate. It isn't one. `.claude/tmp/t14_auth.py` runs both probes back to back: tokenless answered `get_artmode()` in 1.1 seconds and the token connection failed. Ordering isn't doing the work either, because a *first* connection of a session had already hung with the token and succeeded without it.

  An Allow prompt did appear during that run and was accepted, and which probe raised it is what rules the address out for good. Both probes connect under the same client name from the same address, so their Device List identity is identical, and the tokenless one answered 1.1 seconds in -- far too fast for a prompt to have been read and accepted. So the entry for that name and address already existed and was already valid, and the prompt belongs to the token probe. **An unrecognized token doesn't just get ignored: the TV treats the client as unknown and re-prompts.** That is the one difference between two otherwise identical connections, which leaves nothing for the address to explain.

  `.claude/tmp/t4_common.py` passes `config.tv.token_file` on the art channel, which is what resurfaced this. It contradicts T2 and can only ever have worked for as long as the token stayed valid.

  The failure has two shapes, and only one of them looks like an auth problem. The first was silence until the socket timed out. The second was a fast `ConnectionFailure` whose payload was an unrelated event -- `{"event": "art_mode_changed", "status": "off"}`, from somebody using the remote to leave art mode while the probe ran. **So a `ConnectionFailure` carrying a `d2d_service_message` says only that the first frame wasn't `ms.channel.connect`, and any event the TV happened to be broadcasting can occupy that slot.** Read the payload before treating one as a refusal.
* `available()` reports `portrait_matte_id: flexible_polar` on the Art Store's own `SAM-` stream image, which is the only place that field has ever come back as anything but `none`.


### T16. Does `upload(matte=)` mat a portrait?

T12 matted a portrait correctly but did it from the TV's own UI, and T14 then ruled `change_matte()` out entirely, which left `upload()` as the only call that could set one. Nobody had passed a `flexible` matte to it on a portrait: the only portrait matte ever uploaded was `modernwide_polar`, which crashed Art Mode. `.claude/tmp/t16_upload.py` uploads one 810x1080 test portrait with `matte="flexible_black"`, reads `available()` back, selects it, and holds for 30 seconds polling `get_current()`.

**Finding (2026-09-03): yes, and this is the call `frame matte` has to use.** No crash. The TV stored it at 810x1080 with `matte_id: flexible_black`, exactly as asked, kept the selection for the full hold, and framed the image whole. So a portrait can be matted from the API after all, and `flexible` is a type that has now been proven on one through `upload()` rather than only through the UI.

The test image is bright orange with a 1px white border and yellow ticks reaching 5% in from each corner, because `flexible_black` is RGB 34,34,33 and a mat that dark is invisible against the dark test portraits. Measured off the panel photograph, the displayed image is 0.739 wide over tall against the 810/1080 = 0.750 it was uploaded at, and all four corner ticks are present. **Nothing is cropped and nothing is stretched**, and the 1.5% is the camera being off-axis.

**This corrects T12 on where the mat sits.** It is on all four sides, not only the left and right. The image occupies about 86% of the panel's height, so roughly 7% of it is mat above and below, and T12's read of "full panel height with effectively none top or bottom" was too generous. Call the measurement approximate, because it comes off a hand-held photograph rather than anything the API reports.

That also says what `flexible` is for. Its aperture takes the image's own aspect ratio rather than imposing a fixed one, which is why it neither crops nor letterboxes, why the TV picks it for a portrait, and why `modernwide` -- an aperture wider than 16:9 -- has no portrait form and cropped a landscape top and bottom. **A matte type that flexes is a different kind of thing from one with a fixed aperture, and only the flexing kind is safe to apply without knowing the image's shape.**

**And it goes most of the way on T13.** Sampling the photograph across the mat, `black` measures about RGB 30,27,32 against the 34,34,33 `get_matte_list()` declares, so in a daylit room it is black and the declared triple can be trusted. T12 photographed the same matte as medium blue, and that photograph was taken in a dark room, which makes the camera's white balance the likely explanation rather than anything the panel was doing -- likely, not established, since nobody has re-shot the dark-room condition. `.claude/tmp/t4/t16_flexible_black_panel.jpg` is the photograph, with `e_panel.jpg` and `e_corner.jpg` cropped out of it.

**There is also a light line around the aperture, wider than the test image's own border.** Sampled across the edge at mid-image, the mat sits at luminance 25 to 37, jumps to 174, plateaus at 214 to 215, then falls to the orange at 111. That band is 4 pixels wide in the photograph, and since the 810px-wide image spans 450 pixels there, one source pixel is 0.56 of a photograph pixel -- so the band is around 7 source pixels, where the test image's own border is 1px. That couldn't be settled from this image, because every image `t4_images.py` builds carries that border. T15 settled it with one that doesn't: **`flexible` draws a white bevel of its own, about 6 panel pixels wide.**

`MY_F0011` is the uploaded test image and needs deleting.


### T15. Which matte types are safe on a portrait?

T12 saw the TV's picker offer two types for a portrait and six for a landscape without recording which, and `get_matte_list()` returns all ten whatever the image, so the wrapper had no way to refuse a type that would crash. Probing the eight untested types through `upload()` would have been eight chances at error 40000, each needing a power cycle.

**The picker shows thumbnails and no names, so the answer can't be read off the screen. The TV will say them, though: `available()` reports each image's `matte_id` once one is applied,** which is how T12 learned the name `flexible_black` in the first place. `.claude/tmp/t15_upload.py` puts one image of each orientation up, and `.claude/tmp/t15_watch.py` then holds a single connection and polls while somebody steps the picker through its options, printing each name as it appears. Nothing is ever sent that the TV didn't offer, so there is no crash to risk.

**Finding (2026-09-03):**

* **Portrait, two types: `flexible` and `shadowbox`.**
* **Landscape, six: `none`, `modernthin`, `modern`, `modernwide`, `flexible`, `shadowbox`.**
* **Offered for neither orientation, so unusable: `panoramic`, `triptych`, `mix`, `squares`** -- four of the ten `get_matte_list()` returns.

So the crash was never about portraits refusing mats, and it was never about `polar` either. `modernwide` is a landscape-only type, and the API let it through on a portrait anyway. **The rule the wrapper needs is that the API is more permissive than the TV's own picker, so it has to hold the offered sets itself and refuse by orientation.**

That also **corrects what was recorded about `art.upload()`'s `shadowbox_polar` default: it is not a crash waiting to happen on a portrait, because `shadowbox` is one of the two the picker offers.** Passing `matte` explicitly is still right, but because the default is a framing choice nobody made rather than because it is dangerous.

One asymmetry: `none` is offered for a landscape and not for a portrait, so the TV's own UI won't let a portrait be un-matted. T9 did upload a portrait with `matte="none"` and got a center-crop rather than a crash, so that pair has been observed working once, but it predates knowing the picker refuses it and was never a controlled test of the crash. It is one data point rather than grounds for treating `none` as always accepted.

The two test images are left on the TV for T13's color work, and both ended the run carrying `shadowbox_black`, since that is where the picker was last stepped to. Nothing in `.claude/tmp` set that, so don't read it as an upload default.

**And it closes T16's loose end.** These images have bare outer edges rather than the 1px border every image in `t4_images.py` carries, and a white line still appears between the mat and the image: the mat photographs at luminance about 47, then a saturated 255 plateau across 3 to 4 photograph pixels, then the image. **`flexible` draws a white bevel of its own, roughly 6 panel pixels wide,** the way a cut mat does. On a 32" panel that is real picture, and it is worth knowing before choosing a type.

Two things about how much the aperture costs, both measured off the same pair of photographs. On a 16:9 landscape `flexible` leaves only a hairline of mat, since its aperture matches the panel. On a 3:4 portrait it takes about 7% of panel height on each side. Neither orientation is cropped: the 4% inset rectangle in each test image survives on all four sides.


### T13. Does a mat color name predict what the panel shows? (open)

**Nobody has run this. Everything below is what the earlier spikes left behind, written so it can be picked up cold.**

`frame mattes` can only offer colors by name, and `get_matte_list()` gives a name plus an `R`, `G`, `B` triple for each of sixteen. A triple describes the mat the TV *intends* to draw, which is not the same as what the panel shows through its anti-glare film in a lit room. The color that matters most is the near-white end, because that is the obvious default for a light mat and the one a wrong guess would make look cheap.

**What is already known:**

* `black`, declared `34,34,33`, measured about `30,27,32` off a daylit photograph in T16. So in daylight the triple can be trusted, at least at the dark end.
* T12 photographed that same `black` mat as **medium blue**, and a `polar` mat as **light blue** rather than near-white, both in a dark room. The camera's white balance is the likely explanation, but nobody has re-shot the dark-room condition to confirm it, so it is not established.
* `polar` is `232,230,231`, `antique` is `224,219,210`. `burgandy` is the TV's own spelling and has to be sent that way.

**What is left:** whether `polar` reads as near-white on the wall in a lit room, and whether Art Mode's own color-temperature setting shifts the mat. `get_color_temperature()` exposes that setting and it has never been varied, so it is an untested candidate alongside the camera.

**How to run it.** `MY_F0012` (landscape, 1920x1080) and `MY_F0013` (portrait, 810x1080) are already on the TV for this, both bright and flat so a mat reads clearly against them. Apply each color from the picker's "Mat color" row and photograph the panel in daylight. `.claude/tmp/t15_watch.py` holds one connection and prints each `matte_id` as it is applied, so a photograph can be matched to a color name without guessing -- that is the same trick that answered T15, and it is much cheaper than one connection per color.

**One methodological trap, learned the hard way.** Absolute numbers off a photograph are exposure-dependent: two photographs of the identical `black` mat measured luminance about 30 and about 47. So don't compare a sampled value against a declared triple across different photographs. Either get a known reference into the frame, or apply two colors and compare them within a single photograph.


### T8. Does the SmartThings app offer a Google Photos cloud media app for this TV?

Thirty seconds on your phone. Samsung documents a Cloud media app option that links Google Photos, but only on the 2026 Frame and Frame Pro pages, so whether it was backported to the 2023 LS03C is unknown. Check whether the option appears in the SmartThings Art tab.

This doesn't block anything, and it wouldn't replace this project even if it exists, because it wouldn't give you scheduling, mattes, or mirroring. But it's cheap to check and it would change how much the sync feature is worth.

**Finding (2026-09-02):** No. Nothing resembling a cloud media app or a Google Photos link appears in SmartThings for this TV, so the feature was not backported to the 2023 LS03C. Scraping the shared album stays the only route, and the sync feature is worth exactly as much as it looked.
