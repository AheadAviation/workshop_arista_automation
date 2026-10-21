# Workshop 2 Walkthrough

The hands-on path for the ODL follow-up. Set the lab up, then run the six acts in order. Follow this page top to bottom.

For a Copy button on every command, open [Workshop-2-Walkthrough.html](Workshop-2-Walkthrough.html) in a browser. The preview in Cursor cannot copy to the clipboard.

**What you will have at the end of setup:**

- NetBox at http://localhost:8888 with the sample manufacturer loaded (11 sites, owners, approvers, circuits)
- Six cEOS switches running, matching NetBox, with a passing test
- No open changes, no open tickets, and one estate report saved as the baseline

**Time:** about 15 minutes to set up when Docker is already up, then about 50 minutes to run the acts.

**Run every command below from the checkout whose Compose project is already up.** On this machine that is the lab checkout, not a second empty copy of the same repo.

```powershell
cd C:\Users\BrianNelson\Projects\netbox-arista-lab
```

The talk track, quotes, and what to ask them live in `wiki\Workshop-2-Demo-Script.md` in that same folder. This page is the commands.

---

## Prerequisites

- Docker Desktop running
- The cEOS image imported once: `ceos:4.32.0F` (see [[Quick Start]])
- The lab repo cloned at `C:\Users\BrianNelson\Projects\netbox-arista-lab`

Each `.\run.ps1` command takes 15–20 seconds to start, because it runs inside a container. Talk while it starts.

---

## Step 1 — Check NetBox and the switches

```powershell
.\run.ps1 status
docker ps --filter name=clab-netbox-arista-lab
```

NetBox, Postgres, and Redis should say `healthy`. You want six containers: `spine-01`, `spine-02`, `leaf-01` through `leaf-04`.

If NetBox is not running:

```powershell
.\run.ps1 up
```

Wait until `.\run.ps1 status` shows NetBox healthy. Open http://localhost:8888 and sign in as `admin` / `admin`.

If the six switches are not running:

```powershell
.\run.ps1 deploy
```

That takes about 6 minutes and pushes the configs as well.

---

## Step 2 — Rebuild the sample organisation

This deletes the sample manufacturer and creates it again. It does not touch the Arista fabric.

```powershell
.\run.ps1 plants-remove --yes
.\run.ps1 plants
```

`plants-remove` lists what it deleted (sites, devices, circuits, contacts) and ends with `0 errors`.

`plants` ends with **Plants Demo add-on ready**. You should see 11 sites created, then 13 saved filters. A second `plants` without a remove is safe: it reports `0 created`.

---

## Step 3 — Write what the dashboard observed

```powershell
.\run.ps1 plants-meraki --apply
```

The source line must say **MOCK Meraki API**. It writes firmware, serials, and online/offline into NetBox, and adds a few devices NetBox did not have. Run this after every `plants`. Without it, the later acts do not match the script.

---

## Step 4 — Clear leftover demo state

```powershell
.\run.ps1 plants-change reset --yes
.\run.ps1 plants-alert reset --yes
.\run.ps1 plants-scan all --forget
```

You want `0 change record(s)`, `0 incident(s)`, and `Deleted 0 address(es)` (or however many a previous scan had recorded). After this there is nothing open.

---

## Step 5 — Baseline, then prove the fabric

```powershell
.\run.ps1 plants-report
.\run.ps1 day2 status
.\run.ps1 diff
.\run.ps1 test
.\run.ps1 backup
```

| Command | What you need to see |
|---------|----------------------|
| `plants-report` | One row per site, and a file `output\reports\estate-*.md`. Act 5 compares against this file. |
| `day2 status` | `Applied: none` |
| `diff` | `The switches already match NetBox.` |
| `test` | `PASS: all 36 checks passed` |
| `backup` | `flash:backup.cfg` on all six switches |

`diff` and `test` print Ansible warnings about `ansible.posix` and paramiko. Those are noise. The verdict line is the one that matters.

