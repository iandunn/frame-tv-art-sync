# Inventory and the sync diff

TL;DR: the inventory is a local JSON file that remembers which TV images this tool uploaded and where each came from, and the sync diff is the pure function that turns the album, the inventory, and `art.available()` into a list of uploads and deletes.

Both are buildable and testable with no TV attached, which is why they come before the TV wrapper. `../CLAUDE.md` has the architecture and the reasoning; this file is the detail a session needs to build them.


## Why the inventory exists

Without it there is no way to tell this tool's uploads apart from art added by hand or bought from the Art Store, so a mirror would either delete somebody's art or accumulate duplicates forever. It is also the only place the mapping from a source's id to the TV's `content_id` lives, because the TV stores nothing about where an image came from.

Deletes are scoped through it. A `content_id` the inventory doesn't attribute to the source being synced is never touched by default, and that rule is what makes it safe to run `frame sync` against a TV that has art on it from anywhere else. `sync.delete_added_by_hand` is the one thing that widens it, and it widens it only to images the TV itself reports as uploads; Samsung's own art stays out of reach whatever the config says.


## Shape

One JSON file next to `config.toml`, gitignored, resolved against the config file's directory the way `token_file` is. Keyed by `content_id`, because that's the only id the TV will ever hand back.

Each entry holds the source name, the source item ids the image holds, when it was uploaded, and a `render` table saying what settings produced the copy on the TV. The source name and that ordered list of ids together are the identity, and the list is a list even where it holds one, because `[[pipeline.composite]]` can put several photos into one image. Order is part of it because it is part of the image: the same two photos left-to-right and right-to-left are different pixels. `docs/composites.md` has the grouping that produces the list, and it is derived from the album rather than remembered here. the timestamp is bookkeeping, so that a stale entry matching nothing on either side is still explicable a year from now. A `version` field sits alongside the entries, and a reader ignores any field it doesn't recognize, so a later version can add one without breaking a file today's code wrote.

The render table holds the matte id, the layout, the mat color, the edge treatment and the two gap floors, a crop and anchor per photo the image holds, whether the copy carries a burnt-in label, the highlight rolloff, the JPEG quality, and a pipeline version. The crops run in step with the source ids rather than once for the entry, because two photos of one shape can reach it by different anchors when `[pipeline.crop_overrides]` names one of them. `render.py` owns it. It exists because the TV can be asked what it holds but not how it was made: `available()` reports a `matte_id` and the stored dimensions and nothing about the tone curve or the encode, and `get_thumbnail` hangs on this firmware, so the pixels can't be fetched back either. Without the record there is no way to tell a photo rendered under today's config from one rendered under last month's, and since a matte can only be set at upload time, that is the whole question behind changing one.

It records the crop's **effect** rather than the rule that produced it, which is what makes `sync.short_run` unnecessary for a config edit: rewriting a `when` clause so that a different rule catches the same photo the same way replaces nothing, while changing a `to` or an `anchor` replaces exactly the photos that rule reaches. The matte is chosen from the shape the crop leaves rather than the shape the source reported, because that is the shape the panel is handed and a rule can turn a portrait into a landscape.

Three details in it are worth knowing. The rolloff is rounded on the way in, so a file that says `0.10` and a config that says `0.1` describe the same rendering rather than costing a re-upload of everything. The `labelled` flag is there because `frame sync --label` burns the crop that fired into the middle of the image, so a labelled copy really is different pixels; without it the record would describe a photo that isn't on the wall, and the plain sync afterwards would take the labels off by accident rather than on purpose. And the pipeline version is what catches a change to the code rather than to the config -- a different resampling filter, a step added -- which nothing else would notice, since no config value moves when the pipeline changes. Bump it in `pipeline.py` whenever the same photo and the same config would come out different.

An entry with no `render` table is one written before records existed, and so is every entry in a file whose `version` is below the current one, since a record from then describes a shape today's code has nothing to compare against. Either way it reads as unknown, and unknown loses: those photos are uploaded again. A `render` table that is present but unreadable is an error rather than an unknown, because the alternative is guessing at a value that decides whether an image is destroyed.

The file has to survive being deleted. Losing it means losing the attribution, and the damage is not the deletes it would cause but the uploads: with nothing attributed, every photo in the album reads as new, so the run uploads a second copy of each and the originals become unmanaged, reachable afterward only by a run with `sync.delete_added_by_hand` turned on. `Inventory.existed` is what tells a lost file apart from a first run, since one that is merely absent looks exactly like one that owns nothing.

So a run with no inventory refuses as soon as it sees the TV holding anything, and `--first-run` is what says none of it came from this tool. The Art Store's own `SAM-` images don't count toward that, or a genuine first run against a TV showing the Store would refuse to start.


## What an entry does not record

There is no fingerprint of the photo's content, so editing a photo in Google Photos after it was uploaded -- a crop, a filter, a straighten -- is invisible to sync, and the old version stays on the TV until the photo is removed from the album and added back. The render record does not help here, and the split is worth being clear about: it says how *this tool* rendered what the source gave it, and nothing about what the source gave it.

