# Workshop 2 Demo Script

The live part of Workshop 2 for the manufacturer that runs Meraki, Juniper and Ubiquiti. The commands, in order, are in [[Workshop 2 Walkthrough]]. This page is what to say. The slides are made separately. Six acts, about 50 minutes. If time is short, cut Act 5 to the OT summary line and Act 6 to the failure story.

| Act | They said | Commands |
|-----|-----------|----------|
| 1. The source of truth (9 min) | We do not know who owns what, who approves, or what depends on it; it lives in spreadsheets; scan the network first | saved filters, `plants-import`, `plants-meraki`, `plants-scan`, `plants-audit`, `plants-impact` |
| 2. A change with a gate and evidence (10 min) | Approvals all go to Brandon; afterwards it is a scream test | `plants-change` |
| 3. Level 1 and Level 2 runbooks (7 min) | Port VLANs, IP reservations and DHCP scopes should not reach Mark | `plants-triage`, `plants-l1` |
| 4. An alert becomes a ticket (8 min) | Monitoring integration, "if there's a way to trigger something"; known issues; "this circuit always goes down" | `plants-alert` |
| 5. The fleet, and the OT project (6 min) | "A greater picture of how the fleet is doing"; segmenting the plant floor | `plants-report`, `plants-ot` |
| 6. Day 2 on a real fabric (8 min) | Templated configs, consistent validation, proof it worked | `day2`, `generate`, `diff`, `push`, `test` |

Acts 1 to 5 run on mock data (tagged `plants-demo`, with a **mock** Meraki dashboard, a **mock** poller and a **mock** scan) and never touch the Arista lab. Act 6 changes the six real cEOS switches. Say "mock" out loud the first time you use each one.

## What they asked for, and where it is answered

Every point raised in Workshop 1, with who raised it. "Talk track" means it is answered in words or slides, not by a command.

