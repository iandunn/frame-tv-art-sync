# Photography sources, and the ones that already fit the panel

TL;DR: no photograph source is natively 16:9 either, but a public domain nature photo loses 15.6% of its height filling the panel where a painting loses 25%, and the National Park Service's own archive is keyless, human-curated, and reports each image's dimensions in its metadata.

`museum-sources.md` is the same survey for museum art. The two split because the sources behave differently rather than because the subject differs: a museum knows the shape of its collection and publishes nothing that fits a TV, while a photo archive is full of camera-native shapes that nearly fit.


## Why look past museums at all

A crop to fill the panel costs one dimension entirely. For a ratio `r` the fraction that survives is `r / (16/9)` when the image is narrower than the panel and `(16/9) / r` when it is wider. So:

| Ratio | Where it comes from | Filling the panel costs |
|---|---|---|
| 16:9 | broadcast, wallpapers, AI | nothing |
| 2:1 | panoramas | 11.1% of the width |
| 3:2 | every DSLR | 15.6% of the height |
| 4:3 | every phone, most paintings | 25.0% of the height |
| 5:4, 4:5, 3:4 | paintings, phone portrait | a quarter to well over half |

**3:2 versus 4:3 is the whole argument for photography.** A museum collection is mostly the bottom half of that table and a photo archive is mostly the middle, and the gap between 15.6% and 25.0% is the difference between a crop you accept and one you notice.

**Which also means a loss budget written at 15% is in exactly the wrong place.** 3:2 misses it by six tenths of a point. Across the eight NPS galleries measured below, a 15% budget keeps 9 images out of 286 and a 16% budget keeps 86. One point is a factor of ten, so write the budget at 16% or at 25% and never at 15%.


## Sources that are natively 16:9

Only one of these filters on ratio as a first-class query.

**Wallhaven** takes `ratios=16x9` and `atleast=1920x1080` directly, keyless. Measured 2026-09-07: 145,449 SFW general-category images match, and about as many again under its anime category. Two things to know. Its content is user-uploaded with **no license metadata at all**, so it is fine for a wall of your own and can never be redistributed, and this project going open source does not change that because the tool ships code rather than images. And `sorting=toplist` silently applies a one-month window, which is why the same query answered 539 before it answered 145,449.

```bash
curl -s "https://wallhaven.cc/api/v1/search?categories=100&purity=100&ratios=16x9&atleast=1920x1080"
```

**Unsplash and Pexels** are 16:9 on demand rather than natively. Both take `orientation=landscape` and both serve arbitrary crops from their CDN, so any photo comes back at exactly 1920x1080. Both need a free key. `angelopoggi/FrameSurfer` is the existing project doing this, for one random Unsplash pick.

**Openverse** documents an `aspect_ratio=wide` filter and is WordPress's own CC search, which makes it the natural fit. It is unverified here: `api.openverse.org` answered `504` to every call on 2026-09-07, including a bare query with no filters.

**ESA/Hubble, Webb and ESO** publish explicit desktop-wallpaper renditions per image, 1920x1080 among them, under CC BY. There is no JSON API; every `format=json` path on `esahubble.org` returns HTML, so this one needs scraping. NASA's `images-api.nasa.gov` does have a real API and no ratio filter, with each asset's `collection.json` listing whatever renditions exist.

**Generated images** are the trivial case, since you ask for 1920x1080 and get it. Three of the Frame projects on GitHub do only this.

**The panel being FHD rather than 4K is an advantage here.** A 1920x1080 wallpaper is an exact pixel match, so nothing is cropped, nothing is upscaled, and the whole shape problem disappears. The bigger Frames don't get that.


## IIIF is a framework, not a source

Worth being clear about, because it reads like a collection. IIIF is a URL grammar that museums implement on their own image servers, so there is no IIIF archive to browse. What it buys is a server-side crop: you ask for a region and a size and the museum's machine does the work, which is how you get 1920x1080 out of a 200 MP TIFF without downloading it.

**Never ask for `w,h`.** A level-2 server advertises `sizeByDistortedWh`, so `1920,1080` returns exactly 1920x1080 by stretching the image. Compute the 16:9 region first and then ask for `1920,` with the height left off. Verified against the Art Institute's server, which reports `level2` with `regionByPct`, `regionByPx`, `sizeByW` and `sizeByPct`: the first form came back distorted and the second came back honest.


## Region filtering, which is what a nature source needs

**Wikimedia Commons geosearch** is keyless and one call. Against Mount Rainier's coordinates at an 8 km radius it returns real photography with dimensions and licenses in the same response. A sparse area degrades badly, though: a point in the Cascades wilderness came back as eight ISS photographs of Earth, all pinned to the same coarse coordinate 486 m away, because nothing else nearby is geotagged.

```bash
curl -s "https://commons.wikimedia.org/w/api.php?action=query&generator=geosearch&ggscoord=46.8523|-121.7603&ggsradius=8000&ggslimit=12&ggsnamespace=6&prop=imageinfo&iiprop=url|size|extmetadata&format=json"
```

