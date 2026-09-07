# The log, and troubleshooting

TL;DR: every run appends to `frame.log` beside your config with secrets masked, and the failures below are the ones that have actually happened.


## The log

Every run appends to `frame.log` beside your config, rotating at 5 MB and keeping three generations. It holds this tool's own progress and every frame the TV sent, which is the only way to see the ones nothing asked for, such as the TV announcing that it has left art mode. That matters because the run worth having a log of is the one you didn't expect to fail.

The TV's token and address and the album's share key are masked going in, so the log is safe to attach to an issue. Read it before you do, all the same. `--debug` prints the frames as they arrive as well, which is only worth it when you're watching a run live.


## Troubleshooting

**The pairing prompt never appears.** The TV remembers a denial and won't ask twice. Clear the entry from Device List under Settings > General > External Device Manager > Device Connect Manager, then run `frame pair` again.

**A command run right after another one pauses for 25 seconds.** That's expected. The TV keys its Device List on the client name and takes about ten seconds to notice a client left, so a second connection inside that window gets silence; the command waits the TV out and connects on the second try. Only that one failure is waited out, so a TV that's off or unreachable still fails straight away.

**Everything broke after a TV software update.** Tizen updates have flipped Access Notification back off and invalidated tokens. Check that setting, delete the token file, and re-pair.

**"No route to host" for an address you know is up.** On macOS, Local Network privacy gates the terminal app rather than the script, and a denial looks identical to the TV being absent. Grant your terminal access under System Settings > Privacy & Security > Local Network. Internet traffic keeps working while LAN traffic doesn't, so that symptom on its own doesn't tell you the TV is off.

**A sync run fails to read the album.** Google doesn't document the page this scrapes and can change it whenever they want, so treat this as expected maintenance rather than a surprise. `spikes.md` records the structure the parser expects.

**"The album paginates."** Google's page has carried every item in every album this has been pointed at, the largest of them 237 photos, so this shouldn't come up, but there's no telling where the limit sits. Redeeming the continuation token isn't implemented, and reading half an album would look like you'd deleted the other half, so `frame sync` refuses to run at all rather than mirror a partial list. If you hit it, take photos out of the album until it runs, and open an issue with the count that broke it.

**A sync run wants to re-upload the entire album.** That happens after the album is unshared and re-shared under a new link, if Google hands out new ids for the same photos. Nothing is lost. The run uploads everything again and deletes the copies it uploaded before, and every run after it is stable, so the cost is upload time. It's worth letting it finish rather than interrupting it, because a partial run leaves both copies on the TV.

**A sync run wants to re-upload photos you didn't change.** Check whether you changed a matte, a crop rule, or anything under `[pipeline]`. Every one of those is recorded per photo, so moving any of them replaces the photos it reaches. `frame sync --dry-run` says which settings moved before you pay for it, and `sync.short_run` is how to try one on a handful rather than on the album. `framing.md` has the rest.