That is a deliberate limit rather than an oversight. Photos in an album for the Frame get added and removed far more often than they get edited in place, and the workaround costs one round trip through the album when it does happen.

Closing it later is additive. Store a fingerprint per source id, treat a changed fingerprint as a delete plus an upload, bump the format version, and treat an entry written without one as unfingerprinted and therefore unchanged. Two candidates, in order of appeal:

* `item[3]` on the album page, the 27-character hash G2 found stable across fetches. It costs nothing, because the scraper already reads past it. Nobody has confirmed it tracks an edit rather than being a second media key, though, and editing one photo and re-running `.claude/tmp/g_probe.py` settles that in a few minutes. If the hash doesn't move, there is nothing free to use.
* A hash of the downloaded bytes, which G2 already named as the fallback. It cannot be wrong about the pixels, and it costs a download of every photo on every run, which is the whole reason G4 exists.


## The diff

Three inputs, and it must be a pure function over them so it can be tested without hardware:

1. The source's items, from `Source.items()`.
2. The inventory.
3. Whatever `art.available()` returned.

`SyncPlan` carries eight lists: `upload`, `delete`, `delete_unmanaged`, `superseded`, `keep`, `orphaned`, `left_in_place`, and `unmanaged`. The orphaned one matters and is easy to forget. An inventory entry whose `content_id` is no longer on the TV means somebody deleted it through the TV's own UI, and the right response is to drop the entry and re-upload rather than to error, so an orphaned entry whose photo is still in the album also turns up in `upload`. The last two are what a delete flag decided against, and they are filled whether or not their flag is on, so a dry run can say out loud what it is leaving alone.

Rules worth stating, because each one is a bug if missed:

* The diff runs over groups rather than photos, a group being the ordered set of source ids that will share one image, so every list in `SyncPlan` names groups.
* A group with no inventory entry is an upload.
* A group whose entry records a different rendering from the one config asks for is an upload *and* a `superseded`. The two are one operation: the replacement goes up first and the old copy comes down afterward, so the photo is never off the wall and a run that dies in between leaves a duplicate rather than a hole. An entry with no record at all is treated the same way, since nothing else says how that copy was made.
* A superseded copy is only taken down once its replacement is confirmed up. Deleting one whose upload failed would take the photo off the wall to make room for a copy that doesn't exist.
* An inventory entry for this source whose group no longer exists is a delete, or `left_in_place` when `delete_removed_from_album` is off. That covers a photo that left the album and a group that regrouped around an insertion alike, since neither is a group this run wants.
* **An entry sharing any photo with a group this run does want is a delete, and no flag gates it.** That photo is going back up inside a different image, so leaving the old one puts it on the wall twice. It is the duplicate rule below rather than a mirroring decision, which is why `delete_removed_from_album` must not reach it.
* An inventory entry whose `content_id` is absent from `available()` is orphaned, not a delete, whatever the flags say.
* A `content_id` in `available()` that the inventory doesn't know about belongs to somebody else. Leave it, unless `delete_added_by_hand` is on and every row the TV returns for it says `content_type: mobile` and its id doesn't start with `SAM-`.
* An inventory entry attributed to a different source is not this sync's business, even if its source item id looks familiar.
* Two entries for the identical group can only come from a run interrupted between the upload and the write, which a re-render makes routine rather than rare. The entry whose record matches config stands and the other image is a delete, so an interrupted run heals itself on the next one instead of leaving a duplicate on the wall forever. Preferring the *older* entry, which is what this did before records existed, would have an interrupted re-render delete the new copy and keep the old one, and every later run would make the same replacement again. With nothing to choose between them -- neither matching, or the photo no longer in the album -- the newest upload stands. **No flag gates any of that**, because a duplicate is one photo twice over rather than a photo that left the album, and a flag answering the second question must not decide the first.

The last rule generalizes, and it is the rule to keep: a list in `SyncPlan` that no flag names is acted on unconditionally. The flags decide whether this tool may stop mirroring a photo, so anything that lands in the plan for some other reason -- a duplicate, a copy superseded by a re-render -- inherits that default rather than a gate nobody remembered to write.


## Hazards the TV imposes

These come out of `spikes.md` and each one silently corrupts the diff if it isn't handled.

* **`available()` is a concatenation of per-category listings, not a list of items.** An image comes back once per category it appears under, with a different `category_id` on each row, and the Art Store item has been seen twice under one category with differing `slideshow` flags. Dedupe on `content_id` before diffing. More importantly, **`category_id` is not authoritative and must never be used to decide whether an image is yours.** The inventory is the only thing that answers that.
* **`delete()` returns a bool that proves nothing.** It has returned `True` for deletes that did happen, but the return value is not evidence. This is the one place a mistake destroys photos, so confirm a delete by re-reading `available()` rather than by trusting the call.
* **The Art Store's live stream image appears in `available()`** as a `SAM-...` id with `content_type: server`, and it changes as the stream rotates. Filter those out or every run sees a phantom add and a phantom delete.
* **Uploaded images come back as `MY_F0001`**, with an underscore, while categories use the `MY-C0002` shape with a hyphen. Don't pattern match on one and expect the other.
* **`set_favourite` is broken on this firmware.** It returned `error -7` once and has hung on every call since, so anything built on favourites as a playlist needs a different plan.


