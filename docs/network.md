# Omada network

How the LAN is laid out so a machine on the private network can drive the Frame TV without the TV, or anything else untrusted, being able to reach back. Addresses appear as `<tv-ip>`, `<controller-ip>` and `<gateway-ip>`; substitute your own. All of it should hold for any Omada setup with an ER-series gateway and EAP access points.

**TL;DR:** The TV refuses any control connection from a client on a different subnet, paired or not, so it can't be isolated on a VLAN of its own while the controlling machine stays on `Private`. `spikes.md` T2c has that finding. The working design instead puts the TV on its own `Fenced` SSID on the `Private` VLAN and fences it with an EAP ACL that permits only the controlling machine and the gateway. The Working Design at the bottom has the rules themselves; the sections between here and there are the general Omada mechanics it rests on.


## Layout

One network and one SSID on it carry the design. Whatever else the gateway routes is beside the point, and nothing below assumes how many there are.

| Network | Holds | SSID |
|---|---|---|
| `Private` | Trusted devices, plus everything wired, because an unmanaged switch can't put a port on another VLAN | Main SSID |
| `Fenced` (on `Private`) | The Frame TV, and any device that has to share a subnet with the machines that drive it but should still be fenced from the rest of `Private` | `Fenced`, its own passphrase, WPA2 only, `Guest Network` **off**, fenced by an EAP ACL |

A VLAN that needs addresses of its own is created with **Purpose: Interface** and bound to the gateway port the switch is on. The access points tag the SSID's frames themselves, so they arrive on that port already tagged, and the gateway needs a routed interface there to hand out addresses and to have anything to filter.


## Gateway ACL

Settings > Network Security > ACL > Gateway ACL. A `LAN → LAN` rule is stateful when **States Type: Auto**, which makes a deny one-way: a reply to a session the other side opened belongs to that session, so a `Deny A → B` rule never matches B's replies to A. Verified on 2026-09-01 with a deny from a TV-only VLAN to `Private`: REST on the TV kept answering from `Private`.

The fence itself needs no `LAN → LAN` rule at all, because the TV is isolated by its EAP ACL and any VLAN that only needs the internet is isolated by its own `Guest Network` flag. What the Gateway ACL is still good for is `LAN → WAN`, which is the next section.

Two properties of a `LAN → LAN` rule that the ACL screen doesn't state:

* A `! Network [Private]` source denies every VLAN but `Private` in one rule, and needs no editing when a VLAN is added. It's the shape to reach for if a `LAN → LAN` backstop is ever wanted, for instance to contain a VLAN during onboarding while its `Guest Network` flag is off.
* Traffic addressed to the router itself, like the gateway's own web UI, isn't forwarded, so a `LAN → LAN` deny never covers it. Covering it takes a management-access setting on the gateway instead.

To block the TV's own internet access, only `LAN → WAN` can target a single host by IP Group; see the next section.


## Blocking the TV's internet

`LAN → WAN` is the one direction where Source can be an IP Group, so a rule there can target the TV alone by its `/32`. It's all or nothing, though: it costs firmware updates, the Art Store, and SmartThings, which goes through Samsung's cloud, while uploaded art keeps displaying with no internet at all. So it's the heavy step of cutting the TV off from Samsung entirely, worth leaving off unless that's the goal, and it has to be off while anything explores the album-creation idea in `CLAUDE.md`.

**The TV hardcodes its own DNS resolvers, so it has to be allowed to reach DNS anywhere.** A rule that permits port 53 only to a resolver of your choosing takes the TV off the network.

Blocking by domain is the finer instrument, and two published lists name the Samsung ad and telemetry endpoints specifically, so the art, the store, and firmware updates keep working. See https://perflyst.github.io/PiHoleBlocklist/SmartTV.txt and https://github.com/mboutolleau/block-samsung-tv-telemetry. Neither has been tried against this firmware, so treat the split between telemetry and legitimate traffic as their claim rather than a measured one.


## Why it's shaped this way

* **An EAP ACL on a dedicated SSID over a VLAN of its own, for the TV:** the TV refuses control from off its subnet, so a VLAN of its own was ruled out by `spikes.md` T2c. Putting it alone on a `Fenced` SSID on the `Private` subnet makes the SSID a valid Source for an EAP ACL, which then permits only the controlling machine and denies the rest of `Private`. EAP ACLs are stateless, but that's fine here because the only replies the TV owes go to the one permitted machine.