Commons also keeps human-vetted sets that sidestep geosearch's noise. `Category:Featured pictures of landscapes` holds 512 files across 13 subcategories and `Category:Featured pictures of mountains` holds 162. Small, but each one passed a review.

**Flickr is the most capable and is untested here.** `flickr.photos.search` documents `bbox` or a `lat`/`lon`/`radius` triple, a `license` filter that isolates CC0 and public domain, `tags`, and `sort=interestingness`. It needs a free key. It is also the way into the US government photostreams, Forest Service and BLM and individual national forests, which are public domain and heavily Pacific Northwest.


## The National Park Service archive

This is the promising one, and almost everything about it was expensive to find out.

**There are two NPS APIs and only one of them is open.** `developer.nps.gov` answers `403` without a key and serves park info plus website banner images. NPGallery is the actual asset archive and needs no key at all.

**An `nps.gov/media/photo/gallery.htm?id=<guid>` page is an NPGallery album.** The page itself is a shell that prints "Gallery is loading..."; its inline JS reads `album_id` and calls `/npgallery/api/search/execute/albumid/<guid>`. Both halves work against `npgallery.nps.gov` directly:

```bash
curl -s "https://npgallery.nps.gov/Album/<guid>"
curl -s "https://npgallery.nps.gov/api/search/execute/albumid/<guid>?pagesize=500&primarytype=image"
curl -s "https://npgallery.nps.gov/GetAsset/<asset-id>/original"
```

The album call gives `AssetCount`, `Categories` (the curated scenic galleries all report `["Scenic"]`), `Keywords`, `Description`, the `NPSUnits` and `Locations` with a unit code and lat/lon, and `ConstraintsInformation`, which came back `{'Constraint': 'Public domain', 'GrantingRights': 'Full'}` on every asset examined.

**Dimensions are in the metadata, so nothing has to be downloaded to decide.** Each asset carries `FileInfo.Original.Width` and `Height` alongside pre-made `ProxyHiRes` at 1000px and `ProxyLoRes` at 500px. The plain `/Search/Results` endpoint omits `FileInfo`, which is what made this look impossible at first.

**`unitcode`, `state`, `primarytype` and `pagesize` work; `categories`, `assetbasetype` and `q` are silently ignored.** An ignored filter leaves `ResultCount` at 723,257, the whole archive, which is the tell. So scenic filtering has to happen locally, off the album's `Categories` and each asset's `Keywords`. Counts measured 2026-09-07: `state=WA` 9,359 images, `state=OR` 2,720, `state=CA` 67,808, `unitcode=mora` 3,234, `noca` 3,612, `crla` 2,245, `olym` 1,427. `pagesize=500` is honored, so the whole of Oregon and Washington is about 25 requests.

**`state` cross-tags, so don't trust it as a filter on its own.** Yosemite turns up under Oregon and Great Smoky under Washington, because an asset can carry several `NPSUnits`.

A rate limit should be used when downloading from them. If they respond with headers that tell how long to wait for etc those should be respected. But regardless there should be at least a 20ms pause between connections, or some other reasonable/conventional number.

### Which units are worth pointing at

Scenic units with real holdings, from NPGallery rather than a park list, so these are units that actually have photographs. Counts are unit-tags in a sample rather than totals.

- **WA:** `MORA` 1397, `LARO` Lake Roosevelt NRA 787, `NOCA` 359, `OLYM` 249, `LACH` Lake Chelan NRA 6, plus `ROLA` Ross Lake. `LACH` and `ROLA` both redirect to `noca`, because the three are run as one complex.
- **OR:** `CRLA` 2127, `JODA` John Day Fossil Beds 478, `ORCA` Oregon Caves 80.
- **Northern CA:** `PORE` Point Reyes 654, `PINN` Pinnacles 642, `GOGA` Golden Gate 632, `PRSF` Presidio 287, `YOSE` 283, `SEKI` 247, `ALCA` Alcatraz 241, `LAVO` Lassen 143, `REDW` Redwood 134, `MUWO` Muir Woods 81, `WHIS` Whiskeytown 63, `JOMU` John Muir 27, plus `LABE` Lava Beds and `DEPO` Devils Postpile.

The state pages are scrapable after all, which is the cheap way to get a complete list of codes:

```bash
curl -s -L https://www.nps.gov/state/wa/index.htm | grep -o -E '/[a-z]{4}/'
```

**Most of the region's famous landscapes are not NPS**, so they need separate sources: Mount St. Helens and the Columbia River Gorge are Forest Service, the Pacific Crest Trail is Forest Service, Hanford Reach is Fish and Wildlife, and the San Juan Islands and Cascade-Siskiyou monuments are BLM.

### Explored so far

Thirteen `Categories: ["Scenic"]` galleries are worth incorporating, covering `MORA`, `CRLA`, `NOCA` and `OLYM`. Oregon and Northern California have more that nobody has looked at yet.