| They said | Who | Answered by |
|-----------|-----|-------------|
| No single source of truth; several spreadsheets | Brandon, Mark | Act 1: saved filters; `plants-import` checks a spreadsheet before anything is written |
| Phase one: scan the network, get the source of truth | Mark | Act 1: `plants-meraki` compares the dashboard with NetBox (new, missing, offline, wrong firmware); `plants-scan us-plt2` finds three addresses answering that NetBox has never heard of, two on the OT network. The scan is a mock: a real one would be nmap or the RMM from each site |
| Who owns the application on this server, who decides its maintenance window | Mark, Brandon | Act 1: device page and `plants-impact` |
| Old operating systems found, then kept in spreadsheets | Brian | Act 1: `plants-audit` end-of-life list, saved filter "Old operating systems" |
| Juniper and Ubiquiti tracked loosely in a spreadsheet | Lee | Act 1: saved filter "Juniper and Ubiquiti"; Act 3 step 8 is honest that nothing pushes to them yet |
| NinjaOne is the only source of truth for endpoints | Norm | Talk track: an RMM export is one more sheet for `plants-import`. Not demoed |
| Back up, save and version-control the data | Brian | Act 1: the device's Changelog tab, and journal entries written by every command |
| Approvals go to Brandon and two people no longer in infrastructure | Brandon | Act 2: approvers come from NetBox, per device and for what is behind it |
| The change form is generic, not tied to any system | Brandon | Act 2: `change plan` writes the ticket text from NetBox |
| Firmware goes to a small distribution centre first | Lee | Act 2: the plan warns when the canary site is not on the new version yet |
| After a change: ping, services, traffic, wireless clients; otherwise a scream test | Brandon | Act 2: `change verify` (online, what is behind it, firmware, IP, wireless clients back). Act 6: `test` (BGP, MLAG, routes, pings, VXLAN). Application-level checks are not built |
| Multiple teams run their validation, then check the box | Mark | Act 2: `change confirm --by <owner>` for what the dashboard cannot see |
| Attach the tests to the ticket | Brian, Brandon | Act 2 evidence file; Act 6 validation report |
| Do not overbuild: a firmware upgrade is a click in Meraki. Automate the testing | Mark, Brandon | Act 2 talk track: `apply` stands in for their click; the plan and the verification are the automated part |
| No playbooks; Level 1 should have runbooks | Mark, Brandon | Act 3: `plants-triage` card |
| Past Level 1 you call Cisco TAC or Juniper JTAC | Mark | Act 4: a Juniper switch's ticket carries the Juniper runbook, ending in a JTAC case |
| Level 1: change a VLAN on a port for a time clock | Mark | Act 3: `plants-l1 port-vlan`, allowed for Level 2 only, by NetBox permissions |
| Level 1: reserve an IP address | Mark | Act 3: `plants-l1 reserve-ip` |
| Level 1: clear a DHCP scope; how full the scopes are getting | Mark, Norm | Act 3: `plants-l1 dhcp`. It shows usage and gives the runbook; it cannot clear leases (see the notes) |
| "Do you have an IP address?" basic help-desk troubleshooting | Brian | Act 3: `plants-l1 find <ip>` |
| Everything escalates to Lee or Mark | Brian | Act 3: help desk does the safe actions; a refusal says why and to escalate |
| Monitoring integration: trigger something, auto-log a ticket | Brian, Mark (agenda) | Act 4: `plants-alert` turns a dashboard alert into a ticket with owner, impact and steps |
| An AP going down does not reach the help desk | Norm | Act 4 |
| An IP scope problem arrived by IM, with no ticket | Mark | Act 4: `plants-alert poll` opens a ticket for the full scope |
| "Hopefully the tech has put in what they did to fix it"; knowledge bases are few and manual | Norm, Brandon | Act 4: `plants-alert resolve --fix` is required to close, and the next alert of the same kind quotes the last fix |
| "We know this circuit always goes down"; pull it all up when the carrier contract renews | Brian | Act 4: the uplink alert names the circuit, the provider's NOC number, account and SLA, counts 4 outages in 30 days and says to ask for a root cause and service credits. Act 5: incidents per provider. The earlier tickets are samples, not their Zendesk |
| NinjaOne triggers for the Juniper gear were never set up, because each one is charged | Brandon, Norm | Act 4: `plants-alert poll` checks the Juniper and Ubiquiti gear NetBox lists and opens a ticket when one stops answering. The poller is a mock: a real one would ping or use SNMP from each site |
| Thresholds (80% utilisation) that trigger something | Brian | Act 4: `plants-alert poll` opens a ticket for the Greenville circuit at 86% of its commit, and for the DHCP scope at 98%. Act 3: DHCP thresholds |
| "I don't have a greater picture of how the fleet is doing"; things we did not think to monitor | Norm | Act 5: `plants-report`, every site on one screen with what needs attention and the change since last time |
| Telemetry from every system in one place; AD and DNS health | Mark, Norm | Talk track: that is a monitoring tool's job; NetBox gives its alerts context (Act 4) and the report a structure (Act 5). Not built |
| "Going into the OT space, we're going to be looking to segment in some OT firewalling" | Brian | Act 5: `plants-ot` lists, per plant, unknown devices on OT, machines on the wrong VLAN or with no owner, a server not to retire yet, and the draft flows the firewall must allow |
| Security events: quarantine non-compliant clients (Intune, Meraki), shut ports | Mark, Brian | Talk track: the same port action as Act 3, triggered like Act 4, once there is a policy and an approver. Not built |
| Onboarding and offboarding (13%), application access (24%) | Brandon, Norm | Talk track: an ITSM and identity workflow, not NetBox. NetBox can name the application owner who approves access. Not built |
| Integrate with the ITSM; 85% of tickets come through one general form | Brian, Brandon | Acts 2 and 4 write structured ticket text; sending it is one API call once they choose. Ask which tool (Norm mentioned Zendesk) |
| Start small, keep something to grow on | Brandon, Mark | Talk track: each act is a small command over the same NetBox data |
| Day 2: standing up a new network device | Brian | Act 6 changes a running fabric. Zero-touch onboarding of a new switch is not shown |

