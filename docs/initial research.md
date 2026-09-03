# Samsung Frame 32" (QN32LS03CB) — Art Mode Reference

Research notes. Model is the 2023 LS03C generation, 32", no custom bezel.

---

## Panel specs that drive everything

| | |
|---|---|
| Resolution | **1920 × 1080** — the 32" is FHD, *not* 4K, despite some retail listings labeling it "QLED 4K" |
| Sizes that are 4K | 43", 50", 55", 65", 75", 85" (3840 × 2160) |
| SoC | dual-core Cortex-A53, 1.5 GB RAM, Tizen 6.5 |
| One Connect box | Included on the 32": 2× HDMI, 2× USB 2.0, RF, Ex-Link, optical out |
| Art storage | ~6 GB in My Collection |

At 1080p JPEG (~500 KB–1 MB each), 6 GB is effectively unlimited — a thousand images is fine.

### Image prep

- JPEG or PNG, sRGB, exactly 16:9. The TV can't crop or edit — do it beforehand.
- Pull highlights down ~10%; the matte film blooms bright whites.
- **Composition matters more at 32" than size.** Portraits, bold color fields, and single-subject
  works read well. Sprawling detailed scenes (Bruegel-type) turn to mush. Bias museum API
  filtering toward simple compositions.

```bash
mogrify -path out/ -resize 1920x1080^ -gravity center \
  -extent 1920x1080 -colorspace sRGB -quality 92 *.jpg
```

---

## Uploading art — the manual paths

Personal photo upload is **free and built in**; the Art Store subscription (~$60/yr) is only for
Samsung's licensed collection and is entirely skippable.

Samsung's own docs (not third-party writeups):
- SmartThings flow: https://www.samsung.com/us/support/answer/ANS00076727/
- USB flow: https://www.samsung.com/us/support/answer/ANS10005239/

**SmartThings:** add TV as device → Art Mode → `Add Your Photos +` → select → Save on The Frame.

**USB:** drive into the One Connect box → in Art Mode press Select → My Collection → storage
device → browse → Save → Save Selected. Don't pull the drive mid-save.

---

## Sensors and Art Mode behavior

Two sensors, **active only in Art Mode**. Motion defaults **off**, brightness defaults **on**.
Motion sensor is in the bottom-right corner of the panel.

### 32"-specific limitation

> On the 32" model of The Frame, only auto-off is supported if Night Mode is enabled.

No motion-triggered wake on this size. Which also means the display can't startle you by jumping
on when you walk in — that concern doesn't apply here.

### Settings (Art Mode Options)

- **Sleep After** — off by default; turns TV off after N minutes without motion.
- **Motion Detector Sensitivity** — feeds Sleep After.
- **Night Mode** — ambient-light based, not a clock. Below **1.8 nits** → standby.
  Back to Art Mode when light rises to **8 nits** *and* motion is detected.

### Use manual brightness

Three reasons:

1. **Auto hunts.** Passing clouds, a lamp switching on — the whole image shifts at once, very
   visible in peripheral vision on a 32". This is *the* common complaint.
2. **Auto targets "visible," not "believable."** Real art reflects room light and dims with it;
   a screen matching room brightness proportionally still reads as a screen. Most people who tune
   this land noticeably darker than auto picks.
3. **Makes power draw predictable.**

Tradeoff: one setting can't serve sunlit afternoon *and* dark evening. Tune for whenever you're
actually in the room (usually evening) and accept midday looking a bit dim.

Setup notes: test at night and during the day; re-check after changing wall color or lighting;
**avoid strong light sources directly above or below the Frame** — confuses the light sensor.
Worth settling before the room's lighting is finalized.

---

## Scheduling and power

**No native clock-based scheduling.** Night Mode is a light proxy, so the Frame stays lit as long
as a lamp is on. Real time control requires the API (below) or SmartThings routines.

### Power math