**Finding a park's galleries is a two-step walk, and the first step is the one that isn't obvious.** A park's own photo page lists them, at `https://www.nps.gov/<code>/learn/photosmultimedia/photogallery.htm` with the four-letter unit code from the list above, and each link off it carries the album guid this archive is keyed on.

* https://www.nps.gov/media/photo/gallery.htm?pg=5003191&id=CA4C9908-155D-4519-3E19303DAEADE22C
* https://www.nps.gov/media/photo/gallery.htm?pg=5003191&id=CA253B62-155D-4519-3E5F99B8379865C0
* https://www.nps.gov/media/photo/gallery.htm?pg=5003191&id=A15BD33D-AADB-4BD9-B9CA-8561D33545AB
* https://www.nps.gov/media/photo/gallery.htm?pg=5003191&id=43A7D892-A321-4FBD-B5BF-D24A8A415F31
* https://www.nps.gov/media/photo/gallery.htm?pg=5003191&id=3EB2379E-AD8F-162A-F77A7B8FBFA0F051
* https://www.nps.gov/media/photo/gallery.htm?pg=5003191&id=EC76595D-E208-4EB9-BE68-1C29BEAC1E2C
* https://www.nps.gov/media/photo/gallery.htm?pg=6741680&id=F23B92B0-155D-4519-3E8887C9EDC70F23
* https://www.nps.gov/media/photo/gallery.htm?pg=6741680&id=F0A2A880-155D-4519-3EE45D0598311AD3
* https://www.nps.gov/media/photo/gallery.htm?pg=146830&id=BEE18C1E-1DD8-B71B-0B51590AC298A184
* https://www.nps.gov/media/photo/gallery.htm?pg=127759&id=66E97C5E-1DD8-B71B-0B92E15EB9E06325
* https://www.nps.gov/media/photo/gallery.htm?pg=127759&id=EDDA6951-155D-4519-3EB37B1D3201D119
* https://www.nps.gov/media/photo/gallery.htm?pg=127759&id=8F53BDAF-C157-1F5E-5019BD1D5AACF39E
* https://www.nps.gov/media/photo/gallery.htm?pg=127759&id=EDB80BF2-155D-4519-3E6A4E1C7C0DAC93


### What the curated galleries actually hold

Measured over 286 images across eight `Categories: ["Scenic"]` albums, six from Mount Rainier and two from Olympic.

**Not one image is 16:9.** They are camera-native, and the shapes are exactly what park staff shoot: 99 at 4:3, 58 at 3:2, 37 at 3:4, 20 at 2:3. 73% are landscape.

| Crop to 16:9 loses | Images | Share |
|---|---|---|
| 0-5% | 0 | 0% |
| 5-15% | 9 | 3.1% |
| 15-16%, which is 3:2 | 77 | 26.9% |
| 16-25%, which is 4:3 | 115 | 40.2% |
| over 25%, portraits | 85 | 29.7% |

Applying every bar at once -- landscape, still 1920x1080 or better after a 16:9 crop, losing 25% or less -- leaves **154 of 286**.

**51 landscape images are too small**, some as low as 500x375 and 800x404, sitting in the same albums as 6000x4000 originals. Filtering on `FileInfo.Original` is mandatory rather than a nicety.

Two galleries are worth the whole exercise and two are worth nothing. The Mount Rainier fall-color album is 38 images with every original big enough, and the Olympic park-wide album is 115 images with 34 clearing every bar. The fire-lookouts album and the Olympic Wilderness Coast album yield zero each.


## The 3:2 experiment, prepared and not yet run

`.claude/tmp/nps_spike.py` pulls the 3:2 landscape originals from those eight albums, crops each to 16:9 through the real `pipeline.prepare()` and `crop.py` rather than a parallel implementation, and writes them to `.claude/tmp/nps/`. Uploading sits behind `--upload` because the art channel takes one process at a time.

70 photos qualify, 40 from Rainier and 30 from Olympic, and **resolution is a non-issue**: every original was oversized, most at 6000x4000 or 5472x3648, so all 70 downscale into the panel and none needs upscaling.

Three things the run has to answer or work around.

**The anchor is the real question, not the 15.6%.** Center splits the loss evenly top and bottom, which reads fine on a mountain and eats the foreground of a beach photo. `Driftwood on Kalaloch Beach` and `Ruby Beach tidepool` both put their subject low in the frame. One anchor across 70 photos will be wrong for some of them, and that is what the run is for. `--anchor top` and `--anchor bottom` are there, and `--label` burns the title in so the copies can be told apart on the panel.

**The uploads land unmanaged.** The spike writes no inventory entry, so `frame sync` reads all 70 as art added by hand and leaves them alone. Clearing them afterwards takes `frame delete by-hand`, `frame bakeoff --clear`, or a run with `sync.delete_added_by_hand`.

**70 back-to-back uploads is close to the number that has broken the Art app.** T18 has it falling over at 93, so start with `--limit 25` and read the upload timings.