## Before the call

```powershell
.\run.ps1 status                  # NetBox healthy
docker ps --filter name=clab-netbox-arista-lab   # six switches running
.\run.ps1 plants                  # the sample organisation, once (skip if loaded)
.\run.ps1 plants-meraki --apply   # after every `plants` run: it puts back what the dashboard observed
.\run.ps1 plants-change reset --yes
.\run.ps1 plants-alert reset --yes
.\run.ps1 plants-scan all --forget
.\run.ps1 plants-report           # a baseline, so the Act 5 run shows what changed
.\run.ps1 day2 status             # nothing applied
.\run.ps1 diff                    # "The switches already match NetBox."
.\run.ps1 test                    # PASS: all 36 checks passed
.\run.ps1 backup
```

If the switches are not running: `.\run.ps1 deploy` (about 6 minutes; it pushes the configs too). Each `run.ps1` command takes 15-20 seconds to start because it runs in a container: talk while it runs.

Sign in to NetBox at http://localhost:8888 as admin for the browser parts.

## Act 1: The source of truth (9 min)

1. **Devices** in the browser, then the saved filters "Plants: Critical devices", "Plants: Old operating systems" and "Plants: Juniper and Ubiquiti". Open a device: owner, approver, criticality, change window, firmware, where it plugs in. Click its **Changelog** tab: every edit, who and when. Then **Circuits**: each site's internet circuit, and on the provider its NOC number, account and SLA.
2. `.\run.ps1 plants-import`. Read two findings aloud. Nothing is written: "this is your spreadsheet, checked".
3. `.\run.ps1 plants-meraki`. The mock dashboard against NetBox: devices NetBox has never heard of, ones that are offline, old firmware, an IP that disagrees. Nothing is written without `--apply`.
4. `.\run.ps1 plants-scan us-plt2`. Mark's "scan the network": 12 addresses match NetBox, and 3 answer that NetBox has never heard of: a Raspberry Pi called `pi-scoreboard` on the office network, and a Rockwell and a Siemens device on the **OT** network. "Before anyone puts a firewall in front of the plant floor, someone has to know what those are." Say it is a mock scan.
5. `.\run.ps1 plants-audit`. Devices with no owner, end of life soon. "This list is the work, not a surprise."
6. `.\run.ps1 plants-impact us-hq-sw02`. Owners, approvers, what is behind this switch, who to tell, rollout order.

Land: the ticket system holds the *request*; NetBox holds the *facts* the request needs.

## Act 2: A change, with a gate and evidence (10 min)

Firmware on one HQ switch. The plan covers all three switches in that Meraki network, because that is how the dashboard upgrades.

```powershell
.\run.ps1 plants-change plan us-hq-sw02 --summary "Switch firmware" --at "2026-10-25 14:00"
```

Point out: 3 switches in scope, 14 devices behind them, four approvers (not just Brandon), and 14:00 is outside the Sunday 01:00-05:00 window. `us-hq-legacy01` has no owner: "nobody recorded".

Replace `CHG-...` with the id it printed.

```powershell
.\run.ps1 plants-change apply   CHG-... --as-of "2026-10-25 02:00"   # refused: not approved
.\run.ps1 plants-change approve CHG-... --by "Dana Ortiz"
.\run.ps1 plants-change approve CHG-... --by "Priya Nair"
.\run.ps1 plants-change approve CHG-... --by "Carla Mendez"
.\run.ps1 plants-change approve CHG-... --by "Elena Petrova"
.\run.ps1 plants-change apply   CHG-... --as-of "2026-10-25 02:00" --simulate-no-clients us-hq-ap03
.\run.ps1 plants-change verify  CHG-...                              # FAIL
```

