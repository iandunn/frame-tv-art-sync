# Composites

TL;DR: several photos go up as one image under a mat painted into the JPEG, which is what makes a portrait look like art on a landscape panel, and the cost is that one TV `content_id` stops meaning one source photo.

The treatments were chosen on the wall over four rounds on 2026-09-07, and `TODO.md` records what was tried and rejected so nobody buys the same answers twice. This file is the design a session needs to build it. `inventory-and-sync.md` is the thing it changes most, and `../CLAUDE.md` has the architecture around both.


## What was chosen

* **Portraits go two across.** A 3:4 alone leaves most of the panel empty, and the TV's own `flexible` mat doesn't fix that because the mat is not what looks wrong -- the size is.
* **Landscapes go two across by default, with a 2x2 grid of four as the other option.** This one is a trade rather than a gain. A 4:3 under `matte = "none"` fills the whole panel today at the cost of a quarter of its height, and two across shows both photos whole at about 11.8 by 8.8 inches instead.
* **A 16:9 stays whole on the full panel with no mat.** It already matches the panel, so a composite would shrink it and buy nothing back.
* **The edge is `shadowbox` and the mat is `antique` with a fine paper tooth.** `shadowbox` here is painted, not the TV's matte type of the same name: a bright core line where a cut mat's paper would show, and the print's own edges darkened as if it sat behind the mat rather than on it.

The mat is `antique` because the TV's own mat is, so a composite and a TV-matted photo can hang in the same rotation without a seam.


## The spacing rule

On each axis every gap is equal, the outer margins and the gaps between prints alike, so an axis holding `n` prints is divided into `n + 1` equal spaces. The prints are then as large as they can be with no gap under a floor, and 98px is the floor the approved two-up sits at.

**Equal gaps on both axes at once is not reachable**, and no floor gets there. Two 3:4 prints filling the panel's height come to 1620px wide including three equal gaps, whatever height you pick, against a panel that is 1920 wide. So one axis is always looser than the other, and which one depends on the shape: a 4:3 grid fills the height first, so its columns end up 292px apart against 98px between its rows.

Halving the vertical floor to 49 is what `land-grid-tall` is. It grows each print from 7.5x5.7 inches to 8.9x6.7 and pulls the columns in to 228 as a side effect, since the prints get wider too. That's why the floor is per rule rather than a constant.


## Config

A rule table keyed on the shape coming in, the way `[[pipeline.crop]]` already is, and matched the same way: a ratio, or `landscape`, `portrait`, or `*`, within `crop.RATIO_TOLERANCE`. First match wins, and `load_config` refuses a rule that buries a later one, which `crop.CropRule.shadows()` already knows how to decide.

```toml
[[pipeline.composite]]
when   = "3:4"
count  = 2
layout = "row"

[[pipeline.composite]]
when   = "4:3"
count  = 2
layout = "row"

# The 2x2 option, which is this rule with a looser vertical floor rather than a layout of its own.
# [[pipeline.composite]]
# when     = "4:3"
# count    = 4
# layout   = "grid"
# gap_down = 49

[[pipeline.composite]]
when   = "16:9"
count  = 1
layout = "full"

[pipeline.composite_style]
mat        = "antique"
edge       = "shadowbox"
tooth      = true
gap_across = 98
gap_down   = 98
```

* `count = 1` with `layout = "row"` is a single photo under a mat, and `layout = "full"` is a single photo filling the panel with no mat at all. Both matter: `full` is what today's config does, so a shape with no composite rule behaves exactly as it does now.
* `layout` is `row`, `grid`, or `full`. A `grid` divides `count` into the squarest arrangement it can, so `count = 4` is 2x2.
* The style block is global rather than per rule, because a wall with two mat colors on it is not a wall anybody wants. `gap_across` and `gap_down` sit there as defaults and may be overridden on a rule, which is the only way the approved `land-grid-tall` is expressible: it is the 4:3 grid at `gap_down = 49` with everything else left at 98.
* Every value here goes into the render record, so changing any of them re-uploads every composite it reaches. That is the same rule `jpeg_quality` already lives under.


## Grouping