Only 32"-specific measurement found: **22–26 W** in Art Mode on a Kill A Watt (~3× a dedicated
digital picture frame; ~140 kWh/yr at 16 hr/day). **Single unverified reading — measure your own.**
Most wattage figures online are for 55–65" panels and don't apply.

At 24 W:

| Schedule | kWh/yr | @ 9¢ | @ 13¢ | @ 15¢ |
|---|---|---|---|---|
| 24/7 | 210 | $19 | $27 | $32 |
| 6am–10pm | 140 | $13 | $18 | $21 |
| **Delta** | **70** | **$6** | **$9** | **$11** |

Local rates: Seattle City Light standard flat ~13¢/kWh; Tacoma Power standard Residential Service
~8.7¢/kWh effective; PSE ~14.6¢ (gas-heavier generation mix).

Two things shrink the real delta:
- Auto-brightness means overnight hours already draw least — the hours you'd cut are the cheapest.
  Manual brightness flattens this and the table holds.
- Standby ≈ 0.5 W → ~1.5 kWh/yr. Ignorable.

**Verdict: scheduling saves ~one coffee per year.** Do it because a lit screen at 2am is annoying,
not because it pencils out. Burn-in isn't a reason either — Samsung's position is Frame panels are
built for prolonged art display. The actual money is skipping the Art Store subscription.

---

## Path A: script the Art Mode API (recommended)

`samsungtvws` — wraps the WebSocket + REST APIs, sync and async, full Art Mode support, has a CLI.
Drives **real Art Mode**, so ambient dimming and matte rendering still apply.

### Architecture: TV is the server

The Frame listens on 8001/8002 whenever on or in standby. Nothing to configure on the TV pointing
at your machine — no URL, no app. Your script dials out; the TV answers. No cloud, no Samsung
account, no port forwarding. Nothing leaves the LAN.

Consequence: **the scheduler must live on the LAN.** A Cloudflare Worker can't reach the TV — the
bus-sign pattern doesn't transfer, since TRMNL polls outward. If you want Worker-side logic, split
it: Worker fetches/resizes/picks and exposes today's image at a stable URL; a tiny local job pulls
that and pushes to the TV.

### TV-side setup (no Developer Mode needed — that's Path B only)

1. **DHCP reservation.** MAC is under Settings → General → Network → Network Status → IP Settings.
   Token binds to the client, not the address, but every script hardcodes the host.

2. **Settings → General → External Device Manager → Device Connect Manager**
   - **Access Notification** → `First Time` (or On). This is what makes the pairing prompt appear.
     Set to Off, the connection fails *silently*.
   - **IP Remote** → on, if exposed separately on your firmware.
   - To re-pair: clear the entry in **Device List** in the same menu, or the TV remembers the
     denial and won't re-prompt.