## Where this lives

`inventory.py` holds the entries and the file, and `sync.py` holds `plan_sync()`, which has no I/O in it at all. `tests/test_inventory.py` and `tests/test_sync.py` cover both, including every rule above.

`syncer.py` is what acts on a plan, and it holds the two rules that belong to the wiring rather than to the diff: refusing to act when `Inventory.existed` is false and the TV already holds something, and confirming a delete by re-reading `available()` rather than by trusting what `delete()` returned. `tests/test_syncer.py` covers both, and `tests/test_cli_sync.py` covers what `frame sync` reaches for before it commits to anything.


## Running a plan

The order is fixed by two constraints that pull in different directions.

A run opens the channel, reads `available()`, refuses a lost inventory, and closes again before it downloads anything. Preparing the album fetches every photo in it, so a run that dies afterwards spent that bandwidth for nothing, and the two things that kill one at that point are both knowable up front: a TV that is off, on another subnet, or wedged, and an inventory that has gone missing. What the check cannot promise is that an upload will finish, since one has failed 96 into a run with the channel perfectly healthy. It establishes that the handshake completes and the TV answers a request, which is the whole of what is knowable without uploading something.

Connecting twice in one command is what `tv.REOPEN_GAP_SECONDS` exists for. The TV takes about ten seconds to notice a client with this name has gone, so `_Channel.close()` records when it closed and `_connect` waits out the remainder of the gap before opening again. Anything done in between counts toward it, so a run that prepares an album waits nothing at all, and only a run with a handful of photos to prepare pays anything. Waiting is cheaper than tripping the window and recovering, because the recovery is `RETRY_DELAY_SECONDS` at 25 against a gap of 12.

Photos are fetched and put through the pipeline *before* the uploading channel is opened, into a spool directory. That channel closes itself after about 25 seconds of silence, so an interleaved loop would put a live HTTP fetch in every gap between two uploads and one slow response would kill the run partway through. Prefetching also means every download and decode failure surfaces while the TV is still untouched. What can be spooled is every photo belonging to a group with no inventory entry, plus every photo in a group whose entry records a different rendering from the one config asks for -- and on the first run after a format version changes that is every photo on the TV. Missing that second class would put 174 live downloads inside the open channel, which is the exact failure the spool exists to prevent. A photo still in the album whose image was deleted from the TV by hand isn't known to need re-uploading until `available()` has been read, so those few are fetched live under a tighter timeout.

Uploads then happen before deletes, because the album is a couple of hundred megabytes against the six gigabytes the TV has, so there is no reason to empty the wall before filling it. That ordering is what makes a replacement safe as well as economical, since the new copy is up before the one it supersedes comes down.

An inventory save follows each upload rather than the run, so an interrupted run leaves at most one image unaccounted for. The drop of an orphaned entry and the record of its replacement go into the same save, because written separately they leave the two-entries-for-one-photo state that the "older entry stands" rule exists to heal.

A photo that can't be fetched, decoded, or that the TV refuses is named and skipped, and the run carries on and exits non-zero at the end. Anything that reaches the channel itself, a timeout or an unreachable TV, aborts the run, because after one of those nothing else would succeed either. An abort still prints what the run managed, by way of `SyncAborted` carrying the report past the handler that would otherwise reduce it to an error message. That matters because the inventory holds every upload that landed, so a re-run resumes rather than starting over, and the summary is what says so.

A download is retried up to three times with a growing wait on a 429 or a 5xx, honoring `Retry-After` when it asks for longer than the backoff would. A 404 or a 403 is given up on immediately, because those describe the request rather than the moment. A whole album goes out as one burst on a first run and that is the only time this is likely to matter; 94 serial fetches have gone through untroubled, so nothing here is a response to an observed limit.


## Refusing to mirror nothing

An empty item list is refused rather than mirrored, in two places. `parse_album_page` raises when the page lists no photos, because a page whose shape has drifted looks exactly like an album somebody emptied. The CLI then refuses an empty list from any source, which is where a future local-folder or museum source is caught.

Mirroring nothing means deleting everything, and only one of the two readings is recoverable: an album you really did empty can be cleared off the TV by hand, while a scraper that quietly returned nothing would delete the whole collection before anyone noticed.


## Measuring a run

`frame sync` prints each upload's dimensions, aspect ratio, matte, and size before sending it, and the `content_id` and elapsed time after, then a fastest/median/slowest summary with the mean of the last ten. An upload whose ratio `[art.matte_by_ratio]` doesn't name is marked as having taken the fallback, and the run ends with a count per ratio, so an album that has grown a shape worth choosing for says which key to add. The details go out before the call rather than after it, because a request that never answers is exactly the one whose details are wanted.

That series is the measurement that matters, because it separates two failure modes that look identical from the outside. Times that climb toward the deadline mean the Art app is wearing down under a long run, which a pause between uploads might help; a flat series ending in one hang means a single event, which a pause would not touch. `tv.upload_pause` in the config is that pause, off by default and unproven.
