---
marp: true
theme: default
paginate: true
size: 16:9
backgroundColor: #ffffff
color: #1a1a1a
style: |
  section {
    font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif;
    padding: 44px 60px 64px 60px;
    font-size: 20px;
    background-color: #ffffff;
  }
  h1 { color: #0E2E51; font-size: 2.0em; letter-spacing: -0.02em; margin: 0 0 8px; }
  h2 { color: #0E2E51; font-size: 1.42em; letter-spacing: -0.01em; margin: 0 0 6px; }
  h3 { color: #005DA8; font-size: 1.0em; font-weight: 600; margin: 0 0 4px; }
  strong { color: #005DA8; }
  em { color: #5a6b7d; font-style: normal; }
  table { font-size: 0.76em; border-collapse: collapse; width: 100%; }
  th { background: #0E2E51; color: #fff; text-align: left; padding: 8px 10px; font-weight: 600; }
  td { padding: 7px 10px; border-bottom: 1px solid #dde3ea; vertical-align: top; }
  tr:nth-child(even) td { background: #f5f8fb; }
  blockquote {
    border-left: 4px solid #009FDC; background: #f0f7fc;
    color: #33475b; padding: 12px 18px; margin: 16px 0;
    font-size: 0.92em;
  }
  ul { margin: 6px 0; padding-left: 22px; }
  li { margin: 5px 0; }
  .lead { text-align: center; justify-content: center; }
  .lead h1 { font-size: 2.5em; }
  .small { font-size: 0.66em; color: #7a8899; }
  .cols { display: flex; gap: 34px; }
  .cols > div { flex: 1; }
  section.divider {
    justify-content: center;
    text-align: center;
    background-color: #0E2E51 !important;
  }
  section.divider h1, section.divider h2 { color: #ffffff; }
  section.divider p { color: #9fc4e0; }
  section.divider::after { color: #6f8ba6; }
  section::after {
    content: "Confidential — Proprietary to AHEAD  ·  " attr(data-marpit-pagination) " / " attr(data-marpit-pagination-total);
    font-size: 11px; color: #8a99a8;
  }
  .stage { display: flex; gap: 10px; margin-top: 14px; }
  .stage div {
    flex: 1; background: #f0f7fc; border-top: 3px solid #009FDC;
    padding: 10px 12px; font-size: 0.74em; color: #33475b;
  }
  .stage div b { display: block; color: #0E2E51; font-size: 1.08em; margin-bottom: 3px; }
  .kicker { color: #009FDC; font-size: 0.78em; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase; margin-bottom: 6px; }
---

<!-- _class: lead -->

# Network Automation

### From strategy to execution — and what "done" looks like

<p class="small" style="margin-top:26px">
AHEAD Network Automation Practice &nbsp;·&nbsp; with NetBox Labs<br>
Executive briefing &nbsp;·&nbsp; November 2026
</p>

---

## Where we are on the journey

<p class="kicker">FMOLHS Network Modernization</p>

| Workstream | Status |
| --- | --- |
| Current State | **96% complete** |
| Future State | 57% complete |
| Transition State | 22% complete |
| **Automation** | **1% — the work in front of us** |

We have spent four months building the picture. This session is about what we *do* with it.

> The strategy closes 12/31. The delivery portfolio — wireless, switch refresh, SD-WAN —
> runs through 2027+. **Automation is the thread that connects them.**

---

## This is a configuration problem, not a change-management problem

<div class="cols">
<div>

### ServiceNow
Answers *what changed, and who approved it*

- Change records, incidents, workflow
- Holds **process** state
- **You already have this, and it stays**

</div>
<div>

### Network source of truth
Answers *what should this device be*

- Intended state, per port, per VLAN
- Machines can read it and build from it
- **This is the missing piece**

</div>
</div>

> We are not proposing to replace your CMDB or your IPAM. We are proposing the thing
> automation needs to read — and the integration that keeps all three honest.

<p class="small">AHEAD's position: the source of truth must integrate with the CMDB, not compete with it.</p>

---

## Automation is a journey, not a product

<p class="kicker">Voice of AHEAD</p>

Four categories of automation work. Four different processes. **One shared source of truth.**

<div class="stage">
<div><b>1 · Zero Day</b>Build. New hardware, new sites, zero-touch provisioning.</div>
<div><b>2 · Day 2</b>Operate. The day-to-day change: VLANs, ports, policy.</div>
<div><b>3 · Lifecycle</b>Sustain. Upgrades, CVEs, staying current.</div>
<div><b>4 · Troubleshooting</b>Recover. Detect, isolate, remediate.</div>
</div>

> You cannot do any of them well until the network's intent lives somewhere a machine
> can read it. That is why every automation program starts at the same place.

---

## Every category starts read-only, and earns the right to act

<p class="kicker">The progression we recommend — and it is deliberate</p>

| Category | Read-only | Assisted | Automated *(only after gates)* |
| --- | --- | --- | --- |
| **Lifecycle** | Compare NetBox, ServiceNow, and the network | Review mismatches and candidates | Update CMDB or lifecycle workflow after the owners approve |
| **Day 2** | Report compliance and drift | Review diffs and proposed remediation | Automate only within an approved change path |
| **Zero Day** | Validate generated template output | Engineer reviews the proposed config | Deploy approved templates after a Standard change qualifies |
| **Troubleshooting** | Collect read-only evidence | Enrich the ticket; suggest checks | Automate diagnostics once triggers are validated |

> Nobody starts at "automated." You start by **proving the data**, then by **proposing**
> a change, then — only once the change path is approved — by acting.

---

<!-- _class: divider -->

## Let me show you, not tell you

<p>Day 2 and Lifecycle, running live, in a lab</p>

---

## The change you are making right now

> *"They're bringing in a new Philips system. It connects to the wireless, and they need to
> reallocate a new VLAN — across a series of trunks, to a trunk with a bunch of other VLANs
> on it already."*

**Today:** a change request, a window, several switches, and hoping nobody missed one.

**What we will show:** the change declared once, the tooling works out the rest, and a
page proves it landed on every device.

<p class="small">This is real. It is the same pattern whether the vendor is Philips, a new imaging suite, or a badge system.</p>

---

## What you will see

<div class="stage">
<div><b>Intent</b>What the network should be, from the source of truth.</div>
<div><b>Drift</b>Where the devices disagree with it — right now.</div>
<div><b>Preview</b>Exactly what would change. Nothing applied.</div>
<div><b>Approval</b>A named human, on the record.</div>
<div><b>Proof</b>The device confirms it took.</div>
</div>

Then the same loop again, on a switch we break on purpose.

<p class="small" style="margin-top:14px">No terminal on screen. This is what your team would use.</p>

---

## And the lifecycle half

<p class="kicker">The CVE and upgrade conversation</p>

The same source of truth answers a second question: **which devices are not doing what we
said, and what are they running?**

- Compliance state per device, not a spreadsheet exercise
- Software version read from the switch, not from a document
- Set a target version → this becomes your upgrade worklist

> This is the front half of the lifecycle problem: **knowing what you have.** The back half —
> zero-downtime upgrades — is a platform decision, and it is already on our roadmap.

---

## What this changes about your switch refresh

Your refresh starts Q1 2027. The order matters more than the date.

<div class="stage">
<div><b>Before</b>Model the estate. Agree VLANs, addressing, standards.</div>
<div><b>During</b>Render every device from that model. Zero-touch the build.</div>
<div><b>After</b>Prove compliance on day one, not month six.</div>
</div>

> A refresh done without a source of truth just replaces old drift with new drift.
> **The model is what makes the refresh stick.**

---

## What this is not

Being straight about the edges, because the small claim is the one you can trust.

- **Not an IPAM replacement.** Infoblox remains the authority. We integrate.
- **Not a CMDB replacement.** ServiceNow remains the system of record. We complement it.
- **Not a firewall demo today.** Palo Alto fits the same pattern. We are not showing it in 90 minutes.
- **Not a quick win.** Getting 80% of the estate modeled is roughly six weeks. The last 20% is the hard part, and nobody who tells you otherwise has done it.
- **Not production.** This is a lab. Same method, not your network.

---

## Six weeks to something real

<div class="cols">
<div>

**Weeks 1–2**
Reconcile. Catalyst Center, Aruba Central, ServiceNow, spreadsheets — against each other.

**Weeks 3–4**
The model. Sites, racks, devices, circuits, VLANs. Agreed, not assumed.

</div>
<div>

**Weeks 5–6**
The first use case. Day-2 VLAN and port change, through the gate.

**Then**
Lifecycle visibility, then Zero Day. Each on a foundation that already works.

</div>
</div>

> This runs **in parallel** with the automation workshop already scheduled through 12/04 —
> not instead of it. We would rather build the plan with your team than present one to you.

---

## What we would ask for

1. **Access** to the current-state data you already have — Catalyst Center, Aruba Central, the spreadsheets.
2. **A named owner** on your side, so the model reflects reality rather than our assumptions.
3. **One real change** — the Philips VLAN is the obvious candidate — to prove the loop.
4. **Agreement on the gates** — what has to be true before anything acts on its own.

> Start with one change, done properly. It is the only way anyone believes the second one.

---

<!-- _class: lead -->

# Questions

<p class="small" style="margin-top:22px">
Brian Nelson &nbsp;·&nbsp; Principal Consultant, Network Automation<br>
Brian Bradley &nbsp;·&nbsp; Network Advisory<br><br>
Running lab, NetBox, and source references available on request.
</p>