Every device is online and on the new firmware, and it still fails: `us-hq-ap03` had clients before and has none now. That is Brandon's "do I see clients connecting to wireless", answered in seconds and by name.

```powershell
.\run.ps1 plants-change fault us-hq-ap03 --no-clients --clear
.\run.ps1 plants-change verify  CHG-...                              # PASS, owners pending
.\run.ps1 plants-change confirm CHG-... --by "Nora Fields" --note "DCs and file shares fine"
.\run.ps1 plants-change confirm CHG-... --by "Raj Patel"
.\run.ps1 plants-change confirm CHG-... --by "Elena Petrova"
.\run.ps1 plants-change confirm CHG-... --by "Carla Mendez"
.\run.ps1 plants-change verify  CHG-...
.\run.ps1 plants-change show CHG-... --evidence
```

This is Mark's "multiple teams run their validation, then check the box". The check stays open for one device: `us-hq-legacy01` has no owner, so nobody can confirm it. That is the CMDB gap from Act 1, showing up in a change.

Show the evidence file (`output/changes/CHG-...-evidence.md`), and on `us-hq-sw02` the updated firmware field and the journal.

Say plainly:
- `apply` is the mock doing what their click in the Meraki dashboard does. The plan and the verification are what is worth automating (Mark's and Brandon's point).
- The mock upgrades immediately; real Meraki schedules it.
- Approval is by name here; in production it would come from the ITSM tool.

## Act 3: Level 1 and Level 2 (7 min)

1. `.\run.ps1 plants-triage us-plt3-ap02`: path, likely cause, who to call, the steps.
2. `.\run.ps1 plants-l1 find 10.2.10.11`: what is this IP, where is it, who owns it.
3. `.\run.ps1 plants-l1 --as demo.helpdesk dhcp us-plt2`: the OT scope at Greenville is 98% full. The runbook says what Level 1 can do, and who to escalate to for the rest.
4. `.\run.ps1 plants-l1 --as demo.helpdesk dhcp us-hq`: at HQ the MX does not do DHCP. NetBox knows the Windows server that does, and its owner.
5. Help desk, a safe action:
   `.\run.ps1 plants-l1 --as demo.helpdesk reserve-ip us-plt2 100 --for "New label printer"`
6. Help desk, an unsafe one:
   `.\run.ps1 plants-l1 --as demo.helpdesk port-vlan us-plt2-sw02 12 100 --label "Time clock"` is denied. "That is NetBox's permission model, not our script."
7. Level 2 does it: the same command with `--as demo.netops`. NetBox and the mock dashboard both change; a journal entry records who. `plants-l1 --as demo.netops port-vlan us-plt2-sw01 1 100` is refused, because that port feeds an access point.
8. A Juniper site: `plants-l1 --as demo.netops port-vlan mx-plt1-sw02 3 100` says NetBox can record it but nothing pushes it. Be honest: that is the next piece of work.

## Act 4: An alert becomes a ticket (8 min)

Replace each `INC-...` with the id the previous command printed.

**An access point, and what fixed it last time.** The change from Act 2 is still applied. Now an access point behind those switches drops:

```powershell
.\run.ps1 plants-alert simulate us-hq-ap03
```

Before anyone looks, the ticket has a priority from NetBox's criticality (P1: HQ is critical), who to assign it to, who to escalate to, the Level 1 steps, and the switch port it hangs off (`us-hq-sw03` Port 1). It also says "CHG-... changed what this device sits behind ...: rule that out first."

Norm's "hopefully the tech has put in what they did to fix it": closing the ticket requires it.

```powershell
.\run.ps1 plants-alert resolve INC-... --fix "PoE had tripped on us-hq-sw03 Port 1. Power-cycled the port; AP back in 3 minutes." --by "Sam Whitaker"
.\run.ps1 plants-alert simulate us-hq-ap03
```