Setup is done. Leave this window open.

---

## Step 6 — Act 1: One record

In the browser, open **Devices**, then the saved filters **Plants: Critical devices**, **Plants: Old operating systems**, and **Plants: Juniper and Ubiquiti**. Open one device and show owner, approver, change window, and the **Changelog** tab. Then open **Circuits**.

```powershell
.\run.ps1 plants-import
.\run.ps1 plants-meraki
.\run.ps1 plants-scan us-plt2
.\run.ps1 plants-audit
.\run.ps1 plants-impact us-hq-sw02
```

`plants-import` and `plants-meraki` write nothing. The scan is a mock: Greenville has three addresses NetBox has never heard of, two of them on the OT network. `plants-impact` names the owner, the approvers, the window, and what else goes down with that switch.

Say this: the spreadsheet and the dashboard load in once. NinjaOne stays the source for PCs.

---

## Step 7 — Act 2: A change, then the test

Say this before the first command: scheduling the firmware stays a click in Meraki. This is the plan and the test around that click.

```powershell
.\run.ps1 plants-change plan us-hq-sw02 --summary "Switch firmware" --at "2026-10-25 14:00"
```

Copy the `CHG-...` id it prints. Use it in place of `CHG` below. The plan names four approvers, and 14:00 is outside the Sunday window.

```powershell
.\run.ps1 plants-change apply   CHG --as-of "2026-10-25 02:00"
.\run.ps1 plants-change approve CHG --by "Dana Ortiz"
.\run.ps1 plants-change approve CHG --by "Priya Nair"
.\run.ps1 plants-change approve CHG --by "Carla Mendez"
.\run.ps1 plants-change approve CHG --by "Elena Petrova"
.\run.ps1 plants-change apply   CHG --as-of "2026-10-25 02:00" --simulate-no-clients us-hq-ap03
.\run.ps1 plants-change verify  CHG
```

The first `apply` is refused because nobody has approved. `verify` is a **FAIL** on purpose: `us-hq-ap03` had 7 wireless clients and now has none.

```powershell
.\run.ps1 plants-change fault us-hq-ap03 --no-clients --clear
.\run.ps1 plants-change verify  CHG
.\run.ps1 plants-change confirm CHG --by "Nora Fields" --note "DCs and file shares fine"
.\run.ps1 plants-change confirm CHG --by "Raj Patel"
.\run.ps1 plants-change confirm CHG --by "Elena Petrova"
.\run.ps1 plants-change confirm CHG --by "Carla Mendez"
.\run.ps1 plants-change verify  CHG
.\run.ps1 plants-change show    CHG --evidence
```

