# Add-on: Plants Demo

An **optional** module that layers a multi-site manufacturer on top of the core Arista fabric lab: plants and distribution centers, Meraki-centric with some Juniper and Ubiquiti, with **owners, approvers and change windows** recorded in NetBox.

It answers a different question than the core workshop. The fabric lab shows *"NetBox drives device configuration."* This add-on shows *"NetBox is the CMDB: who owns this, who approves a change to it, and what is out of date?"*

> The core lab works exactly as before whether or not this add-on is installed. Skip it, install it, or remove it at any time.

---

## Quick Start

With NetBox running and the core data imported (`docker compose run --rm ansible python scripts/full_import.py`):

```powershell
.\run.ps1 plants                  # create the sample organisation (about 1 minute)
.\run.ps1 plants-audit            # ownership and lifecycle report
```

```bash
./run.sh plants
./run.sh plants-audit
```

Re-running is safe: it only changes what differs (`0 created, 0 updated` on a second run).

Optional extras:

| Command | What it does |
|---------|--------------|
| `plants --link-fabric` | Also assigns owners, approver and a change window to the core `DC1` fabric site |
| `plants-audit --scope all` | Audit everything in NetBox, including the Arista fabric |
| `plants-audit --json` | Machine-readable output |
| `plants-audit --fail-on-gaps` | Exit code 1 if any device has no owner (usable as a CI gate) |
| `plants-import` | Validate a spreadsheet (CSV) asset register. Writes nothing. See [Importing a Spreadsheet](#importing-a-spreadsheet) |
| `plants-import --apply` | Import the rows that passed validation |
| `plants-meraki` | Compare a **mock** Meraki dashboard with NetBox. Writes nothing. See [Meraki Sync](#meraki-sync-mock-dashboard) |
| `plants-meraki --apply` | Write what the dashboard observed (serials, firmware, online/offline) and add devices NetBox lacks |
| `plants-meraki-mock` | Run the mock Meraki API on its own at `http://localhost:8081/api/v1` (for `curl` or `--base-url`) |
| `plants-views` | Create the saved filters in the NetBox UI (`plants` already does this). See [Showing Them What Helps](#showing-them-what-helps) |
| `plants-impact <device>` | Before a change: owner, approver, change window, what is downstream, who to tell, rollout order |
| `plants-triage <device>` | Level-1 card for a device that is down: where it plugs in, likely cause, who to call, steps |
| `plants-change <plan\|approve\|apply\|verify\|confirm\|...>` | A firmware change with approvers, a window, a baseline and evidence. See [Change Workflow](#change-workflow-mock-dashboard) |
| `plants-l1 <find\|reserve-ip\|port-vlan\|dhcp\|reset>` | Level 1/2 runbook actions, as a demo user. See [Level-1 and Level-2 Actions](#level-1-and-level-2-actions) |
| `plants-alert <simulate\|handle\|list\|reset>` | A monitoring alert in, a ticket with NetBox context out. See [Alerts to Tickets](#alerts-to-tickets-mock-dashboard) |
| `plants-alert poll` | One monitoring pass: Juniper and Ubiquiti gear answers, circuits under 80%, DHCP scopes not full. See [Monitoring the Gear Meraki Cannot See](#monitoring-the-gear-meraki-cannot-see-mock-poller) |
| `plants-alert resolve <id> --fix "..."` | Close a ticket saying what fixed it; the next alert of the same kind quotes it. See [Known Fixes](#known-fixes) |
| `plants-alert show <id>` | One ticket's card again |
| `plants-report` | The weekly estate report: every site on one screen, with what changed since last week. See [Estate Report](#estate-report) |
| `plants-ot [site]` | OT segmentation readiness for each plant: what blocks it and the draft firewall flows. See [OT Readiness](#ot-segmentation-readiness) |
| `plants-scan <site\|all>` | Compare what answers on the network with NetBox (**mock** scan). `--apply` records the unknowns, `--forget` removes them. See [Network Scan](#network-scan-mock) |
| `plants-remove` | Dry run: shows what would be deleted |
| `plants-remove --yes` | Delete the sample data |
| `plants-remove --yes --purge-model` | Also delete the reference data (device types, custom fields, demo users...) |

---

## What It Adds

| Object | Count | Notes |
|--------|-------|-------|
| Sites | 11 | 8 plants, 3 distribution centers across US, Canada, Mexico, UK, China, New Zealand |
| Devices | 85 | 59 Meraki (MX/MS/MR), 3 Juniper, 1 Ubiquiti, 11 servers, 11 OT workstations |
| Cables | 74 | Firewall to core switch to downstream switches to APs, servers and machines |
| VLANs and prefixes | 53 each | `10.<site-index>.<vlan>.0/24`, including a per-plant OT VLAN |
| Circuits and providers | 13 and 8 | One primary internet circuit per site, plus two backups. Each provider records its NOC number, account and SLA |
| Contacts | 18 | Roles: Owner, Approver, Change Window Contact, Escalation |
| Custom fields | 10 | criticality, change window, firmware canary, OS end-of-life date, Meraki IDs, firmware version, Depends On (which server a machine needs)... |
| Saved filters | 13 | One-click answers in the NetBox UI, such as old operating systems or OT networks |
| Demo users | 3 | See [Demo accounts](#demo-accounts) |

All of it is fictional. The data lives in one file, `netbox_data/plants_demo.yml`.

Some gaps are planted on purpose so the audit has something to find: three servers nobody owns, one site with no standing change window, plant-floor machines still on Windows XP and 7, and at Greenville a line HMI (`us-plt2-hmi01`) with no owner, sitting on the office VLAN instead of OT.

---

## 10-Minute Walkthrough

**1. Show the structure.** Open <http://localhost:8888/dcim/sites/?tag=plants-demo>. Sites roll up into regions (country), carry a tenant (business unit), a criticality and a change window. Memphis Distribution Center is flagged `Firmware Canary`: the low-impact site that receives firmware first.

**2. Ask "who owns this?"** Run `.\run.ps1 plants-audit`. The report shows owner coverage, sites with no change window, end-of-life operating systems and decommission candidates. Network gear may inherit its site's owner; servers and OT machines must have an explicit owner, which is why three servers show up as "nobody to ask."

**3. Same answers in the UI.** Each is a saved filter, one click in the filter bar (or a link):

- Devices on an end-of-life OS: `/dcim/devices/?filter=plants-old-os`
- Decommission candidates: `/dcim/devices/?filter=plants-decommission`
- Firmware canary sites: `/dcim/sites/?filter=plants-canary-sites`
- Critical sites: `/dcim/sites/?filter=plants-critical-sites`

The full list is under [Showing Them What Helps](#showing-them-what-helps). Do not build an end-of-life bookmark from a date range on the *OS End-of-Life Date* field: NetBox also matches devices that have no date at all, so the count is too high. The saved filter uses the `legacy-os` tag, which agrees with the audit.

**4. Show the blast radius.** Open any access point and look at its interface's **Connection**: it names the switch and port it plugs into. Open that switch and its uplink port names the firewall. (NetBox's Trace button follows a cable to the far end, so it stops at the first switch port; walking the uplinks is how you reach the firewall.) The contacts on the site and on each device tell you who has to be asked before you touch any of it.

**5. Show least privilege.** Log in as `demo.helpdesk`: this user can reserve an IP address and leave a journal note, but cannot edit a device. `demo.netops` can edit everything; `demo.approver` can only read and comment.

**6. Bring the fabric in.** `.\run.ps1 plants --link-fabric`, then `.\run.ps1 plants-audit --scope all`. The Arista fabric now has an owner, an approver and a change window, and shows up in the same report as the plants.

**7. Bring their spreadsheet in.** See [Importing a Spreadsheet](#importing-a-spreadsheet).

**8. Show what Meraki would add.** See [Meraki Sync](#meraki-sync-mock-dashboard). It runs against a mock dashboard, so it works without any Meraki access.

**9. Show the answers they said they lack.** See [Showing Them What Helps](#showing-them-what-helps): the change-impact card and the level-1 card.

---

## Showing Them What Helps

The workshop did not ask for configuration templates. It asked for these, and each one maps to something NetBox now shows:

| What they said | What to show | How |
|----------------|--------------|-----|
| "Who owns it? Who can decide on maintenance? Who knows when we can decommission it?" Three or four assets were running that nobody owned. | Owners, approvers and decommission candidates on every record | `/dcim/devices/?filter=plants-decommission`, and `plants-audit` |
| "We have double the Windows XP and 7 machines we thought, in Mexico and Canada." | Every old OS, every site, one click | `/dcim/devices/?filter=plants-xp-and-7` |
| "Juniper and Ubiquiti are in a spreadsheet; Meraki is in the portal." | One place for both | `/dcim/devices/?filter=plants-non-meraki-network`, then `plants-import` |
| "Everything filters up through me, and I am not the right person." | The approver for *that device*, and for everything downstream of it | `plants-impact <device>` |
| "Downstream of this switch are servers with owners I have to talk to." | What loses connectivity, and the owners to tell | `plants-impact <device>` |
| "We roll firmware to a small distribution center first." | That rule, written down, with the order for the rest | the rollout table in `plants-impact` |
| "There are no playbooks. An AP down is something we learn about from somebody else." | A level-1 card for any device | `plants-triage <device>` |
| "We are about to segment the plant floor." | Every OT network and plant-floor machine, and what blocks segmenting each plant | `/ipam/prefixes/?filter=plants-ot-networks`, then `plants-ot` |
| "We know this circuit always goes down." | The circuit, its provider's number, and how often it went down | `plants-alert simulate us-plt3-fw01 --type uplink` |
| "Hopefully the tech has put in what they did to fix it." | The last fix, quoted on the next ticket | `plants-alert resolve`, then the next alert |
| NinjaOne triggers for the Juniper gear were never set up: "we got charged for every one of them". | A ticket for gear the Meraki dashboard cannot see | `plants-alert poll --simulate-down mx-plt1-sw01` |
| "A greater picture of how the fleet is doing." | Every site on one screen, and what changed since last week | `plants-report` |

Keep Mark's warning in mind when you present it: with under 25 sites, the value is knowing what exists, who owns it and who approves a change. It is not scripting everything.

### Saved filters

`plants` creates them; `plants-views` re-creates them if you edit the list. They appear in the filter bar's **Saved filters** list for everyone, including the demo users, and every name starts with `Plants:`.

| Saved filter | Answers | Link |
|--------------|---------|------|
| Old operating systems | What is past end of life, everywhere | `/dcim/devices/?filter=plants-old-os` |
| Windows XP and Windows 7 | The machines the assessment undercounts | `/dcim/devices/?filter=plants-xp-and-7` |
| Believed unneeded | What to confirm and retire | `/dcim/devices/?filter=plants-decommission` |
| Offline devices | What the Meraki sync saw go down | `/dcim/devices/?filter=plants-offline` |
| Critical devices | Where a change needs the most care | `/dcim/devices/?filter=plants-critical-devices` |
| Plant-floor (OT) machines | HMIs and workstations on OT networks | `/dcim/devices/?filter=plants-ot-machines` |
| Meraki-managed | What the dashboard knows | `/dcim/devices/?filter=plants-meraki-gear` |
| Juniper and Ubiquiti | Network gear the portal cannot see | `/dcim/devices/?filter=plants-non-meraki-network` |
| Imported from a spreadsheet | Records that came from the register | `/dcim/devices/?filter=plants-from-spreadsheet` |
| Added by the Meraki sync | Devices the dashboard had and NetBox did not | `/dcim/devices/?filter=plants-from-meraki` |
| Firmware canary sites | Where firmware goes first | `/dcim/sites/?filter=plants-canary-sites` |
| Critical sites | Where downtime costs most | `/dcim/sites/?filter=plants-critical-sites` |
| OT networks | The segmentation project's scope | `/ipam/prefixes/?filter=plants-ot-networks` |

NetBox silently ignores a filter parameter it does not understand and then shows *everything*, so `plants-views` tries each filter first and refuses to save one that would not narrow the list. You have to be signed in to see any of this: NetBox does not allow anonymous viewing here.

### The change-impact card

```powershell
.\run.ps1 plants-impact us-plt2-sw01     # an access switch
.\run.ps1 plants-impact us-hq-fw01        # one of an HA pair
```

It answers, for one device: who owns it and who approves a change, the site's change window, **what loses connectivity if it goes down** (worked out from the cabling in NetBox), the people to tell or ask (the owners and approvers of everything affected, not only the device's own), where else the same model runs with the **safest place to start** first, and what to check afterward.

Things worth pointing out:

- On the access switch it lists the six devices behind it, including the MES server and its owner, who is not the switch's owner. That is the "owners I have to talk to" list.
- On `us-hq-fw01` it says nothing is lost because `us-hq-fw02` still connects the site. Redundancy is counted: a device with another way round is not reported as lost.
- The rollout table puts a canary site first when one runs that model (on the MS120 switch above, the Memphis distribution center is marked as the canary). When none does, it says so and falls back to lowest criticality first.
- It lists devices that are recorded at the site but not cabled, such as the spreadsheet imports. A spreadsheet has no cabling, so those cannot be counted: a concrete reason to cable them.

`--json` gives the same data for a ticketing system.

### The level-1 card

```powershell
.\run.ps1 plants-triage us-plt3-ap02      # an access point the Meraki sync marked offline
```

For a help desk technician it shows: the device's status and how NetBox came to believe it, serial and IP, **where it plugs in** (each hop to the firewall with port names and status), the **likely cause**, how many neighbours on the same switch are also down, who to call (owner, approver, escalation), and the steps.

To show the "it is the switch, not the AP" case, mark a switch offline, run the card for an AP behind it, then put it back:

```powershell
# in NetBox: set us-plt3-sw02 to Offline, then
.\run.ps1 plants-triage us-plt3-ap04     # names us-plt3-sw02 as the likely cause
# set us-plt3-sw02 back to Active
```

### What these cards are, and are not

- **The steps are examples.** They are generic and live in the `showcase.runbooks` block of `netbox_data/plants_demo.yml`. Replace them with the team's real procedures; the card fills in the device's own details. The post-change checks are examples in the same way.
- **They are only as good as the data in NetBox.** The impact lists only what is cabled to a firewall at the site; "no cabling recorded" means unknown, not safe.
- **Status is what NetBox last recorded.** After `plants-meraki --apply` that comes from the dashboard; otherwise it was typed or imported. The card says which. Neither card queries a live dashboard.
- **They are read-only.** They report; they do not change anything on the network.
- **They are a command-line view today.** The saved filters are in the NetBox UI. Putting the cards in the UI would mean installing a NetBox custom script, which changes the core NetBox container, so it is not done here.
- **Level-1 *actions*** (set a port VLAN, reserve an IP) are a separate command: see [Level-1 and Level-2 Actions](#level-1-and-level-2-actions).

---

## Importing a Spreadsheet

Their current source of truth is a spreadsheet. `plants-import` reads a CSV asset register, checks it against NetBox, and reports what is wrong with it **before** anything is written.

```powershell
.\run.ps1 plants-import                                          # validate the sample register; writes nothing
.\run.ps1 plants-import --apply                                  # import the rows that passed
.\run.ps1 plants-import netbox_data/samples/my-register.csv      # your own file
```

`netbox_data/samples/legacy_asset_register.csv` is a deliberately messy 22-row register. Validating it gives **3 ready, 10 with warnings, 9 rejected**. Applying it imports the 13 usable rows as devices, with 12 IP addresses and 11 owner assignments.

### What it finds

| Finding | Outcome | Example in the sample |
|---------|---------|-----------------------|
| Site not in NetBox | Rejected | "Plant 5" |
| Model with no matching device type | Rejected | Catalyst 2960-X, ThinkPad T14 |
| Invalid IP address | Rejected | `10.3.10.300` |
| IP already in NetBox, or used twice in the sheet | Rejected | `10.2.10.11` already belongs to `us-plt2-mes01`; two switches share `10.4.99.60` |
| Asset already in NetBox, listed twice, or nameless | Rejected | `us-plt2-fw01`; `GVL-MES-EDGE2` pasted twice; a blank row |
| Typo or variant that can only mean one thing | Imported, flagged | "Monterey Plant", "Memphis DC", owner "R. Patel" or "Luis H." |
| Nobody recorded as owner, or an owner who is not a known contact | Imported, flagged | the retired archive server; "Dave (IT)" |
| Operating system past end-of-life | Imported, flagged, tagged `legacy-os` | Windows XP, Windows 7, Server 2012 R2 |
| Retired status | Imported as *decommissioning*, tagged `decommission-candidate` | the archive server |
| IP outside the site's address plan, DHCP instead of an IP, duplicate serial, unrecognised OS or status, no criticality | Imported, flagged | `192.168.1.50` at Reno; "Windows 11"; status "TBD" |

A rejected row is never imported. A flagged row is, because a record with a known gap is more useful than no record.

A spreadsheet has no cabling, rack positions or approvers. NetBox is where those get added.

### The rules it follows

- **Dry run by default.** Nothing is written without `--apply`.
- **It only touches what it created.** Imported devices carry the `spreadsheet-import` tag. A row whose name matches any other device in NetBox is rejected, so the generated sample devices and the Arista fabric cannot be overwritten.
- **A blank cell never erases data.** If someone fixes a missing owner in NetBox and the sheet is imported again, the fix stays. When the sheet names a *different* owner or IP, the sheet wins for its own devices and the old value is replaced.
- **Safe to repeat.** A second `--apply` reports `0 created, 0 updated`.
- **Reversible.** Imported objects are also tagged `plants-demo`, so `plants-remove` removes them with the rest.

### Demo flow

1. `.\run.ps1 plants-import` and read the findings aloud. This is what a spreadsheet looks like when something finally checks it.
2. `.\run.ps1 plants-import --apply`, then open `/dcim/devices/?tag=spreadsheet-import`.
3. `.\run.ps1 plants-audit`. The imported archive server and `ukt-fs02` now appear as ownerless, and the new Windows 7 and XP machines join the end-of-life list.
4. In NetBox, assign `ukt-fs02` an owner and run `plants-import --apply` again. The fix is kept.

### Using a real customer sheet

- Save the spreadsheet as CSV (Excel: *Save As, CSV*) and put it under `netbox_data/`, for example `netbox_data/samples/`. The container cannot see files outside the repo folders that `docker-compose.yml` mounts. Do not commit customer data.
- Columns are recognised by name, ignoring case and spacing. **Required:** asset name (also `Name`, `Hostname`, `Computer Name`), site (also `Location`, `Plant`) and `Model`. Optional: `Serial`, `IP Address`, `OS`, `Owner`, `Application`, `Criticality`, `Status`, `Notes`. Any other column is listed as "not used" in the report.
- Owners are matched against the contacts in NetBox, and sites against the sites. A near match is accepted only when it is the single obvious candidate, and the report says so.
- A model is rejected, not guessed, when NetBox has no matching device type. Add the device type, or add a line under `sheet_import.model_aliases` in `netbox_data/plants_demo.yml`. The same block holds the OS names, status words and end-of-life dates.

---

## Meraki Sync (Mock Dashboard)

The spreadsheet says what someone *wrote down*. The Meraki dashboard knows what is *actually there*: which devices exist, what firmware they run, whether they are online. `plants-meraki` reads a dashboard and brings the observed facts into NetBox, then reports where the two disagree.

> **This runs against a mock, not a real Meraki organization.** The mock is a small local web service that imitates the Dashboard API (same endpoints, API-key check, paging and rate limiting) and serves the add-on's sample devices plus planted differences. It shows what the integration looks like. It has **not** been run against a real Meraki account, so expect to adjust when you point it at one. See [Pointing it at a real dashboard](#pointing-it-at-a-real-dashboard).

```powershell
.\run.ps1 plants-meraki                  # compare; writes nothing
.\run.ps1 plants-meraki --apply          # write what the dashboard observed
.\run.ps1 plants-meraki --json           # machine-readable
.\run.ps1 plants-meraki --fail-on-drift  # exit code 1 if anything disagrees (usable as a CI gate)
```

On the sample data, validating gives **60 devices matched, 2 to create, 11 sites to link**, and 13 findings.

### What it writes, and what it only reports

NetBox stays the authority for what only people know (owners, approvers, change windows). The dashboard is the authority for what is observed.

| It writes | Rule |
|-----------|------|
| Serial, Meraki Serial, Firmware Version, Source System (`meraki-api`) | Only when different |
| Status | Only `active` to and from `offline`: online or alerting means active, offline or dormant means offline. A planned, staged, failed, inventory or decommissioning device is **never** changed |
| Each site's Meraki Network ID | Links a dashboard network to a site, matched by saved ID first, then by name |
| New devices | Dashboard devices NetBox does not have, when their network is linked to a site and their model has a device type. Tagged `meraki-sync`, inheriting the site's criticality, with a management IP and a comment saying where they came from |

| It only reports | Meaning |
|-----------------|---------|
| `device-offline`, `device-alerting` | The dashboard says it is down or degraded |
| `firmware-drift` | Not on the version most devices of that model run (a patch list) |
| `ip-drift`, `model-drift`, `site-drift` | NetBox and the dashboard disagree. Someone should decide which is right, so it is not "fixed" |
| `missing-from-meraki` | NetBox has a Meraki device the dashboard does not know: decommissioned, or never onboarded |
| `unsupported-model` | A device type NetBox has no model for (the sample's camera) |
| `unknown-network`, `network-id-mismatch`, `name-conflict`, `ip-conflict` | Things it declined to guess about |

### What the sample shows

| Planted in the mock | Finding |
|---------------------|---------|
| Two access points offline, one dormant, one alerting | `device-offline` x3, `device-alerting` |
| One switch, one AP and one firewall on old firmware | `firmware-drift` x3 |
| A switch on a different IP than NetBox records | `ip-drift` |
| A switch that is a bigger model than NetBox says | `model-drift` |
| An AP that the dashboard places in another plant | `site-drift` |
| Devices NetBox has but the dashboard lacks | `missing-from-meraki`: `gvl-pack-ap01` (from the spreadsheet) and `us-dc2-ap02` |
| Two devices the dashboard has but NetBox lacks | Created: `ca-plt1-ap05` and `mem-sw03` |
| A camera | `unsupported-model` |

It works with the spreadsheet import, in either order. The sheet's Meraki rows are matched to the dashboard **by serial number**: `gvl-pack-sw01` and `mem-sw02` are confirmed, and `gvl-pack-ap01` is flagged as missing. `mem-sw03` was *rejected* by the import (it shared an IP with another switch) but the dashboard knows it, so the sync adds it. After a sync, re-importing the sheet does **not** revert the fields Meraki owns (status, serial, source).

### Demo flow

1. `.\run.ps1 plants-meraki` and read the findings. Point out that nothing was written.
2. `.\run.ps1 plants-meraki --apply`, then show `/dcim/devices/?tag=plants-demo&status=offline` and `/dcim/devices/?tag=meraki-sync`.
3. Run it again: `0 created, 0 updated`. Run `plants-audit`: the new devices have an owner through their site.
4. Note what did **not** change: `us-plt2-sw01` still has the IP NetBox records. A person decides which side is right.

A device the dashboard saw go down is set to `offline`, and `plants-audit` does **not** count it as a decommission candidate: a dead access point is broken, not retired.

### Safety

- **Validate-only by default**; `--apply` writes.
- **It cannot touch the Arista fabric.** It only considers devices tagged both `meraki` and `plants-demo`. After a full setup, import and sync, the core lab's devices, interfaces, IPs and cables are unchanged (fingerprinted before and after) and the Ansible inventory still lists exactly the 6 Arista switches.
- **It never deletes**, and an `--apply` against a dashboard NetBox does not recognise changes nothing: no networks are linked and no devices are created. Devices with a matching serial are the exception, since a serial number is an unambiguous match.
- **A blank value never erases** what NetBox has.
- **Removable.** Created devices are tagged `plants-demo`, so `plants-remove` takes them out with the rest.

### Pointing it at a real dashboard

```powershell
$env:MERAKI_API_KEY = "<read-only key>"      # or put MERAKI_API_KEY in .env
.\run.ps1 plants-meraki --base-url https://api.meraki.com/api/v1
```

- Use a **read-only** API key. The sync only issues `GET` requests, and the key is never printed.
- Run it without `--apply` first. If the key sees several organizations, choose one with `--org-id` (or `MERAKI_ORG_ID`).
- Networks are matched to sites by the **Meraki Network ID** custom field, else by identical name. Set that field on each site, or name sites the way the dashboard names networks. Until a network is linked, its devices are reported as `unknown-network` and not created.
- It reads `/organizations`, `/networks`, `/devices` and `/devices/availabilities`. It follows `Link` paging and waits and retries on HTTP 429. The older `/devices/statuses` endpoint is deprecated and is not used.
- **Not verified against real Meraki:** the exact firmware strings for MX and MS devices (they are treated as opaque text), whether your models have a device type in NetBox (add one or the device is `unsupported-model`), and behavior with very large organizations.

To explore the mock by hand:

```powershell
.\run.ps1 plants-meraki-mock    # in one window; key is mock-meraki-api-key
curl.exe -H "Authorization: Bearer mock-meraki-api-key" "http://localhost:8081/api/v1/organizations/999001/devices?perPage=5"
```

`--mock-rate-limit 3` makes the built-in mock answer HTTP 429 on every third call, to show the retry behavior.

### Tests

The decision logic, the mock API, the client, the change workflow, the level-1 actions, the alerts, the scan and the reports have automated tests (206) in `tests/`, run inside the Ansible container because it has the dependencies:

```powershell
docker compose run --rm -T -v "${PWD}/tests:/ansible/tests:ro" ansible sh -c "pip install -q pytest && python -m pytest -q -p no:cacheprovider tests"
```

---

## Change Workflow (Mock Dashboard)

A firmware change with the controls the customer said they lack: who must approve, what is downstream, whether the window is open, and evidence afterwards instead of a scream test. It runs against the **mock** Meraki dashboard. Its writes persist in `output/changes/dashboard_state.json`, so `plants-meraki` sees them too.

```powershell
.\run.ps1 plants-change plan us-hq-sw02 --summary "Switch firmware" --at "2026-10-25 14:00"
.\run.ps1 plants-change approve CHG-YYYYMMDD-001 --by "Dana Ortiz"      # once per required approver
.\run.ps1 plants-change apply   CHG-YYYYMMDD-001 --as-of "2026-10-25 02:00"
.\run.ps1 plants-change verify  CHG-YYYYMMDD-001
.\run.ps1 plants-change confirm CHG-YYYYMMDD-001 --by "Nora Fields"     # an owner checks the box
.\run.ps1 plants-change reset --yes                                     # put the demo back
```

| Step | What it does |
|------|--------------|
| `plan` | Scopes every device of the same product in the same Meraki network, walks the cables for what sits behind them, collects the approvers and owners to notify, and checks the planned time against the site's change window in the **site's** time zone. Writes `output/changes/<id>.md` (ticket text) and a NetBox journal entry. |
| `approve --by` | Only a recorded approver is accepted. |
| `apply` | Refuses until everyone has approved and the window is open. `--override-window REASON` forces it (and is recorded); `--as-of` simulates the site clock; `--simulate-fault DEVICE` makes a device drop after the change; `--simulate-no-clients AP` leaves an access point up with nobody able to join. |
| `verify` | Re-reads the dashboard and runs the checks: firmware, changed devices online, everything downstream still online (a device already down beforehand is not blamed on the change), management IP matches NetBox, wireless clients back on every access point that had some at `plan` time (none is a failure, under half a warning), and owners confirm what the dashboard cannot see. Writes `<id>-evidence.md`. On PASS it updates the NetBox `firmware_version` field. |
| `confirm --by OWNER [--note]` | An owner of something the dashboard cannot see (servers, HMIs) says it works. Only an owner recorded for one of those devices is accepted. The check passes once every such device is confirmed; one with no owner keeps it open, by design. Run `verify` again to refresh the evidence. |
| `fault DEVICE [--clear] [--no-clients]` | Demo lever: a device goes offline (or an AP loses its clients), or comes back. |

The mock applies firmware **immediately**; real Meraki schedules an upgrade. That is the one honest difference to mention.

---

## Level-1 and Level-2 Actions

Runbook steps a helpdesk or a network engineer can run, as themselves. `--as USER` signs in with a short-lived NetBox token for that demo account, so **NetBox's permissions decide what is allowed**, not the script.

```powershell
.\run.ps1 plants-l1 find 10.2.10.11
.\run.ps1 plants-l1 --as demo.helpdesk reserve-ip us-plt2 100 --for "New label printer"
.\run.ps1 plants-l1 --as demo.helpdesk port-vlan us-plt2-sw02 12 100        # denied: escalate to L2
.\run.ps1 plants-l1 --as demo.netops   port-vlan us-plt2-sw02 12 100 --label "Label printer"
.\run.ps1 plants-l1 --as demo.helpdesk dhcp us-plt2                         # the OT scope is 98% full
.\run.ps1 plants-l1 reset --yes
```

- **`find`** says what an IP or device is, where it plugs in, who owns it and its change window.
- **`reserve-ip`** takes the next free static address (`.100`-`.199`) in the VLAN and records the reason in a journal entry. `--dry-run` shows it first.
- **`port-vlan`** writes NetBox first, then pushes to the mock dashboard (and puts NetBox back if the push fails). It refuses a port cabled to network equipment, a VLAN the site does not have, and any switch that is not Meraki.
- **`dhcp`** reads the site MX's DHCP scopes (`GET /devices/{serial}/appliance/dhcp/subnets` on the mock) and marks each `ok`, `filling` (80%) or `full` (95%; change with `--warn` and `--full`). For a full scope it prints the runbook: look for one device taking many addresses, reserve a static address for what needs one now, and escalate a lease-time or scope change to the site's escalation contact. It does not clear leases. Where a server does DHCP (HQ), it names that server and its owner from NetBox instead. Exit code 2 when a scope is full.
- Juniper and Ubiquiti switches are recorded in NetBox but nothing pushes to them yet. The command says so rather than pretending.

`plants-change reset --yes` also undoes the level-1 changes.

---

## Alerts to Tickets (Mock Dashboard)

The workshop asked for "monitoring integration with automation, if there's a way to trigger something". `plants-alert` takes an alert shaped like a Meraki webhook and opens a ticket that already carries what NetBox knows.

```powershell
.\run.ps1 plants-alert simulate us-hq-ap03                 # the mock dashboard reports it down
.\run.ps1 plants-alert simulate us-plt2-fw01 --type dhcp   # a DHCP scope nearly exhausted
.\run.ps1 plants-alert handle output/alerts/webhook-....json   # any saved webhook body
.\run.ps1 plants-alert list
.\run.ps1 plants-alert reset --yes                         # delete them; undo simulated outages
```

Each ticket (`output/alerts/INC-<date>-<n>.md`, plus a warning in the device's NetBox journal) has:

- **Priority** from the criticality of the device and of anything that lost connectivity behind it (critical P1, high P2, medium P3, low P4).
- **Assign to** the owner, **escalate to** the escalation contact, and the owners of what is behind it to tell.
- **Recent changes:** a `plants-change` applied in the last 72 hours (`--since-hours`) that changed the device, what it sits behind, or its site. The likely cause says to rule that out first.
- **History:** earlier incidents of the same kind on the same device in 30 days ("2 times in 30 days"), and the **last fix** of any age. See [Known Fixes](#known-fixes).
- **Circuit:** for a firewall uplink alert, the circuit on that port, the provider's NOC number, account and SLA, and whether a backup circuit should be carrying the site. See [Circuits and Providers](#circuits-and-providers).
- **Steps:** the level-1 runbook for that kind of device and vendor (Juniper and Ubiquiti have their own); for a DHCP alert, `plants-l1 dhcp <site>`.

```powershell
.\run.ps1 plants-alert simulate us-plt3-fw01 --type uplink   # WAN1 drops at Tulsa
.\run.ps1 plants-alert show INC-YYYYMMDD-004                 # the card again
```

A device NetBox does not know still gets a ticket, saying so: that is a CMDB gap to fix. The body has the fields of a Meraki webhook (`alertType`, `deviceSerial`, `networkId`, ...) but has not been compared with a real one, and the ticket is a file: handing it to an ITSM is one API call that is not written yet.

### Known fixes

"Hopefully the tech has put in what they did to fix it." Closing a ticket asks for exactly that:

```powershell
.\run.ps1 plants-alert resolve INC-YYYYMMDD-005 --fix "PoE had tripped on us-hq-sw03 Port 1. Power-cycled the port." --by "Sam Whitaker"
.\run.ps1 plants-alert simulate us-hq-ap03      # the new ticket quotes that fix
```

`--fix` is required. The fix goes into the ticket file and the device's NetBox journal, and a simulated outage ends. The next alert **of the same kind** on that device shows a *Last fix* row and adds "Last time (ID, date): ..." to the likely cause. A DHCP alert does not quote an uplink fix.

The firewall at Tulsa comes with three earlier tickets (`ZD-...`) from the `incident_history` block of `netbox_data/plants_demo.yml`. They are labelled **ticket history (sample)** wherever they appear; their dates are relative to today, so the "in 30 days" counts stay true. With a real ITSM, this history would be read from it.

---

## Monitoring the Gear Meraki Cannot See (Mock Poller)

The Juniper and Ubiquiti switches send no Meraki alerts, and the NinjaOne triggers for them were never set up because each one is charged. `plants-alert poll` is one pass of a monitoring loop that covers them:

```powershell
.\run.ps1 plants-alert poll                                  # 2 tickets: a circuit at 86%, a DHCP scope at 98%
.\run.ps1 plants-alert poll --simulate-down mx-plt1-sw01     # the Monterrey Juniper core stops answering
.\run.ps1 plants-alert poll --simulate-up mx-plt1-sw01       # it answers again
```

| Check | Source | Ticket when |
|-------|--------|-------------|
| Juniper and Ubiquiti gear answers | Devices tagged `juniper` or `ubiquiti` in NetBox, by their primary IP | It does not answer: `Device unreachable`, with the vendor's runbook and what is behind it |
| Internet circuits under 80% of commit | The mock dashboard's uplink usage for each primary circuit | Over the threshold (`--uplink-pct`): `Uplink near capacity`, P3 or P4, with "ask the provider for a quote" |
| DHCP scopes not full | The mock dashboard's DHCP scopes | A scope is full: `DHCP scope nearly exhausted` |

It does not open a second ticket while one is open for the same device and problem, and it reports a device that answers again. Run it from a scheduler every few minutes and it is a monitor.

**The asking is mocked.** NetBox decides *what* to check and *what to put on the ticket*. Whether a switch answers comes from `monitor_mock` in the data file and the `--simulate-down` marks (kept in `output/alerts/probe_state.json`); nothing is pinged. Real polling would be ping or SNMP from a box at each site, or their RMM. The uplink usage comes from the mock's version of `GET /networks/{id}/appliance/uplinks/usageHistory`, whose shape has not been checked against real Meraki.

---

## Circuits and Providers

Every site has a primary internet circuit in NetBox's **Circuits** (`/circuits/circuits/?tag=plants-demo`), and HQ and Tulsa also have a backup. Each provider's comments hold its NOC number, portal, account number and SLA. The circuit's termination says which firewall port it lands on, for example "WAN1 on us-plt3-fw01".

That gives an uplink alert a phone number instead of a hunt. On the Tulsa alert the ticket says: call Windstream on this number with this account and circuit; check WAN2 (the 50 Mbps LTE backup) is carrying traffic and expect it to be slow; tell the plant manager; and, because it is the fourth outage in 30 days, send Windstream the dates and ask for a root cause and the service credits. A site with no backup circuit gets "nothing at this site reaches the internet" instead.

`plants-report` counts incidents per provider over 30 days, which is the start of a provider review.

The circuits are not cabled to the firewalls: the core lab's topology scripts assume every cable joins two interfaces. All providers, accounts and numbers are fictional.

---

## Estate Report

"A greater picture of how the fleet is doing." One command, one screen, every site:

```powershell
.\run.ps1 plants-report
```

Each row is a site: devices, down (dashboard or poller), no owner, old OS, dashboard drift, unknown (on the dashboard or answering a scan, but not in NetBox), the fullest DHCP scope, the primary circuit's usage, incidents in 30 days and open ones. Below it, **Needs attention**: open incidents by priority, repeat problems ("4 times in 30 days"), full scopes, busy circuits, devices down, unowned devices at critical or high sites, and old operating systems. Then incidents per provider.

It writes `output/reports/estate-<time>.md` (for an email or the ticket system) and a `.json` copy. The next run compares with the last copy and shows the change in each total, for example "Down: 7 (+2)", so running it weekly shows whether things are getting better.

It reads NetBox, the mock dashboard, the mock poller and the incident files. It writes nothing to NetBox.

---

## OT Segmentation Readiness

They are about to segment the plant floor. A firewall between OT and the rest cuts off anything that was not known about. `plants-ot` checks each plant first:

```powershell
.\run.ps1 plants-ot              # every plant, one line each, then the detail
.\run.ps1 plants-ot us-plt2      # one plant
```

| It flags | Why it blocks |
|----------|---------------|
| Unknown devices answering on the OT network (from the scan) | The firewall will cut them off; nobody knows what they are |
| OT machines on another VLAN (`us-plt2-hmi01` on the office VLAN) | Move them first, or the new rules miss them |
| OT machines with no IP in NetBox (`suz-pc-line2`, from the spreadsheet) | Nobody knows where they plug in |
| OT machines with no owner | Nobody can sign off the change or test the line afterwards |
| A decommission candidate an OT machine depends on (`mx-plt1-srv-old01`) | Do not retire it before the dependency is moved |

It also lists machines on unsupported operating systems (the firewall becomes their only protection) and the **draft firewall flows**: each OT machine to the servers it depends on (the *Depends On* field), with addresses, VLANs and the owner to ask. Cross-site flows are shown too: the Suzhou PC reaches the MES server at Columbus.

On the sample: Greenville has **4 to fix**, Monterrey and Suzhou 1, Tulsa is ready. It writes `output/reports/ot-readiness-<date>.md`.

Ports are not recorded in NetBox, so each flow says who to ask for them. The flows are a draft for the project, not firewall rules.

---

## Network Scan (Mock)

Mark's phase one: "scan the network, get the source of truth". `plants-scan` compares what answers at a site with what NetBox records:

```powershell
.\run.ps1 plants-scan us-plt2            # 3 answer that NetBox has never heard of, 2 on OT
.\run.ps1 plants-scan all
.\run.ps1 plants-scan us-plt2 --apply    # record them in NetBox, tagged scan-found
.\run.ps1 plants-scan all --forget       # delete what earlier scans recorded
```

It reports three lists: **not in NetBox** (with the vendor from the MAC address, a name if one answered, and open ports), **recorded but silent** (decommissioned, or switched off), and the count that match. An unknown on an OT network gets the warning that segmentation would cut it off; `plants-ot` reads the same results.

`--apply` creates an IP address for each unknown, tagged `plants-demo` and `scan-found`, with what the scan saw in the description. A rescan then lists it as "recorded by an earlier scan, still not identified" until someone assigns it to a device.

**The scan is mocked.** What answers is NetBox's own addresses, minus the `scan_mock.silent` list, plus the `scan_mock.extras` in the data file. A real scan would be nmap or the RMM's discovery from a box at each site; this is the comparison and the recording that would sit behind it.

---

## Demo Accounts

| User | Group | Can do |
|------|-------|--------|
| `demo.netops` | `plants-netops` | View all; add/change/delete sites, devices, interfaces, cables, VLANs, prefixes, IPs, contacts |
| `demo.helpdesk` | `plants-helpdesk` | View all; add/change IP addresses; add journal entries |
| `demo.approver` | `plants-approvers` | View all; add journal entries |

The password is `PlantsDemo-2026!` unless you set `DEMO_USER_PASSWORD` in `.env` before running. This is a lab-only default; never reuse it. Admin credentials are unchanged.

---

## How It Stays Additive

- **Isolated by tag.** Everything is tagged `plants-demo` and **nothing** is tagged `containerlab`. The core lab selects devices by that tag, so its Ansible inventory, config generation, ContainerLab topology and AVD export never include the add-on's devices. We verified this: the generated topology is byte-identical before and after loading the add-on and after the spreadsheet import, with 6 hosts, 6 nodes and 12 links.
- **Own reference data.** New manufacturers, device types, roles, platforms, custom fields and tenants. Nothing existing is edited.
- **Hidden where irrelevant.** The custom fields are set to show only when they have a value, so the Arista devices do not display empty plant fields.
- **Reversible.** `plants-remove --yes --purge-model` returns NetBox to its pre-add-on state.

`--link-fabric` is the one place the add-on writes to a core object, and it only adds contact assignments and custom-field values to the `DC1` site. Removing the add-on takes them back out.

### Known interactions

- The core Ansible inventory groups hosts by site, so the plant sites appear as **empty groups** (for example `@sites_us-plt2`) in `ansible-inventory --graph`. No plant device is a host in any group, and the inventory still lists exactly the 6 Arista switches, so no play can reach a plant device.
- The core lab's `fabric-*` Config Contexts are not scoped to a role or site, so they also appear on the plant devices' Config Context tab. They are not used for anything there. Scoping them to the `evpn-fabric` tag in the core lab would hide them.
- The core topology and AVD scripts loop over every cable in NetBox and skip any that are not between `containerlab` devices. With the add-on installed that is 74 extra cables to skip, which is a negligible slowdown. This is also why the add-on's circuits are not cabled to the firewalls: a circuit termination is not an interface, and those scripts would trip over it.
- Viewing requires signing in. Anonymous visitors cannot read data: the API answers 403 and the UI redirects to the login page, even though `LOGIN_REQUIRED` is `false` in `docker-compose.yml` (that setting does not grant anonymous viewing). The demo users all have read access; they differ in **edit** rights.

---

## Editing the Sample Data

`netbox_data/plants_demo.yml` is the only file to edit. Then re-run `plants`.

- **Add a site:** copy an entry under `sites:`, pick an unused `index` (it becomes the second octet of the site's `10.x.0.0/16`), and choose a `profile` (`large`, `medium`, `small`). Devices, VLANs, prefixes, IPs and cables are generated from the profile.
- **Mix vendors:** `overrides: {switch: ex2300-24p}` swaps a site's switches for another device type.
- **Add owners:** list contact keys under `contacts:` by role. The first contact listed is the primary.
- **Add a server or machine:** add an entry under `servers:` or `endpoints:`. Leave off `owner:` to see it flagged.
- **Change the saved filters, runbook steps or post-change checks:** edit the `showcase:` block, then run `plants-views` for filters. Runbook steps may use only the placeholders listed in that block; the tests catch a typo.
- **Adapt the spreadsheet import:** the `sheet_import:` block maps free-text model names, operating systems and statuses to NetBox values, and holds the OS end-of-life dates.

---

## Not Included Yet

The data foundation, the spreadsheet import, the (mock) Meraki sync, the saved filters, the impact and level-1 cards, the change workflow, the level-1/2 actions, the alert intake with known fixes, the (mock) poller, circuits, the estate and OT readiness reports and the (mock) scan are in place. Not built yet:

- A real ticketing hook. The change workflow and the alert intake write ticket text files and NetBox journal entries; they do not open a ticket in an ITSM (the team mentioned Zendesk), and approval is recorded by name, not by sign-in. The earlier tickets in the history are samples, not read from an ITSM.
- A listener for live webhooks. `plants-alert handle` reads a saved body; it does not run a web server.
- Real polling and real scanning. The poller and the scan decide what answers from the data file; real ones would be ping or SNMP and nmap (or the RMM) from a box at each site.
- Ports for the OT flows. NetBox records which server a machine depends on, not on which ports.
- The uplink usage endpoint has not been checked against real Meraki, and the providers, accounts and phone numbers are fictional.
- Clearing DHCP leases, application-level checks after a change, and security actions such as quarantining a client.
- A NetBox Event Rule that starts a change automatically.
- A sync or write path for the Juniper and Ubiquiti gear.
- Any check against a real Meraki organization (writes in particular have only run against the mock).

---

**Back to:** [[Home]] | [[Day 2 Operations]]
