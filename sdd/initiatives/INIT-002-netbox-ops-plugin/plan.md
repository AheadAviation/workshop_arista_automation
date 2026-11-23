# Here is what I plan to do

## What I'll build

**One page inside NetBox that runs the whole demo.** Not a separate app — a NetBox plugin, so
it looks and behaves like part of NetBox. Brian Nelson asked for this: "a plugin to manage it
all, load in devices, check the container labs, show the awx, show the itsm ticket, all of the
day two opps."

**A devices view.** Every switch, what NetBox says it should be, what it actually is, and the
difference. This is the workbench's fabric view, moved inside NetBox where the audience already
is.

**A lab control page.** The running containerlab topology, live: which nodes are up, which are
down, and buttons to deploy or destroy. "Check the container labs" — Brian Nelson's words.

**The day-two change, end to end.** Pick a switch and a VLAN. Preview shows exactly what would
change and nothing is sent. Opening a ticket. A named human approves in the ticket. The change
pushes. The device is checked. The audit record closes.

**A real AWX.** Containerised, with its own database and a bootstrap that creates the
credentials, inventory and job templates. The plugin shows the job templates and the last run,
and can launch a job. He said "show the awx" and chose real containers over a mock.

**A real Zammad.** Containerised, with its own database, bootstrapped with an admin, an API
token and a group. The plugin shows the ticket that a change opened, links to it, and takes the
approval from it. He chose real containers here too.

**A lifecycle view.** Compliance and operating system per device, read from the devices. The
CVE and upgrade conversation, as a picture instead of a spreadsheet.

**An audit trail that lives in NetBox.** Every change, who approved it, what the device looked
like before and after — as records you can filter and query like anything else in NetBox, not a
file.

## What I'll reuse

The whole engine I already built and verified on live switches: the drift comparison, the EOS
executor that previews without applying, the approval gate that refuses a change with no named
approver, and the audit record. Roughly three thousand lines with five hundred tests behind it.
The plugin becomes a new face on that, not a second implementation.

The existing ZTP plugin's structure, since it is already a working NetBox plugin in this repo
on this NetBox version.

The container images I have already pulled and confirmed will run: AWX 24.6.1, the AWX
execution environment, and Zammad.

## How I'll check it works

Unit tests for every piece of logic, with fake devices and fake AWX and Zammad, so none of it
needs the real stacks to be verified.

A live check that walks the whole flow against the real switches, the real AWX and the real
Zammad, the way I did for the workbench.

And a rehearsal script that tells you before you present whether all of it is actually up.

## What I won't do

No replacing the workbench. The workbench keeps its own page and its tests; this plugin is a
second surface on the same engine, and if it turns out to be the better surface we retire the
other one deliberately rather than by accident.

No mounting the Docker socket into NetBox. Reading the switches needs Docker access, so it goes
through a scoped proxy that can only do the few things we need. A hospital's CISO should not see
NetBox holding the keys to the host.

No rewriting the NetBox fabric data, and no touching the ZTP portal.

No pretending AWX and Zammad are fast. Both take minutes to come up the first time, and the
plugin will say "starting" rather than showing an empty screen.

## What you'll have at the end

A NetBox tab that shows your devices, your labs, your changes, your AWX jobs, your tickets and
your audit trail — one place, and it is the same place the customer already trusts as the source
of truth.

## The honest part

This is a lot. Two full application stacks, a plugin with its own database models, an API, a
user interface, and two integrations. That is genuinely more than one sitting, and I would
rather tell you that now than produce a half-working thing quietly.

So I will build it in waves, in the order that gets something visible soonest:

1. The plugin skeleton and the engine inside it — something in NetBox that works.
2. Lab control — you can see and drive your containers.
3. The day-two flow end to end, with the audit trail.
4. AWX, then Zammad, with their integrations.
5. Lifecycle, then the rehearsal and the runbook.

After wave three you have a demo. Waves four and five make it the demo you described.

---

Shall I go ahead and build this?

1. Yes, build it
2. Change something first
3. Not now


---

**Reply recorded:** "Yes, build it" — Brian Nelson, 2026-11-23.
**Pacing answer recorded:** "Build all five waves straight through" — Brian Nelson, 2026-11-23.

*(Recorded by hand: the kit's `plan_approval.py` helper is not installed on this seat.)*