The new ticket says "2 times in 30 days", has a **Last fix** row, and the likely cause starts "Last time (INC-..., date): PoE had tripped...". That is a knowledge base the help desk builds by closing tickets.

**The circuit that always goes down.**

```powershell
.\run.ps1 plants-alert simulate us-plt3-fw01 --type uplink
```

WAN1 at Tulsa. The ticket names the circuit (`WIN-TUL-88213`, Windstream, 500 Mbps), the number to call with the account and the SLA, and says the site should now be on its 50 Mbps LTE backup, so expect it to be slow. History: **4 times in 30 days**, with the last fix (a fibre cut). Step 4: "send Windstream the dates and ask for a root cause and the service credits the contract allows." That is Brian's "hammer the vendor" at renewal time. The three earlier tickets are labelled *ticket history (sample)*: say so.

**What the Meraki dashboard cannot tell you.**

```powershell
.\run.ps1 plants-alert poll
.\run.ps1 plants-alert poll --simulate-down mx-plt1-sw01
.\run.ps1 plants-alert list
```

The first pass checks the 5 Juniper and Ubiquiti devices NetBox lists, the 11 primary circuits and 37 DHCP scopes, and opens two tickets: Greenville's circuit at **86%** of its 500 Mbps (P3, "ask Spectrum for a quote") and the OT DHCP scope at 98%, the problem that once arrived by IM. The second pass: the Monterrey Juniper core switch stops answering. P2, **13 devices behind it**, the Juniper runbook ending in a JTAC case, and no duplicate tickets for the two still open.

Show `output/alerts/INC-...md` and the warning in the device's journal in NetBox.

Say plainly: the dashboard alert, the poller and the circuit usage are mocks with the real shape; `plants-alert handle <file.json>` takes a real webhook body. The ticket is a file here; sending it to their ITSM is one more API call, once they choose the tool. Run from a scheduler, `poll` is the monitor they never set up in NinjaOne.

## Act 5: The fleet, and the OT project (6 min)

```powershell
.\run.ps1 plants-report
```

Norm's "greater picture of how the fleet is doing". One row per site: devices, down, unowned, old OS, dashboard drift, unknown, fullest DHCP scope, circuit usage, incidents and open tickets. The totals show the change since the baseline run before the call ("Open: 5 (+5)"). **Needs attention** starts with the open tickets from Act 4, then the repeats (Tulsa's circuit, 4 times), then the full scope, the busy circuit and what is down. The provider table puts Windstream at the top. It also writes a Markdown copy for an email.

```powershell
.\run.ps1 plants-ot
.\run.ps1 plants-ot us-plt2
```

"Going into the OT space, segment in some OT firewalling." One line per plant first: Tulsa is ready; Greenville has **4 to fix**. At Greenville: the Rockwell and Siemens devices from the Act 1 scan, an HMI for Door Line 3 that sits on the office VLAN, and that HMI has no owner to sign off or test the change. Then the draft flow the firewall must allow, HMI to MES server, and who to ask for the ports (Raj Patel).

Scroll to Monterrey in the full run: "Do not retire `mx-plt1-srv-old01`: `mx-plt1-hmi05` depends on it." The audit called that server a decommission candidate. Suzhou: a PC that talks to the MES server at Columbus, across sites.

Land: the segmentation project starts from a list of what to fix and who to talk to, not from an outage on the line.

## Act 6: Day 2 on the Arista fabric (8 min)

The same idea on real (containerised) switches: NetBox is the intent, the configs are generated, and every push is previewed and tested.

```powershell
.\run.ps1 day2 apply add-vlan     # VLAN 150 added in NetBox (config context tenant-core)
.\run.ps1 generate
.\run.ps1 diff                    # only the 4 leaves change: VLAN 150, its gateway, VNI 10150, EVPN
.\run.ps1 push                    # OK: <switch> matches NetBox (saved), x6
.\run.ps1 test                    # PASS: all 36 checks; report in output/reports/
```