One device stays unconfirmed: `us-hq-legacy01` has no owner. Show the evidence file under `output\changes\`.

---

## Step 8 — Act 3: Level 1, stopped by permissions

```powershell
.\run.ps1 plants-triage us-plt3-ap02
.\run.ps1 plants-l1 find 10.2.10.11
.\run.ps1 plants-l1 --as demo.helpdesk dhcp us-plt2
.\run.ps1 plants-l1 --as demo.helpdesk dhcp us-hq
.\run.ps1 plants-l1 --as demo.helpdesk reserve-ip us-plt2 100 --for "New label printer"
.\run.ps1 plants-l1 --as demo.helpdesk port-vlan us-plt2-sw02 12 100 --label "Time clock"
.\run.ps1 plants-l1 --as demo.netops port-vlan us-plt2-sw02 12 100 --label "Time clock"
.\run.ps1 plants-l1 --as demo.netops port-vlan mx-plt1-sw02 3 100
```

Greenville VLAN 100 is 98% full. The help desk login is refused the port change. The same command as `demo.netops` is allowed. The Juniper command records the intent in NetBox and says nothing is pushed to the switch.

The browser passwords, if you sign in as those users, are `PlantsDemo-2026!`. This tool cannot clear DHCP leases. The runbook escalates that.

---

## Step 9 — Act 4: An alert becomes a ticket

Copy each `INC-...` from the command that created it.

```powershell
.\run.ps1 plants-alert simulate us-hq-ap03
.\run.ps1 plants-alert resolve INC --fix "PoE had tripped on us-hq-sw03 Port 1. Power-cycled the port; AP back in 3 minutes." --by "Sam Whitaker"
.\run.ps1 plants-alert simulate us-hq-ap03
```

The second ticket has a **Last fix** row, and it points at the change from Act 2. Say this: the text is ready for their ticketing tool. Sending it is one connection, once they choose the tool.

```powershell
.\run.ps1 plants-alert simulate us-plt3-fw01 --type uplink
.\run.ps1 plants-alert poll
.\run.ps1 plants-alert poll --simulate-down mx-plt1-sw01
.\run.ps1 plants-alert list
```

The uplink ticket names the circuit, the provider number, the account, and the SLA. `poll` opens tickets for the gear Meraki cannot see. The second poll does not open a second ticket for one that is already open. The checks are mocks.

---

## Step 10 — Act 5: The fleet, and the OT list

```powershell
.\run.ps1 plants-report
.\run.ps1 plants-ot
.\run.ps1 plants-ot us-plt2
```

The report's totals show the change since the baseline from Step 5. `plants-ot` is one line per plant. Greenville has items to fix, including the unknown OT devices from Act 1. Say this: the report reads the record. It is not a monitoring product.

---

## Step 11 — Act 6: The same idea on the real switches

Say this first: their network is Meraki, so this act is the pattern for gear we configure, such as Juniper. It is not a proposal to replace Meraki.

```powershell
.\run.ps1 day2 apply add-vlan
.\run.ps1 generate
.\run.ps1 diff
.\run.ps1 push
.\run.ps1 test
```

`diff` shows the VLAN on the four leaves and nothing else. `test` passes all 36 checks.

If there is time, show a failed test and the fix:

```powershell
.\run.ps1 day2 apply bad-asn
.\run.ps1 generate
.\run.ps1 push
.\run.ps1 test
.\run.ps1 day2 revert bad-asn
.\run.ps1 generate
.\run.ps1 push
.\run.ps1 test
```

The first `test` fails. The second passes. If time is short, stop after the first `test`.

---

## Step 12 — Put the lab back

Run this after the call, so the next run starts clean. It does not delete the sample organisation.

```powershell
.\run.ps1 plants-alert reset --yes
.\run.ps1 plants-change reset --yes
.\run.ps1 plants-scan all --forget
.\run.ps1 day2 revert all
.\run.ps1 generate
.\run.ps1 push
.\run.ps1 test
```

To remove the sample organisation itself, later:

```powershell
.\run.ps1 plants-remove --yes
```

Add `--purge-model` only when you also want the device types, custom fields, and demo users gone. The Arista fabric stays either way.

---

## If a command fails

| What you see | What to do |
|--------------|------------|
| `.\run.ps1 plants` prints the usage screen | You are in `workshop_arista_automation`. `cd` to the lab repo in Step 1. |
| `Not approved yet` | Approve all four names from the plan. |
| `outside the window` | Add `--as-of "2026-10-25 02:00"` on `apply`. |
| `verify` still fails after `--clear` | The clear was the wrong fault. Use `fault us-hq-ap03 --clear` or `--no-clients --clear`. |
| `poll` opens no circuit or DHCP ticket | One is still open. `.\run.ps1 plants-alert reset --yes` and start Act 4 again. |
| Statuses look wrong after `plants` | Run `.\run.ps1 plants-meraki --apply` again. |
| `test` fails before Act 6 | `.\run.ps1 diff`, then `.\run.ps1 push`, then wait 30 seconds and run `test` again. |
| A command hangs on start | `docker compose ps`. NetBox must be healthy. |

---

**Back to:** [[Quick Start]] | [[Day 2 Operations]]