**Grouping is a pure function of the album and the config, and the inventory has no say in it.** Take the photos of one shape, sort them by `taken_at_ms` **oldest first** with `source_id` breaking a tie, and chunk them by the `count` that shape's rule asks for. A chunk that can't fill its count falls back to the next smaller layout for that shape, so four to a grid leaving two puts those two up as one row, and only a group that falls all the way to one hangs alone.

Chronological neighbours come from one afternoon, which is what makes a pair read as a pair rather than as two unrelated pictures.

**Oldest first is the whole of the stability story, and it is worth knowing why.** Chunking from the newest end would put the leftover single at the *oldest* end and re-pair everything below an insertion: five portraits pair as `(P1,P2) (P3,P4) P5`, and adding a newer photo turns them into `(N,P1) (P2,P3) (P4,P5)`, so one photo costs 25 new composites at album scale. From the oldest end the same five pair as `(P5,P4) (P3,P2) P1`, and adding `N` gives `(P5,P4) (P3,P2) (P1,N)`. One composite changed, the leftover is the newest photo rather than the oldest, and the photo you just added pairs with the one taken just before it.

Shapes never mix, and a shape is what a photo is **after** its crop rule, which `crop.resolved_size()` already works out for the diff. So a group's cells are uniform by construction. The members can still carry different crops, because `[pipeline.crop_overrides]` can reach the same shape by a different anchor, which is why the record keeps a crop per member rather than one for the entry.

**A photo appears in exactly one composite**, which is what keeps a removal from invalidating more than one.

**What this deliberately doesn't do is preserve existing groups.** A photo inserted mid-history, or one removed, shifts every group after it in that shape and so re-uploads them. Reading the groups off the inventory instead would keep the damage to the one group that changed, and it was in an earlier draft of this file. It came out because it buys less than it costs. The bookkeeping is real -- a test for whether a group is still intact, a rule for dissolving one that isn't, and a photo claimed by two groups at once -- and it makes the result worse rather than better: a stranded old photo gets paired with whatever arrives next whatever their dates, singles accumulate where the count is odd, and a `count` edit does nothing to the groups already on the wall because they all look intact. Ian's standing preference settles it, on 2026-09-07: regenerating is acceptable, optimizing to avoid it is fine only when it's easy, and neither is worth a worse layout.


## The inventory

`source_id` becomes `source_ids`, a list, always, even where it holds one. `FORMAT_VERSION` goes to 2, and a version 1 entry's `source_id` string reads as a one-element list, so an existing file needs no migration and no rewrite.

**A version 1 file's render records read as unknown, though**, because the record gained fields in the same version and an old one describes one photo cropped and matted on its own, which is not a shape a composite could be compared against. So the version is read at the file level and the whole file's records are dropped, which is the re-upload the next section says the first run costs anyway. A malformed record in a version 2 file is still an error rather than an unknown, since that one can be repaired.

```json
"MY_F0365": {
  "source": "google_album",
  "source_ids": ["AF1Qip...", "AF1Qip..."],
  "uploaded_at": "2026-09-07T04:11:02Z",
  "render": { "layout": "row:2", "edge": "shadowbox", "mat": "antique", "...": "..." }
}
```

**The group's identity is its ordered list of source ids**, and that is what the diff keys on in place of `source_id`. Two runs over an unchanged album produce the same list, so an unchanged group matches its entry and nothing moves. Order is part of the identity because it is part of the image: the same two photos left-to-right and right-to-left are different pixels. It is the order the chunking used, so the oldest photo hangs on the left and a grid fills left to right and then down.

One hazard is new, and it is the existing duplicate case widened rather than a different thing. Two entries can claim the same photo in different groups, which is what a run dying between uploading a re-composited group and dropping the old one leaves behind: a run that turns `(P, Q)` into `(P, Q, R, S)` and dies has both on the wall, so `P` is up twice.

**So an entry sharing any member with a group this run wants is deleted, and no flag gates it.** `SyncPlan` already states that rule for a duplicate and a superseded copy, on the grounds that a list existing for a reason other than mirroring is garbage by construction. Leaving it to `delete_removed_from_album` would put the stale entry in `left_in_place` with the mirror off, and `P` would then be on the wall twice with no later run healing it.

Two entries claiming the identical group are the narrower case and resolve as they do today: the entry whose record matches config stands, and with nothing to choose between them the newest upload stands. `sync._preferred()` already implements that and needs only its key changed.


