# Here is what I plan to do

## What I'll build

**The page Brian Nelson demos.** One localhost screen that puts NetBox's intended state next to what the
switches actually are, and shows exactly where they differ. Brian Nelson asked for this, because he wants a
GUI front end and to stay away from the CLI in front of this audience.

**The change engine behind it.** It works out the precise change needed, shows the difference without touching
a switch, and applies it only after you click approve. Brian Bradley asked for this with the Philips vendor
story: a new system arrives, needs a VLAN, and that VLAN has to land on a series of trunks.

**The VLAN-across-trunks story, working end to end.** One action adds a VLAN to every trunk in the fabric, and
the page proves it landed on each switch. This is the exact real-world change Brian Bradley described, and he
says it is the reason this demo matters to them.

**The lifecycle picture.** Which devices match intent, which have drifted, and what operating system each is
running. Brian Wright said the CVE and upgrade problem is a big pain point today because they cannot find
outages to patch, and that ten-year-old gear has known vulnerabilities they cannot see. This turns that into
a picture instead of a spreadsheet.

**One intent, two vendors.** The same intent and the same approval gate driving an Arista switch and a FRR
router, so the vendor label on the box stops mattering. Brian Wright asked for this on behalf of their CTO,
who does not care what vendor the switch is if automation works across the board.

**Who approved what.** A record of every change, who approved it and what the switch looked like before and
after. Chris Veith flagged that the CISO will be in the room and that audits keep asking for network
documentation.

**A reset button and a rehearsal checklist.** So the demo can be run twice, and so it can be checked in about
ten minutes on the morning of the meeting.

## What I'll reuse

NetBox as the source of truth, which is already where this fabric's intent lives. The config render path that
already produces all six switch configurations — I confirmed it renders and the six files are current. The
existing technique that previews a change on a switch without applying it. The running cEOS fabric and the
dual-DC FRR track already in this repo, so no new images and no new containers. The add-on pattern and test
style from the plants demo, which keeps an optional demo from disturbing the core workshop. The 206 passing
tests as the baseline I must not break.

## How I'll check it works

Unit tests with fake devices for all the logic, so none of it needs the lab running. A scripted end-to-end run
against the live lab that breaks a switch on purpose, watches the page catch it, approves the fix and confirms
the switch came back. Plus the rehearsal checklist, which is the check that actually matters the night before.

## What I won't do

No AWX, no Zammad, no InfluxDB or Grafana stack, no new container images, and no licensed Junos images — those
were on the table from the source projects and they cost more time than they return for a 90-minute executive
meeting. No new IPAM, because Chris Veith told us they use Infoblox and cannot be asked to replace it. No
ServiceNow replacement, because Brian Nelson was explicit that we do not want to look like we are competing
with their CMDB. No rewrites of the fabric data the current workshop depends on. And nothing that pushes a
configuration without an approval click.

## What you'll have at the end

A localhost page that tells the whole story in about fifteen minutes: here is what the network should be,
here is what it is, here is the drift, here is the change, here is who approved it, and here is the switch
proving it took. With a reset button and an audit trail behind it, and safe to run in front of a CIO, a CISO
and a CTO without ever opening a terminal.

One thing to be straight about: a demo is tomorrow. The page, the change engine, the VLAN story and the audit
trail are the demo and I would build those first. The lifecycle picture and the two-vendor proof are the
"also mention" pieces, and I will build them lighter and later if the clock runs short.

---

Shall I go ahead and build this?

1. Yes, build it
2. Change something first
3. Not now

---

**Reply recorded:** "Yes, build it" — Brian Nelson, 2026-11-23.
**Sequencing answer recorded:** "Demo-first: page + VLAN story + approval gate tomorrow-ready, lifecycle/vendor
proof after" — Brian Nelson, 2026-11-23.

*(Recorded by hand: the kit's `plan_approval.py` helper is not installed on this seat — see the initiative
document's Deviations note. The reply above is the actual answer given to the approval question.)*
