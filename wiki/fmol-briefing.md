---
marp: true
theme: default
paginate: true
size: 16:9
backgroundColor: #0f1419
color: #e6edf3
style: |
  section {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 48px 64px;
  }
  h1 { color: #ffffff; font-size: 2.1em; letter-spacing: -0.02em; }
  h2 { color: #ffffff; font-size: 1.55em; letter-spacing: -0.01em; }
  h3 { color: #93a1b0; font-size: 1.05em; font-weight: 500; }
  strong { color: #4da3ff; }
  em { color: #93a1b0; }
  table { font-size: 0.78em; }
  th { color: #93a1b0; border-bottom: 1px solid #2a3644; }
  td { border-bottom: 1px solid #1d2733; }
  blockquote {
    border-left: 3px solid #4da3ff;
    color: #93a1b0;
    font-style: normal;
    padding-left: 18px;
    margin: 18px 0;
  }
  .lead { text-align: center; }
  .lead h1 { font-size: 2.4em; }
  .lead p { color: #93a1b0; font-size: 1.15em; }
  .small { font-size: 0.72em; color: #93a1b0; }
  .cols { display: flex; gap: 40px; }
  .cols > div { flex: 1; }
  section.divider { justify-content: center; text-align: center; }
  section.divider h2 { font-size: 2em; }
  .tag {
    display: inline-block; background: #1d2733; color: #4da3ff;
    border-radius: 4px; padding: 1px 7px; font-size: 0.62em;
    vertical-align: middle; margin-left: 8px; letter-spacing: 0.03em;
  }
---

<!-- _class: lead -->

# From spreadsheets to a source of truth

### What network automation actually looks like, and where the value lands

<p class="small">AHEAD · Network Automation Practice &nbsp;|&nbsp; with NetBox Labs</p>

---

## Why we're here

Your network is **well architected and well executed**. This isn't a rescue.

The problem is narrower, and harder:

> Doing changes fast, and keeping them accurate — across three vendors, a hospital's
> change calendar, and an audit trail.

That is a **configuration** problem, not a **change-management** problem.

---

## The distinction that matters

<div class="cols">
<div>

### A CMDB
- Answers *what changed, and who approved it*
- Change records, incidents, workflow
- Holds **process** state

</div>
<div>

### A configuration source of truth
- Answers *what should this device be*
- Holds **intended** state, per port, per VLAN
- Machines can read it and render from it

</div>
</div>

> You already have the first. The second is what automation needs, and it's the one
> nobody owns today.

<p class="small">This is why NetBox is complementary to ServiceNow and Infoblox — not a replacement for either.</p>

---

## Automation has four stages

<div class="cols">
<div>

**1. Day zero — build**
Zero-touch provisioning. A device arrives, comes online, configured from intent.

**2. Day two — operate**
The change you're making today: a new VLAN, a new system, a new port.

</div>
<div>

**3. Lifecycle — sustain**
Upgrades and CVEs. Proving an image won't break the clinic before you push it.

**4. Troubleshooting — recover**
Something broke. Isolate, remediate, find root cause.

</div>
</div>

> Four different processes. Four different payoffs. **One shared source of truth.**

---

## Where your pain actually sits

| Stage | Your situation |
| --- | --- |
| Day zero | Not the problem — you're not greenfield |
| **Day two** | **The Philips VLAN. Trunks, ClearPass, "just go."** |
| **Lifecycle** | **Ten-year-old gear, CVEs you can't see, no outage window** |
| Troubleshooting | Real, but you asked about change speed |

Focus today: **day two and lifecycle** — the two Brian flagged.

---

<!-- _class: divider -->

## Let me show you, not tell you

<p class="small" style="color:#93a1b0">Everything from here is running, live, in a lab.</p>

---

## The lab, honestly

- Six Arista switches in software, real EOS, running now
- NetBox as the source of truth
- A web console — **no terminal on screen**, because a CLI demo proves nothing to a CTO

> This is a lab. It is not a copy of your network. We're showing the method,
> not a running system in your estate.

---

## The change you have today

> *"They need to reallocate a new VLAN across a series of trunks. Switch port VLAN add,
> to a trunk with a bunch of other VLANs on it already."*

**Today:** a change request, a window, four or five switches, and hoping someone didn't
miss one.

**What we'll show instead:** one object in the source of truth, the tools work out
the rest, and the page proves it landed.

---

## What you'll see in the next ten minutes

| You'll see | What it proves |
| --- | --- |
| The fabric as intended vs actual | Intent is a thing a machine can read |
| A new VLAN previewed, not applied | Change is reviewable before it's real |
| A named approval to push it | Nothing happens without a human |
| A switch broken on purpose, then fixed | Drift is *detected*, not discovered later |
| Every change on an audit record | Your CISO gets her documentation |
| The same intent, two vendors | The label on the box stops mattering |

<p class="small">Then a short version of the same thing for CVE and upgrade work.</p>

---

## What this does not do

Being straight about the edges, because we'd rather you trust the small claim:

- **No IPAM replacement.** Infoblox stays. We integrate.
- **No CMDB replacement.** ServiceNow stays. We complement it.
- **No firewall demo today.** Palo Alto is where your security budget sits, and it fits
  this pattern — we're just not showing it in 90 minutes.
- **No magic.** Getting 80% of your estate into a trustworthy model is a six-week job.
  The last 20% is the hard part, and nobody who tells you otherwise has done it.

---

<!-- _class: divider -->

## Demo

<p class="small" style="color:#93a1b0">Fabric · Day-two change · Lifecycle · Two vendors · Audit trail</p>

---

## After the demo — what a first six weeks looks like

<div class="cols">
<div>

**Weeks 1–2**
Current state. Discovery, reconciliation, the parts that disagree.

**Weeks 3–4**
The model. Sites, racks, devices, circuits, VLANs — in NetBox, agreed.

</div>
<div>

**Weeks 5–6**
The first automation. Start where the pain is: day-two VLAN changes.

**Then**
Lifecycle and CVE reporting on top of data you now trust.

</div>
</div>

---

## What we'd ask for

1. **Access** to the current-state data you have — Catalyst Center, Aruba Central, your spreadsheets.
2. **A named owner** on your side for the model, so it reflects reality.
3. **One real change** — the Philips VLAN is a perfect candidate — to prove the loop.

> We can start small and still show something real in six weeks.

---

<!-- _class: lead -->

# Questions

<p class="small">Brian Nelson · Principal Consultant, Network Automation<br/>
Brian Bradley · Network Advisory</p>