* **A dedicated SSID over leaving the TV on the main one:** Omada's `Guest Network` flag bundles client isolation with an RFC1918 block, which the TV can't have since the controlling machine is on RFC1918, and there is no standalone client-isolation setting. EAP ACLs key on SSID, so the TV needs an SSID no trusted client shares, or the rule would fence them too.

* **Gateway ACL over Switch ACL:** Switch ACLs are per-packet with no connection tracking, so a deny from the TV's VLAN to `Private` there would drop the TV's replies along with everything else. The Gateway ACL is the only stateful layer.

* **Network-to-network rules over a rule for one machine:** `LAN → LAN` rules can only use whole networks, and TP-Link has confirmed IP Groups aren't supported there. Narrowing one of those rules to a single machine isn't possible at the gateway, and everything on `Private` is trusted anyway.

* **Purpose `Interface` over `VLAN`:** `Interface` gives the network a gateway address, DHCP, and routing. `VLAN` is layer 2 only, so there'd be nothing for the ACL to act on.

* **WPA2 and `PMF: Capable` on the TV's SSID:** a hedge for the TV, which has never been seen joining a `WPA2-PSK/WPA3-SAE` mixed SSID. Whether it can is untested rather than known either way; the passphrase bullet under Things That Cost Time To Learn has the state of it.


## Things that cost time to learn

* **The `Guest Network` flag silently kills replies to sessions the private side opened.** `ping` and `curl` from the private network time out rather than being refused, even with a permit rule in place, because the flag drops the guest client's reply on the way back. The Gateway ACL never sees any of it.

* **The implicit permit means a new VLAN reaches everything until told otherwise.** Create the deny rules before joining a device to the new SSID.

* **A new LAN doesn't appear in the ACL Network lists until you log out of the controller and back in.** Reopening the dialog and hard-refreshing the page weren't enough.

* **An unmanaged switch passes tagged frames but can't add tags.** The SSIDs and their VLANs work through it, and every wired device is pinned to the native VLAN. Moving a wired device to another VLAN needs a managed switch, or a spare gateway port added to that network as an untagged member.

* **An IP Group entry for a single host needs a `/32` mask.** The Edit Group form defaults to `/24`, and `<controller-ip>/24` means the whole subnet, not one address. A permit rule built on a group like that permits everything, and the deny below it never matches anything, so the fence silently passes every client on the subnet while looking exactly like an EAP ACL that was never applied.

* **`Prohibit Wi-Fi Sharing` on an SSID isn't client isolation.** It stops clients re-sharing the network through their own hotspot.

* **The TV only talks to clients on its own subnet, and pairing doesn't change that.** From `Private`, the `8002` WebSocket handshake is refused in well under a second with `ms.channel.timeOut` and no prompt, and no TV setting or ACL changes it. A client the TV has already allowed gets the same refusal as one it has never seen, so this isn't a pairing problem that a one-time trip to a shared subnet fixes. REST on `8001` and `8002` answers across VLANs throughout, so `frame status` could read `PowerState` from `Private`, but nothing that needs the WebSocket can run there. `spikes.md` T2c has the numbers.

* **The TV won't join the `Private` SSID, and Omada logs it as a wrong password.** Two things differ between `Private` and the `Fenced` SSID the TV does join: `Private` is `WPA2-PSK/WPA3-SAE` mixed mode where `Fenced` is `WPA2-PSK`, and the two passphrases don't use the same set of symbols. Neither has been isolated. Scattered reports say Samsung TVs choke on `&`, `<`, `>`, and `"` in passphrases, and others say Tizen doesn't reliably join WPA3-SAE. Neither can be settled while the TV sits on a `WPA2-PSK` SSID with a passphrase it accepts. Settling one means deliberately flipping that SSID to mixed mode, or changing the passphrase, and watching whether the TV comes back.

* **A Mac that can reach the gateway but no other host on its own subnet is being blocked by macOS, not Omada.** Local Network privacy under System Settings > Privacy & Security gates each terminal app separately, and a denial looks exactly like AP client isolation: `No route to host`, incomplete ARP, every peer unreachable. Routed traffic to another VLAN is unaffected, which is why it went unnoticed until the Mac joined the TV's own SSID. Check that before touching any wireless setting.

