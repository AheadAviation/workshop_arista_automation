# Here is what I plan to do

## What I'll build

**A "where does this jack go?" tab inside the Network Ops plugin.** Someone types a room and a
jack number — the way a helpdesk actually gets the request — and NetBox answers with the switch
and the port, or says precisely why it cannot. This comes from the customer's own TWIX tool,
which resolved building/room/jack to a switch port so a non-network person never had to know a
switch name.

**The physical layer NetBox is currently missing.** Patch panels as devices, with rear ports
punched down to the switch and front ports as the jacks people plug into, plus the cable run
between them. Right now this repo models the fabric uplinks and nothing else — there are no
locations, no racks and no panels in it, so "where is jack 228" has no way to be answered.

**A resolver that walks the cabling.** Front port, through its rear port, along the cable, to the
switch interface — and if the run goes through a second panel, on through that one too. It
returns one switch port, or it refuses and says which of the four things went wrong. That refusal
is the part worth copying: TWIX would not guess, it printed "No unique port found, no action will
be performed."

**A hand-off from the jack to the existing change flow.** Once a jack resolves to a port, the
page offers to stage the change on that port — which uses the change flow that already exists,
approval and audit included. The jack does not become a second way to push config.

**The seeded example, so the demo has something real to point at.** Patch panels and jacks for
the sites already in the sample data, wired to the interfaces that exist.

**Documentation and a second way in.** The Day-2 page gets the jack-based path next to the
switch-based one, so the two are visibly the same operation.

## What I'll reuse

The plugin's own page pattern — the tab strip, the named route per tab, and the two permission
mixins that make reading a device a different right from changing one. This work adds a tab to
that, not a parallel page.

The change flow, unchanged: stage, preview, ticket, approve, push, diff, verify. A jack resolves
to a port, and from there it is the existing flow. Nothing in the approval gate moves.

The device-type and cabling helpers in `scripts/plants_demo` — they already create device types,
expand an interface list into named ports, and create cables with two terminations. The panel
seeding uses the same helpers rather than a second way to write the same objects.

The plugin test pattern that loads the plugin's modules with a stubbed NetBox package, so the
resolver can be tested without a running NetBox.

`netbox_topology_views`, which is already installed, to show the traced path as a picture.

## How I'll check it works

A test per refusal: a jack that does not exist, a jack whose panel port has no cable, a jack
wired to two switch ports, and a jack wired to network equipment rather than a desk. Each must
produce its own distinct message, because in TWIX the good behaviour and the failing behaviour
both printed something and only one of them was safe.

A test that a two-panel run resolves all the way through to the far switch port, so the walk is
proven to follow a real path rather than one hop.

A test that the new tab refuses an anonymous visitor and a signed-in user without the device
permission, the same check the rest of the plugin already passes.

A live check against the running NetBox and the real switches: seed the panels, look up a jack on
the page, confirm it names the switch port that NetBox's own cable trace names for that jack.

## What I won't do

No new database table and no migration. The lookup reads NetBox's own objects, so it needs
nothing of its own to store — and the plugin currently has no models at all, so a table here
would be its first migration for no gain.

No work queue, no scheduling and no retry loop. TWIX's queue is the strongest idea in that tool
and it is a separate piece of work; bolting it onto this change would make one plan that does two
unrelated things.

No changing the approval gate, the audit record or the ITSM integration. The jack path ends at
the port, and the port goes through the gate that already exists.

No guessing when a jack is ambiguous. If the answer is not exactly one switch port, the page says
so and offers nothing to click.

No editing the fabric data or the ZTP portal.

## What you'll have at the end

A tab in the Network Ops plugin where a room and a jack number become a switch and a port, with
the cable path shown on the way — or a plain sentence explaining why it could not resolve. And
the demo can say the thing the customer's own tool said for nine years: you do not need to know
which switch you are on.

---

Shall I go ahead and build this?

1. Yes, build it
2. Change something first
3. Not now


---

## Facts this plan is built on (checked, not assumed)

| Fact | How established |
| --- | --- |
| NetBox is **4.1.11 Community** | `nb.status()` against the running instance |
| Front and rear ports exist as first-class objects | `GET /api/dcim/front-ports/`, `/api/dcim/rear-ports/` → 200 |
| Port templates exist, so one panel device type covers a 24-port panel | `GET /api/dcim/front-port-templates/`, `/api/dcim/rear-port-templates/` → 200 |
| A cable may terminate on an interface **and** a front/rear port | `/opt/netbox/netbox/dcim/constants.py` → `CABLE_TERMINATION_MODELS` lists `frontport`, `rearport`, `interface` |
| A patch-panel rear port can cable straight to a switch interface | same file → `COMPATIBLE_TERMINATION_TYPES['interface']` includes `frontport`, `rearport` |
| Required fields are small | `OPTIONS`: FrontPort = `device,name,type,rear_port`; RearPort = `device,name,type` |
| NetBox's own trace endpoint works from a port | `GET /api/dcim/interfaces/{id}/trace/` → 200 |
| The plugin has **no** `models.py` and **no** `migrations/` | file listing of `plugins/netbox_ops/` |
| There are no locations, racks or panels in this NetBox today | `GET` counts: 0 locations, 0 racks, 1 device type, 6 devices, 12 cables (all fabric uplinks) |
| `netbox_topology_views` is installed | `nb.status()['plugins']` |

