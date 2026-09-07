# Cropping and mattes

TL;DR: a crop decides how much of the photo reaches the panel and is permanent, a matte decides how what's left is framed and is a re-upload away from being something else, and `frame bakeoff` is how you choose the matte by looking at the wall rather than at a name.

A phone photo is 4:3 and the panel is 16:9, so there's no setting that both fills the screen and keeps the whole photo. You pick which to give up, and there are two places to pick it.


## Cropping

`[[pipeline.crop]]` crops before the upload. It's a list of rules, each one saying that a photo of about this shape becomes about that shape, and the first rule a photo matches wins. Leave it out and nothing is cropped, which is the default.

    [[pipeline.crop]]
    when = "4:3"
    to = "16:9"
    anchor = "center"

To try rules out, set `sync.short_run` to mirror just the newest few photos of each orientation, and run `frame sync --label` to draw the rule that fired onto each photo as it goes up. Then look at the panel, adjust, and run it again. Once the rules are right, run it once more without `--label`, because the label is part of the JPEG and nothing takes it off in place.

If one particular photo comes out wrong, `[pipeline.crop_overrides]` names it on its own:

    [pipeline.crop_overrides]
    "AF1QipEXAMPLEONE" = { to = "none" }

The key is the `/photo/` segment from that photo's URL in the shared album, or the id `--label` drew on it. `config.example.toml` has the rest.


## Mattes

The matte does the rest. Whatever reaches the TV gets framed inside the mat you configured, so `flexible` keeps the photo and gives up the screen area, and `none` lets the TV center-crop instead.

**A matte is keyed to the photo's aspect ratio, not to whether it's landscape or portrait**, which is why `[art.matte_by_ratio]` is a table rather than two keys. Three of the six types draw a mat whose aperture is a fixed 16:9, and the TV will only put a 16:9 image inside one. Hand it a 4:3 and it accepts the upload, then puts an error dialog on the panel that takes a power cycle to clear. So the type that looks right for a landscape can be a crash for the landscape next to it in the same album.

    [art]
    fallback_matte = "flexible_black"

    [art.matte_by_ratio]
    "16:9" = "modern_black"
    "4:3"  = "flexible_black"
    "3:4"  = "flexible_black"

The two settings are read in that order, so a photo your crop rules reshape gets the matte for the shape it ends up rather than the one the album holds. Crop a 4:3 to 16:9 and it's the `"16:9"` line that mats it.

A ratio is snapped to the nearest simple fraction before it's looked up, so a 4080x3072 and a 4032x3024 both land on `4:3` rather than being two shapes half a percent apart. Anything your table doesn't name takes `fallback_matte`, and the run tells you how many photos did that and at which ratio, so you know which key is worth adding. Run `frame sync --dry-run` to see what your own album comes out as, crops and mattes both.

There's no command for applying a matte, because the TV only accepts one at upload time. Change a value in `[art.matte_by_ratio]` and run `frame sync`: every photo whose settings no longer match is uploaded again under the new ones and its old copy taken down. The same goes for the `[pipeline]` settings and the crop rules. `inventory.json` records what each photo was rendered with, which is how a sync knows the difference.

The difference between a crop and a matte is what you can take back. A matte is a re-upload away from being something else; a crop is gone from the stored image for good.


## Choosing a matte with `frame bakeoff`

A matte can only be set as a photo is uploaded, so seeing all sixteen mat colors means uploading the same photo sixteen times, and the TV's picker shows thumbnails and no names. A bakeoff round handles both. It puts one photo per shape in your album on the TV, once per matte, holding everything else still, and draws the name of whatever varies and that photo's shape across the middle of each copy, so the wall tells you what you're looking at. It prints the roster too.

A round covers every shape because the shape decides which types the TV will draw, so a mat chosen on a 4:3 may not be available for a 16:9 in the same album. `--orientation` narrows a round when that isn't what you want, which is worth doing on the color rounds since a color doesn't turn on shape.

Three rounds, colors before types, because a color has to be judged with some type drawing it and `flexible` is the one that crops nothing.

A round ignores your crop rules and says so, since it holds everything but the mat still and a crop would change what's underneath. Crops get judged the other way round, with `frame sync --label` above.

```
frame bakeoff --compare=colors --orientation=landscape --dry-run
frame bakeoff --compare=colors --orientation=landscape
frame bakeoff --compare=colors --orientation=portrait
frame bakeoff --compare=types --color=polar
frame bakeoff --clear
```

Uploading never changes what the panel shows, so open the TV's own picker and step left and right through the copies. Then put the winners in `config.toml` yourself, one line per shape under `[art.matte_by_ratio]`.

**This is the other command that deletes, and it deletes more than `frame sync` ever does.** A round starts by emptying the TV, so that nothing sits between the variants in the picker, and that reaches every uploaded image whether or not the inventory claims it. Samsung's own art is never touched. It names what it's about to delete and asks first, unless you pass `--yes`, and `--dry-run` prints the same list without doing anything. Run each round one at a time: the next round's clear is what takes the last one down, `--clear` on its own ends the series, and a plain `frame sync` then restores the album.

Sixteen colors is about two minutes of uploading, so the second pass over a shortlist is worth narrowing. An optional `[bakeoff]` table does that. An empty list covers everything, the same as leaving the key out, so the key can stay in the file for you to edit when you want fewer.

```toml
[bakeoff]
colors = ["polar", "antique", "sand"]
types = ["flexible", "shadowbox"]
```

The names are checked against the six types and sixteen colors this tool knows about, when the config loads rather than when the round reaches the panel. That matters because the API accepts matte combinations the TV's own picker withholds, and at least one of them crashes Art Mode hard enough to need a power cycle. Run `frame mattes` to see the list, along with anything your own TV reports that this tool has no record of.