Then the failure story:

```powershell
.\run.ps1 day2 apply bad-asn      # one leaf of a pair gets the wrong AS, a typo
.\run.ps1 generate; .\run.ps1 push
.\run.ps1 test                    # FAIL: 2 of 36, the leaf-03/leaf-04 session is down
.\run.ps1 day2 revert bad-asn
.\run.ps1 generate; .\run.ps1 push
.\run.ps1 test                    # PASS
```

If there is time, drift: shut a port by hand, and `diff` finds it (see [[Day 2 Operations]]).

Land: the test report is the "we know this worked" evidence Brian described, and a failed one is just as useful in the ticket.

## After

```powershell
.\run.ps1 plants-alert reset --yes     # also clears the poller's simulated outages
.\run.ps1 plants-change reset --yes    # also undoes the level-1 changes
.\run.ps1 plants-scan all --forget     # only needed if you ran a scan with --apply
.\run.ps1 day2 revert all
.\run.ps1 generate; .\run.ps1 push; .\run.ps1 test
```

## Notes to be honest about

- **DHCP leases.** `plants-l1 dhcp` reads usage; it does not clear leases. Check the current Meraki API before promising that a scope can be cleared remotely; a shorter lease time or a wider scope is the change we know of.
- **The mocks.** The Meraki dashboard, its webhook, its client counts and its uplink usage are mocks with the real API's shape. Nothing has been run against their organisation.
- **The poller and the scan.** Both decide what answers from the data file. NetBox decides what to check and what goes on the ticket or report; the asking would be ping, SNMP or nmap from a box at each site, or their RMM.
- **Ticket history and circuits.** The earlier `ZD-...` tickets, the providers, accounts and phone numbers are made up.
- **OT flows.** NetBox records which server a machine depends on, not the ports. The flows are a draft for the project, not firewall rules.
- **Juniper and Ubiquiti.** They are in NetBox and the (mock) poller watches them, but nothing reads their configuration or pushes to them yet.

## If something goes wrong

| Symptom | Fix |
|---------|-----|
| `Not approved yet` | Approve all four people the plan listed. |
| `outside the window` on apply | Add `--as-of "2026-10-25 02:00"`. |
| A port shows an unexpected "before" VLAN | A previous run was not reset: `plants-change reset --yes`. |
| `verify` still fails after `--clear` | The `--clear` was for the other fault type: `fault us-hq-ap03 --clear` (offline) or `--no-clients --clear`. |
| `poll` opens no circuit or DHCP ticket | One is still open from a rehearsal: `plants-alert reset --yes`. |
| The access point ticket has no "Last fix" | The `resolve` was for another incident id: `plants-alert list`. |
| Statuses or serials look wrong after `plants` | Run `plants-meraki --apply` again. |
| `push` prints FAIL | Run it again for that switch (`.\run.ps1 push leaf-03`); it prints the lines that would not apply. |
| `test` fails before Act 6 starts | `.\run.ps1 diff`, then `.\run.ps1 push`, then wait 30 s for BGP. |
| Command hangs on start | `docker compose ps`: NetBox must be healthy. |

## What to ask them at the end

- Which ITSM tool (Zendesk?), and can it receive tickets by API or webhook? Can we read past tickets' resolution notes?
- Which monitoring tool would send the alerts, and can it call a webhook? Would they poll the Juniper and Ubiquiti gear from a small box per site, or from NinjaOne?
- Which Juniper and Ubiquiti sites matter first?
- Which plant goes first for OT segmentation, and who owns the line machines there?
- Who really approves for a plant: the plant manager, IT, or both?
- Can we have the onboarding and offboarding process document Norm mentioned, and their circuit list with provider contacts?

---

**Back to:** [[Add-on Plants Demo]] | [[Day 2 Operations]]