**Prior art: NOT RUN, disclosed gap.** The kit helper `scripts/dev/prior_art.py` is not installed
on this seat (`C:\Users\BrianNelson\.claude\scripts\dev\` does not exist) — the same gap INIT-002
recorded. What was done instead is the manual survey in "What I'll reuse". What this does not
prove: that no third-party NetBox plugin already resolves a patch-panel jack to a port.

**Plan approval not machine-recorded.** `plan_approval.py` is likewise absent, so the reply is
recorded by hand below, as INIT-002 did.

## The design (drawn first, before any code)

**Figma:** https://www.figma.com/design/JhU479tVCJyK3i1otCMqby

Five screens, one per outcome — the resolved answer plus the four refusals — because the design
question here is not "how does a lookup form look" but **"how do five different answers stay
visibly different to an operator at 2am"**. Drawn against NetBox's own tokens rather than a
generic kit: the colour variables are read from the running instance's `netbox.css` (Tabler's
dark theme — `#040A11` body, `#182433` surface, `#00857D` brand teal), and the type is **Inter**,
which is what NetBox actually ships. No design-system library in the file matched NetBox's
Bootstrap/Tabler language, so the components are local and token-bound.

**What the design settled that the prose had not:**

| Decision | Why it is that way |
| --- | --- |
| Five states, not two | "Cannot resolve" hid four different problems with four different fixes. A grey "not found" is the operator's typo; a blue "not cabled" is a data-quality gap; an amber "two ports claim it" is a cabling error that must be fixed before any change; a red "feeds network equipment" is a refusal by policy. |
| State is carried by colour **and** icon **and** a 3px accent bar | Colour alone fails for a colour-blind operator and in a screenshot pasted into a ticket. |
| The path is a table, not prose | Three rows — front port, rear port, switch port — so the reader can see *where* the trace stopped. A sentence cannot show a break in the middle. |
| The action button disappears on refusal | TWIX's `output_failure` still rendered the page chrome; here there is nothing to click unless there is exactly one port. The refusal is the design. |
| Site/Building/Room/Jack, four fields | TWIX needed building + comm room + jack and resolved on a fuzzy building name. Making the site explicit removes the ambiguity that made its `findPort` return more than one row. |
| No VLAN field | TWIX commented its VLAN input out and hard-set `vlan = 0`, with the note "For now we can no change the vlan number". It had learned that letting the requester name a VLAN was the bug. |

Components in the file: `Answer banner` (5 variants), `Path row`, `Tab` (2 variants), `Field`.
24 colour variables, all bound — no hex literals in the screens.

## The pieces

Six specs, in this order. The first is the decision; the last is the write-up.

| Spec | Domain | Title | Complexity | Security | Blocked by |
| --- | --- | --- | --- | --- | --- |
| SPEC-001 | docs | The resolution contract and the data-model decisions | medium | light | — |
| SPEC-002 | data | Patch panels, rooms and the horizontal cabling | high | standard | SPEC-001 |
| SPEC-003 | service | The resolver: jack to one switch port, or a named refusal | high | required | SPEC-002 |
| SPEC-004 | api | The Jack lookup tab and its change hand-off | medium | required | SPEC-003 |
| SPEC-005 | frontend | The path table and the four refusal states | medium | light | SPEC-004 |
| SPEC-006 | docs | Day-2 page and demo act | low | light | SPEC-005 |

**Dependency edges:** SPEC-001 → SPEC-002 → SPEC-003 → SPEC-004 → SPEC-005 → SPEC-006

What each one settles:

**SPEC-001 — the decision, written down.** What a "room" is in NetBox terms (a Location under the
site, with the building as its parent), which end of a panel is the jack, how a jack is named so
two panels in one room cannot collide, and the four refusal reasons with their exact wording. It
also records why this adds no table.

**SPEC-002 — the data.** Panel device types carrying 24 front and 24 rear port templates; the
room locations; the panels themselves; and the horizontal run from each rear port to the switch
interface it is punched down to. Idempotent, and able to remove what it created.

**SPEC-003 — the resolver.** One function: site, building, room, jack in; either one switch
interface and the path it took, or a refusal from a closed set. Cascaded panels handled. No
NetBox writes, so it can be tested with nothing running.

**SPEC-004 — the tab.** A named route and a read-only view using the plugin's existing
`ReadOnlyOperatorMixin`, the resolution rendered on the page, and an action that stages a change
on the resolved port through `change_flow`.

**SPEC-005 — the page.** The path as a table — jack, panel, rear port, cable, switch, interface —
and the four refusals rendered as four visibly different states rather than one empty result.
Built to the Figma file, whose five screens are the acceptance reference for this spec.

**SPEC-006 — the write-up.** A jack-based section in `wiki/Day-2-Operations.md` beside the
switch-based one, and the demo act in the workshop script.

**Deletes:** nothing. This is additive. One thing does become reachable two ways — moving a port's
VLAN, today only via the switch-and-port path — and SPEC-006 says so on the page rather than
leaving two silently divergent routes.