3. **Settings → General → Network → Expert Settings → Power On with Mobile** (a.k.a. "Power On
   with Wi-Fi"). Library sends a magic packet but the TV must be willing to hear it.
   - Art Mode off = **standby**, which still answers on 8002. Fully off (long-press power) does
     not. Prefer `set_artmode(False)` over powering off; WoL is insurance.

4. **Port 8002 = TLS + token** (8001 is unencrypted/tokenless). Art mode requires 8002 on anything
   recent. First connect pops an on-screen "Allow this device?" — accept within ~30 s.

### Connectivity check before touching code

```bash
curl -s http://192.168.1.50:8001/api/v2/ | jq
```

Unauthenticated info endpoint — returns model, device ID, `TokenAuthSupport`. If this works,
network is fine and later failures are auth or library-level.

### Pairing

```python
import os
from samsungtvws import SamsungTVWS

tv = SamsungTVWS(
    host="192.168.1.50",
    port=8002,
    token_file=os.path.expanduser("~/.config/frame/token"),  # persists across runs
    name="frame-art",   # shows in the TV's Device List
)

art = tv.art()
print(art.supported())    # sanity check
print(art.get_artmode())  # 'on' / 'off'
print(art.get_current())  # current content_id
```

Run once interactively to hit Allow. After that the token file makes it cron-safe.
**Skipping `token_file` = re-prompted every run** — most common mistake.
`name` is the identity the token is issued against — keep it constant or you re-pair.

### Operations

```python
art.upload(open("art.jpg","rb").read(), file_type="JPEG", matte="none")
art.select_image(content_id, show=True)
art.set_brightness(4)
art.set_artmode(True)
art.set_slideshow_status(15)   # 15-min rotation, shuffle default; also kicks TV into art mode
art.available()               # inventory of content_ids on the TV
```

`matte="none"` if you've composited the mat into the image yourself — otherwise the TV's mat eats
into an already-small display area.

### Nothing has to be always running

The TV holds state after your script disconnects: uploaded images, selection, per-image mat and
color, slideshow on/off and interval, brightness, color tone.

| Want | Needs a machine awake |
|---|---|
| Upload / curate / set mats | No — one-off |
| Rotate through collection | No — TV does it |
| Clock-based on/off | Yes, 2×/day |
| Day/evening brightness swap | Yes, 2×/day |
| Daily fresh museum art | Yes, 1×/day |

**Start as a one-off CLI**, invoked by hand:

```bash
frame upload ~/art/batch-3/ --matte shadowbox_black
frame slideshow 30
frame brightness 4
```

If the day/evening brightness swap turns out to be worth it, add two launchd entries calling the
same script — no rearchitecting. Dedicated hardware (Pi) only earns its keep for the daily-fetch
version, and even then a missed day is invisible.

**Do on first run:** dump `art.available()` to local JSON. That's your inventory — without it,
three months later you won't know what's already uploaded and you'll create duplicates.

### Gotchas

- **Firmware updates reset things** — Tizen updates have flipped Access Notification back and
  invalidated tokens. Make cron failures log loudly rather than silently no-op for weeks.
- **Upload storage accumulates.** Delete-as-you-go beats a cleanup script later.
- **The art channel is separate** — the library opens `com.samsung.art-app` as its own WebSocket,
  distinct from the remote channel. This is what goes zombie in long-running processes.
  Short-lived cron invocations sidestep it entirely → another argument for launchd over a daemon.
- 2022 Frames were reported to block the art API, but active forks work through 2024 models, so
  LS03C is fine.

---

## Path B: native Tizen app — don't

Doable (HTML/CSS/JS in a `.wgt`), but:

- Developer Mode: Apps → press `123` → enter `1-2-3-4-5` → enter dev machine IP. Then add the TV
  in Tizen Studio's Remote Device Manager.
- Tizen Studio 6.1 current as of early 2026. Certificate creation needs Samsung Certificate
  Extension 2.0.73+ after Sept 2025. Distribution needs a Samsung cert → Seller Office account,
  manually confirmed.

**Dealbreaker: a Tizen app runs in TV mode, not Art Mode.** You lose the motion sensor, ambient
adaptation, and low-power behavior — the whole reason the Frame looks like art. Easy Photo Player
accepts that tradeoff because it targets all Samsung TVs, not just Frames.

If you want a UI, build a small web app on the machine that drives the TV, talking to the Frame
over WebSocket, and serve it on the LAN. Same experience, no certs, no Seller Office queue.

---

## Google Photos / slideshow options

**Native slideshow: yes.** "Display All" rotates selected images, 1-minute default, interval
adjustable. Real Art Mode, so ambient adaptation applies.

**Google Photos — unverified for LS03C:**

1. **SmartThings "Cloud media app"** — Art category → `+` → `...` → Cloud media app → link Google
   Photos. But this is documented on Samsung's **2026 Frame / Frame Pro** page. Unknown whether
   backported to 2023 LS03C. Check whether the option appears in your SmartThings Art tab.
2. **Native Google Photos TV app** — announced Dec 2025, launching 2026 starting with Memories;
   Create and Personalized Results (topic slideshows: ocean, hiking, Paris) later in the year.
   Pitched at the "AI TV lineup," so probably recent models only.

**Third-party fallback:** Tizen apps like Easy Photo Player stream from Google Photos, work on
2017+ Frames. Caveats: free tier capped at 10 views/day, paid from €4.99/mo, photo selection must
be refreshed every 7 days due to Google API limits. Also a regular app → not Art Mode.

**Verdict:** for a curated fixed collection, native slideshow over local art wins — no
subscription, no API expiry, proper Art Mode. Google Photos only makes sense if you want the
display auto-updating as you shoot.

---

## Free art sources (replaces the Art Store)

Major museums publish CC0 collections with public APIs:

- **Art Institute of Chicago** — 50,000+ CC0 images, unified API, no key required. Cleanest of
  the bunch; they open-source their own code too.
- **Rijksmuseum** — ~360,000 images, more than a third of the collection.
- **The Met, Cleveland, Getty, NGA, Smithsonian, Paris Musées** — similar.

**IIIF is the underrated part:** server-side cropping and scaling by URL parameter, so you can
request a 1920×1080 region without downloading a 200 MP TIFF.

Existing projects:
- A free open-source Art Mode alternative pulling from Met, Rijksmuseum, AIC, Cleveland, and
  Wikimedia Commons — processes images with metadata labels, writes to USB, no API keys.
- **MCP server** for browsing/downloading open-licensed imagery from Wellcome, Met, LoC,
  Rijksmuseum, Smithsonian, Europeana. Relevant given Claude Code usage.
- AIC documents using the Getty art generator with any IIIF manifest URL to import artwork into
  Animal Crossing. (Unrelated but delightful.)

---

## Projects worth stealing from

- **`mcsdodo/samsung-frame-art-gallery`** — closest thing to self-hosted Easy Photo Player. SSDP
  auto-discovery, batch upload, live preview of how images will look, smart edge cropping, auto
  museum-style matting to 16:9, Met public-domain browser.
- **`ow/samsung-frame-art`** — minimal script, flings a folder at the TV. Original design: Pi
  picking a random image on cron. Forked to pull from the Rijksmuseum API.
- **`TheFab21/ha-samsungtv-smart`** — most actively maintained fork. Frame 2024 source list and
  picture mode detection fixed via REST fallbacks; self-healing art WebSocket with a zombie-channel
  circuit breaker for the "art mode randomly turns off" problem. Also does opt-in artwork
  identification via reverse image search + LLM confirmation, showing title, artist, date, bio.
- **`frame_art` HA component** — author says it's a proof of concept and will degrade HA
  performance because `art.py` isn't async. Use TheFab21's fork instead.

Home Assistant angle is where this gets useful: art on when presence detected, palette shifted by
time of day or weather, art swapped when a specific phone joins Wi-Fi.

---

## Physical

**Bezels.** Samsung's run $99–$200 with quality complaints on their own site. DIY: decorative wood
trim mitered at 45°, corner braces, stain, hook-and-loop Command strips onto the TV's bezel. Under
$50. **Measure the screen only, not the built-in bezel**, or you'll cover picture. Fancier variant:
~300 15 mm split wood balls glued along a plain bezel for a bobbin-frame look, ~2 hrs, under $30.

Note: a DIY bezel covers the bottom-right motion sensor. Doesn't matter on the 32" (no motion wake
anyway) — but notch the corner if you ever move this to a larger Frame.

**One Connect box.** 3D-printable wall mount for the 32" box on Printables, Fusion 360 source
included. Samsung moderators warn against mounting the box directly behind the panel — needs
airflow clearance.

**In-wall cable timing.** Running the One Connect cable in-wall to a recessed low-voltage box
below the TV is trivially easy while the walls are open and impossible after.