* **The TV never dials out to the controlling machine.** The `8002` WebSocket and the pairing prompt are both inbound to the TV, so one-way rules are enough. Wake-on-LAN is the exception, because the broadcast doesn't cross VLANs. Tested on 2026-09-01 by disabling the deny from the TV's VLAN to `Private` and re-running the `8002` handshake, which behaved identically with the rule off.


## Troubleshooting

* If the TV stops answering after a change, first check whether it's in Art Mode proper; the art channel connects but never sends `ms.channel.ready` from any other screen, including the select-art screen, and a long hammering of connections can leave the Art app stuck there until a reboot. Next check whether `Guest Network` got turned on for its SSID, whose symptom is a timeout not a refusal, and whether the controlling machine's IP changed, which needs a fresh pairing prompt.

* If the TV answers `ping` but a `frame` command can't connect, suspect a firmware update before the network. Updates have reset Access Notification and invalidated the token; `CLAUDE.md` has the re-pairing steps.

* To confirm the EAP fence, load `http://<tv-ip>:8001/api/v2/` from a device not in the fence IP Group; it should hang, not return JSON. A `/32` mask on the IP Group entries matters here: a `/24` is the whole subnet and silently permits everything, so the deny never matches and the fence looks broken.

* If a fenced client can load the gateway's web UI at its gateway address, no `LAN → LAN` or EAP deny stops it, because traffic addressed to the router itself isn't forwarded. Closing it takes a management-access setting on the gateway.

* If something on another VLAN needs to be found by discovery from `Private`, it won't be. mDNS and SSDP don't cross VLANs, so use the address directly. The TV is on the `Private` subnet precisely so it doesn't have this problem, but the EAP deny still blocks multicast discovery of it from non-permitted clients, so address it directly too.


## The working design

The TV lives on its own `Fenced` SSID on the `Private` VLAN, fenced by an EAP ACL, and the controlling machine on `Private` drives it. Giving the TV a VLAN of its own is the design to reach for first, and T2c rules it out: the TV refuses any control connection from off its own subnet, paired or not, so it can't sit on a VLAN of its own while the controlling machine stays on `Private`. `spikes.md` T2c has that finding, along with three same-subnet failure modes that each look like the TV being unreachable. The other options weighed, a controller on the TV's VLAN, the controlling machine hopping VLANs, and source NAT at the gateway, are recorded there too; the `Fenced` SSID won because it keeps the TV off a shared broadcast domain with the trusted machines while leaving both on the `Private` subnet.

The pieces:

* **`Fenced` SSID**, `WPA2-PSK` only, `Guest Network` off, the TV alone on it at `<tv-ip>` by DHCP reservation, which holds the address across a reboot.
* **EAP ACL fence**, under Settings > Network Security > ACL > EAP ACL:

  | # | Policy | Source | Destination | Protocols |
  |---|---|---|---|---|
  | 1 | Permit | SSID `Fenced` | IP Group `Reachable from Fenced SSID` | All |
  | 2 | Deny | SSID `Fenced` | Every LAN on the gateway, `Private` included | All |

  Order matters, because rule 1 is what carves the two permitted hosts out of rule 2's deny. The IP Group holds `<controller-ip>/32` and `<gateway-ip>/32`, each a `/32`, because a `/24` there is the whole subnet and silently permits every client on it. If the EAP rule form has no Direction field, the fence works without one: the controlling machine in the group reaches the art channel, and a client outside it hangs on the TV's REST.
* **Both channels paired** from the controlling machine, through the fence. Device List holds one entry for the two channels, keyed on name plus address, so an address change on the controlling machine costs one more prompt.
* **No Gateway ACL rule**, because a VLAN that only needs the internet carries the `Guest Network` flag and the AP blocks RFC1918 for those clients itself. Anything on `LAN → WAN` is a separate choice, and Blocking The TV's Internet above has it.
* **UPnP disabled** on the gateway.
* **A backup of the Omada config**, because none of the above is reproducible from memory.

The fence is a whitelist, so any other client that has to reach the TV needs a fixed address and a `/32` entry of its own in the IP Group. A phone driving the TV through SmartThings is the case that comes up.

Moving the controlling machine from Wi-Fi to Ethernet means moving its DHCP reservation to the Ethernet MAC and keeping the address, so the TV's Device List entry stays valid. Expect one more pairing prompt on the first connection from the new interface anyway.

Two clients on separate `EAP610`s reach each other with no extra configuration, which is what the TV and the machine driving it rely on. AP-to-AP isolation never turned out to be a factor, because macOS Local Network privacy accounted for every symptom that resembled it.