## The render record

Four fields join it: `layout`, `edge`, `mat`, and the two gap floors. Every field is compared, so any of them costs a re-upload of everything it reaches, which is the rule the record has had since it existed.

`matte_id` stays and reads `none` for every composite, because the mat is in the pixels and a TV matte drawn around it would be a second mat. The one exception is `layout = "full"`, which carries whatever `[art.matte_by_ratio]` says for its shape, exactly as it does today.

The crop fields stay per photo, and that is the one place the record stops being flat. Every member of a group shares a post-crop shape, since that is what the group was formed on, but two of them can reach it by different routes: `[pipeline.crop_overrides]` can give one photo a `bottom` anchor where the rule gave its neighbour a `center` one. So the crop and the anchor are stored per member alongside its `source_id` rather than once for the entry, and a changed override shows up as a render mismatch on exactly the composite holding that photo.


## What a sync does differently

`plan_sync` keys on the group rather than the photo, and every list in `SyncPlan` comes to mean a group instead of an image. Past that the rules are the ones already written down in `inventory-and-sync.md`, and none of them change:

* A group with no entry is an upload.
* A group whose entry records a different rendering is an upload and a `superseded`, the replacement going up before the old copy comes down.
* An entry whose `content_id` is no longer on the TV is `orphaned`, and its group is uploaded again if the album still holds its photos.
* An entry whose group no longer exists is a delete, and `delete_removed_from_album` gates it exactly as it gates a photo today. That covers a group whose photo left the album and a group that regrouped around an insertion alike, since neither is a group this run wants.
* **The one thing no flag gates is an entry sharing a member with a group this run does want.** Its photo is going back up inside a different composite, so leaving the old one costs a photo on the wall twice, which is the duplicate rule rather than a mirroring decision.

`SyncReport` has to say this in terms a person can act on. A run that replaces 25 composites is touching 50 photos, and printing composites without photo counts would understate what it is about to do.


## Building the image

A new `composite.py` holds the layout arithmetic -- the spacing rule, the cell sizes, the fallback -- pure and tested with no image library in it, the way `crop.py` is. `pipeline.compose()` takes the prepared tiles and the layout and returns the JPEG.

**Every tile goes through `prepare()` on its own, and the mat is painted afterwards.** `prepare()` rolls highlights down about 10%, so a mat that went through it comes out visibly darker than the TV's own mat of the same color, and the composite would no longer sit beside a TV-matted photo without a seam. The assembled image is encoded directly rather than re-prepared.

The result is exactly 1920x1080 and goes up under `matte = "none"`, so the TV neither crops it nor draws anything around it.


## Consequences worth knowing before starting

* **The first run after this ships re-uploads the whole album**, because every entry on disk records a rendering that no longer exists. That is one long run of uploads, which is exactly the shape of the failure `TODO.md` T18 is about, so it is worth doing with `--debug` and reading the timing summary afterwards.
* **It reduces the upload count from then on.** Two across takes the 237-photo album to 120 images, measured 2026-09-07, and both of the Art app's crashes came under sustained load, so fewer and larger uploads is the direction that helps. How many photos and which shapes is a property of whichever album `config.toml` points at rather than of this project, so recount rather than reusing that number.
* **A photo added mid-history, or removed, re-uploads that shape's groups from there on.** Adding a photo taken in April to an album that already runs to June shifts every April-or-later group of that shape by one, and a removal does the same. Photos added at the newest end, which is the ordinary case, cost one composite. Nothing about this is worth optimizing away, but it is worth knowing that removing an old photo can mean a run of 25 uploads rather than one.
* **Losing `inventory.json` costs no more than it does today.** Grouping is derived from the album rather than remembered, so a rebuilt inventory pairs the photos exactly as the lost one did. What a loss costs is the attribution, which is the same hazard `inventory-and-sync.md` already describes.
* **`frame sync --label` draws on each print rather than on the composite.** The label names the rule that fired for one photo, and a composite holds several, so each tile is labelled before it is laid down and a `full` layout is the same one-label case it is today.
* **`frame bakeoff` ignores all of this**, the way it already ignores `[[pipeline.crop]]`, and says so when a composite rule is configured. A round exists to hold everything but the mat still.
