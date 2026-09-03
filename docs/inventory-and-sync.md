# Inventory and the sync diff

TL;DR: the inventory is a local JSON file that remembers which TV images this tool uploaded and where each came from, and the sync diff is the pure function that turns the album, the inventory, and `art.available()` into a list of uploads and deletes.

Both are buildable and testable with no TV attached, which is why they come before the TV wrapper. `../CLAUDE.md` has the architecture and the reasoning; this file is the detail a session needs to build them.


## Why the inventory exists

Without it there is no way to tell this tool's uploads apart from art added by hand or bought from the Art Store, so a mirror would either delete somebody's art or accumulate duplicates forever. It is also the only place the mapping from a source's id to the TV's `content_id` lives, because the TV stores nothing about where an image came from.

Deletes are scoped through it. A `content_id` the inventory doesn't attribute to the source being synced is never touched, and that rule is what makes it safe to run `frame sync` against a TV that has art on it from anywhere else.


## Shape

One JSON file next to `config.toml`, gitignored, resolved against the config file's directory the way `token_file` is. Keyed by `content_id`, because that's the only id the TV will ever hand back.

Each entry holds the source name, that source's item id, and when it was uploaded. The source name and the source item id together are the identity; the timestamp is bookkeeping, so that a stale entry matching nothing on either side is still explicable a year from now. A `version` field sits alongside the entries, and a reader ignores any field it doesn't recognize, so a later version can add one without breaking a file today's code wrote.

The file has to survive being deleted. Losing it means losing the attribution, so the tool should refuse to delete anything at all when the inventory is missing rather than treat an empty inventory as "nothing is mine". `Inventory.existed` is what tells those two apart, since a file that is merely absent looks exactly like one that owns nothing.


## What an entry does not record

There is no fingerprint of the photo's content, so editing a photo in Google Photos after it was uploaded -- a crop, a filter, a straighten -- is invisible to sync, and the old version stays on the TV until the photo is removed from the album and added back.

That is a deliberate limit rather than an oversight. Photos in an album for the Frame get added and removed far more often than they get edited in place, and the workaround costs one round trip through the album when it does happen.

Closing it later is additive. Store a fingerprint beside `source_id`, treat a changed fingerprint as a delete plus an upload, bump the format version, and treat an entry written without one as unfingerprinted and therefore unchanged. Two candidates, in order of appeal:

* `item[3]` on the album page, the 27-character hash G2 found stable across fetches. It costs nothing, because the scraper already reads past it. Nobody has confirmed it tracks an edit rather than being a second media key, though, and editing one photo and re-running `.claude/tmp/g_probe.py` settles that in a few minutes. If the hash doesn't move, there is nothing free to use.
* A hash of the downloaded bytes, which G2 already named as the fallback. It cannot be wrong about the pixels, and it costs a download of every photo on every run, which is the whole reason G4 exists.


## The diff

Three inputs, and it must be a pure function over them so it can be tested without hardware:

1. The source's items, from `Source.items()`.
2. The inventory.
3. Whatever `art.available()` returned.

`SyncPlan` carries five lists: `upload`, `delete`, `keep`, `orphaned`, and `unmanaged`. The orphaned one matters and is easy to forget. An inventory entry whose `content_id` is no longer on the TV means somebody deleted it through the TV's own UI, and the right response is to drop the entry and re-upload rather than to error, so an orphaned entry whose photo is still in the album also turns up in `upload`. `unmanaged` is everything on the TV the inventory doesn't claim, and it exists so a dry run can say out loud what it is leaving alone.

Rules worth stating, because each one is a bug if missed:

* An item in the album with no inventory entry is an upload.
* An inventory entry for this source whose source item id is no longer in the album is a delete.
* An inventory entry whose `content_id` is absent from `available()` is orphaned, not a delete.
* A `content_id` in `available()` that the inventory doesn't know about belongs to somebody else. Leave it.
* An inventory entry attributed to a different source is not this sync's business, even if its source item id looks familiar.
* Two entries for one source item can only come from a run interrupted between the upload and the write. The older entry stands and the extra image is a delete, so an interrupted run heals itself on the next one instead of leaving a duplicate on the wall forever.


## Hazards the TV imposes

These come out of `spikes.md` and each one silently corrupts the diff if it isn't handled.

* **`available()` is a concatenation of per-category listings, not a list of items.** An image comes back once per category it appears under, with a different `category_id` on each row, and the Art Store item has been seen twice under one category with differing `slideshow` flags. Dedupe on `content_id` before diffing. More importantly, **`category_id` is not authoritative and must never be used to decide whether an image is yours.** The inventory is the only thing that answers that.
* **`delete()` returns a bool that proves nothing.** It has returned `True` for deletes that did happen, but the return value is not evidence. This is the one place a mistake destroys photos, so confirm a delete by re-reading `available()` rather than by trusting the call.
* **The Art Store's live stream image appears in `available()`** as a `SAM-...` id with `content_type: server`, and it changes as the stream rotates. Filter those out or every run sees a phantom add and a phantom delete.
* **Uploaded images come back as `MY_F0001`**, with an underscore, while categories use the `MY-C0002` shape with a hyphen. Don't pattern match on one and expect the other.
* **`set_favourite` is broken on this firmware.** It returned `error -7` once and has hung on every call since, so anything built on favourites as a playlist needs a different plan.


## Where this lives

`inventory.py` holds the entries and the file, and `sync.py` holds `plan_sync()`, which has no I/O in it at all. `tests/test_inventory.py` and `tests/test_sync.py` cover both, including every rule above.

Nothing calls either of them yet. Wiring them to `frame sync` needs the TV wrapper, and two things belong to that wiring rather than to the diff: refusing to act at all when `Inventory.existed` is false, and confirming a delete by re-reading `available()` rather than by trusting what `delete()` returned.
