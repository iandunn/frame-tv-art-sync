# Museum art as a source

TL;DR: museums give away hundreds of thousands of public domain images, almost none of them are 16:9, and nothing that already exists mirrors them onto a Frame the way `frame sync` mirrors an album.

This is the survey behind the museum source in `TODO.md`, written before any of it was built. `../CLAUDE.md` has the architecture a source has to fit; this file is what the survey found. `photo-sources.md` is the same survey for photography, where the shapes are kinder and the National Park Service turns out to hold the most promising archive.


## What already exists

Two shapes, and the thing you'd want is neither of them. One shape reads a museum API and pushes to the TV, but has no memory between runs. The other mirrors a folder properly and knows nothing about museums.

| Project | Source | How it reaches the TV | Mat |
|---|---|---|---|
| [`n-pillai/frame-art-server`](https://github.com/n-pillai/frame-art-server) | Met, Art Institute, Cleveland, Wikimedia Commons, no API keys | `samsungtvws` over the LAN, or USB | Fills the screen with no mat, and ships a `tv_no_mat.py` that strips mats from everything |
| [`mcsdodo/samsung-frame-art-gallery`](https://github.com/mcsdodo/samsung-frame-art-gallery) | The Met's open collection | Web UI, one upload per button press | Burns museum-style matting into the JPEG |
| [`turley/frame-tv-artwork-sync`](https://github.com/turley/frame-tv-artwork-sync) | A local folder | Docker service, tracks its uploads, mirrors when `REMOVE_UNKNOWN_IMAGES` is set | Passes a real matte id, e.g., `shadowbox_polar` |
| [`janstrm/…-Frame-Art-Director-Integration`](https://github.com/janstrm/Home-Assistant-Samsung-Frame-Art-Director-Integration) | Local files or an HTTP URL | Home Assistant, with auto-rotation and tag filters | Matte style and color as HA entities |
| [`danmunz/docent`](https://github.com/danmunz/docent) | Whatever you drop in, plus Google Drive | Local web app over the LAN | Crops to 16:9, sets shadowbox, modern, panoramic |

`frame-art-server` is the closest to the museum half and it has no inventory at all. Its own README says the TV doesn't deduplicate, so a refresh means deleting the whole library and uploading the new batch. It is also a one-shot script by its author's description, with no daemon and no scheduler.

The two things none of them do are the two this project already does. Nothing records how a copy was rendered, so nothing can replace a photo because its matte changed. And nothing keeps a matte keyed to the image's own aspect ratio, which is what T22 found the firmware actually keys on.

There are also MCP servers over the same collections -- [archival-imagery-mcp](https://glama.ai/mcp/servers/chandhoke/archival-imagery-mcp) for Wellcome, Met, LoC, Smithsonian and Europeana, and [open-museum-mcp](https://github.com/cfpramod/open-museum-mcp) for license-verified search over Met, Cleveland and AIC. They're a curation front end rather than a sync path, though, so they don't compete with a source.


## Museum art is almost never 16:9

Filtering to works that already fit the panel is not a strategy. Measured against the Art Institute's API, which reports each record's source image dimensions. This is a census rather than a sample: 1,864 of the 1,962 public domain paintings AIC holds, the shortfall being records that report no dimensions.

- **1.0% of public domain paintings are within 2% of 16:9.** Nineteen out of 1,864.
- **55.2% are portrait**, so more than half the collection is out before any ratio question is asked.
- **The common painting ratios are 3:4, 4:5, 11:14, 11:15, 5:6, 4:3, 10:13 and 13:16**, each between 2.0% and 4.7%, snapped the way `mattes.ratio_of()` snaps them. Nothing clusters near 16:9, because 16:9 is a broadcast ratio and no painter was aiming at it.

So a museum source has to either crop or matte, the same choice the Google album forced, and the arithmetic is worse because the shapes are more varied.

### How much a crop costs

Cropping to fill the panel loses one dimension entirely. For a ratio `r`, the fraction that survives is `r / (16/9)` when the image is narrower than the panel and `(16/9) / r` when it's wider. Losing under 15% means `r` between **1.511 and 2.092**, which is about 3:2 through 2:1.

Against the same census, first as a share of everything and then as a share of the 29.5% that pass the `r >= 1.3` landscape filter `frame-art-server` uses:

| Crop loses under | Share of all paintings | Share of the wide-filtered ones |
|---|---|---|
| 5% | 2.7% | 9.3% |
| 10% | 6.7% | 22.8% |
| 15% | 12.0% | 40.6% |
| 20% | 16.8% | 57.2% |
| 25% | 23.4% | 79.6% |

**The 20% to 25% step is where 4:3 lives**, and 4:3 alone moves the number 25 points. A 4:3 passes a `>= 1.3` landscape filter and then loses exactly a quarter of its height, so that filter admits precisely the shape a tight loss budget rejects. Anything that filters on orientation is not filtering on croppability.

Across 3,529 public domain works of every type the shape is the same and slightly worse: 28.4% pass `>= 1.3`, and 35.6% of those clear the 15% budget. No type does much better than paintings. Prints are 10.1% of their own kind inside the budget, drawings 10.3%, paintings 10.2%, photographs only 3.2%.

### Why a low percentage is still plenty

12.0% reads as fatal and isn't. The Art Institute alone holds 1,962 public domain paintings and 62,054 public domain works of all kinds, so a 15% budget yields 223 paintings and roughly 6,300 works from one museum. Add the Met, Cleveland, the Rijksmuseum and Wikimedia and the pool is tens of thousands.

That's the difference from a phone album, and it changes which lever is worth pulling. The album `config.toml` points at holds 169 items, 118 of them 4:3 and 51 of them 3:4 and not one 16:9, so throwing away everything that crops badly would leave nothing. A museum collection is large enough that "only take the works that barely need cropping" is a real curation strategy, and the selection can happen in the source rather than in `[[pipeline.crop]]`.


## Metadata, which is how a collection gets narrowed

A collection is far too large to curate by hand, so what the source can filter on decides what the wall can be.

**The Art Institute is by far the richest, and its fields are genuinely filled in.** Measured over 500 public domain paintings: `artist_title` 98.2%, `date_start` 99.8%, `date_display` 100%, `classification_titles` 100%, `place_of_origin` 100%, `medium_display` 100%, `department_title` 100%, `subject_titles` 97.8%, `style_titles` 90.4%.

- `subject_titles` is the genre vocabulary and the one worth building on: `landscapes`, `portraits`, `trees`, `water`, `women`, `men`, `weather/seasons`, `religion`.
- `color` gives the dominant color as HSL plus the share of pixels it covers, on 99.4% of records, which is a palette filter.
- `colorfulness` is populated on every record and is not trustworthy. The Bedroom scores `0`, as do 83 of 500, so the number does not mean what the name suggests.
- `is_boosted` and `boost_rank` are AIC's own highlight flag, 229 public domain works. `theme_titles` are hand-curated lists like `Essentials` and `Art Institute Icons`, on 21% of records.
- `is_on_view` plus `gallery_title` is what is physically hanging today, 515 public domain paintings.

**The Met has comparable fields and a much worse shape to consume them in.** `artistDisplayName`, `artistNationality`, `objectBeginDate`/`objectEndDate`, `department`, `classification`, `medium`, `culture`, `period`, `isHighlight`, and `tags` carrying Getty AAT and Wikidata URLs. Its search endpoint takes `isPublicDomain`, `hasImages`, `medium` and `departmentId` but returns only a list of `objectIDs`, and there is no field selection, so a filterable local index means one request per object across roughly 490,000 of them. That is a bulk download rather than a query.

**Cleveland is keyless** and gives `creators`, `culture`, `technique`, `type`, `department`, `creation_date_earliest`/`latest`, `is_highlight`, `current_location` and `exhibitions`.

**The Rijksmuseum's key-based API is gone.** `www.rijksmuseum.nl/api/` returns `410`. It is replaced by a Linked Art API at `data.rijksmuseum.nl/search/collection`, keyless and cursor-paged, reporting 735,428 items with images. It is JSON-LD rather than the old flat records, so every tutorial written before the switch is wrong about the shape.

### Genre metadata is a shape filter

The useful finding, measured over the census. Filtering on subject nearly doubles the share of a collection that can fill the panel, because what a painting is of predicts how wide it is.

| Filter | Landscape | Inside a 15% crop budget | Inside 25% |
|---|---|---|---|
| everything | 44.6% | 12.0% | 23.4% |
| subject `landscapes` | 83.2% | 21.6% | 50.0% |
| subject `portraits` | 8.8% | 1.9% | 3.4% |
| `is_boosted` | 46.7% | 14.2% | 24.2% |

**Excluding portraits-the-genre is the single biggest lever**, and `is_boosted` is worth nothing for this, which is worth knowing before reaching for the highlight flag as a shortcut. Famous and fits-the-panel are unrelated.

**Nothing published captures composition busyness**, which `initial research.md` says matters more than anything else at 32". Image entropy is the only cheap proxy anyone has suggested.


## What this means for the design here

- **The matte is still the default answer**, because the ratios are more varied than the album's and `[art.matte_by_ratio]` already handles a shape it doesn't recognize through `art.fallback_matte`.
- **A museum source would exercise the fallback hard.** 4:5, 5:6, 11:14 and 1:1 are common in a collection and absent from a phone album, and only `flexible`, `shadowbox` and `none` are available for a shape that isn't 16:9. `SyncReport.fell_back` counting by ratio is what would say which keys to add.
- **A ratio filter belongs in the source, not the pipeline.** It decides what gets fetched at all, which is a different question from how a fetched image is framed, and only a source knows how many candidates it can afford to discard.
- **Composition matters more than ratio at 32".** `initial research.md` has the note that sprawling detailed scenes turn to mush on this panel, which is a stronger filter than any of this and much harder to automate.

None of this is decided. It's the input to the decision.


## Re-running the measurement

`.claude/tmp/census.py` builds the painting census and `.claude/tmp/summary.py` and `.claude/tmp/crop_loss.py` report over it, all of them scratch rather than deliverables. `census.py` runs on its own; the other two read JSON that `curl` fetched first:

1. Paintings, up to the API's 1,000 result cap on a search:
    ```bash
    curl -sS -X POST "https://api.artic.edu/api/v1/artworks/search" -H "Content-Type: application/json" -d '{"query":{"bool":{"must":[{"term":{"is_public_domain":true}},{"term":{"artwork_type_title.keyword":"Painting"}}]}},"fields":["id","title","thumbnail","artwork_type_title"],"limit":100,"page":1}'
    ```
1. Everything, paginated, filtering `is_public_domain` locally:
    ```bash
    curl -sS "https://api.artic.edu/api/v1/artworks?fields=id,title,thumbnail,artwork_type_title,is_public_domain&limit=100&page=1"
    ```

Three things to know before trusting a rerun.

**`thumbnail.width` and `thumbnail.height` are the source image's dimensions, not a thumbnail's.** The Bedroom comes back as `12614x9875`.

**Those dimensions track the artwork's own proportions closely but not exactly**, because the photograph carries a sliver of edge. The Bedroom is 73.6 x 92.3 cm, a ratio of 1.254, against 1.277 from the pixels. That's within 2%, which is the same tolerance `crop.RATIO_TOLERANCE` uses, so it's fine for distribution work and wrong for anything needing an exact ratio.

**The search endpoint's result ordering is clustered, so don't sample from it.** It caps at 1,000 results, and the first 1,000 public domain paintings it returns are 31% Century of Progress works, against 12.8% of the population. That block is unusually tall, so sampling that way understated the crop numbers by several points. The census dodges it by splitting on `date_start` into buckets that each come in under the cap and taking every bucket whole, which is what `census.py` does. The all-types numbers here are still a sample, paginated off `/artworks` rather than the search endpoint, so treat them as approximate next to the painting census.
