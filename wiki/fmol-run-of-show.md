# FMOL executive briefing — run of show

**90 minutes.** CIO, CISO (Brittany), CTO (Chris Newsome), Network Director (Chris Hare), maybe
architects. Brownfield. Security-conscious, HIPAA, audit-heavy. No automation platform today.

Structure per Brian Bradley: **NetBox Labs first, then Brian Nelson's overview + demo, then Q&A.**

---

## The shape

| Time | Who | What |
| --- | --- | --- |
| 0:00–0:05 | Brian Bradley | Open, intros, set the room up |
| 0:05–0:25 | NetBox Labs (Ben / Rich) | The platform, at exec altitude — no device screens |
| 0:25–0:40 | Brian Nelson | The four-stage framework and why the value is in day-two and lifecycle |
| 0:40–0:55 | Brian Nelson | **The live demo** |
| 0:55–1:05 | Brian Nelson | Six weeks to value, what we'd ask for |
| 1:05–1:30 | All | Q&A |

Have the lab **already running and warm** before the call starts. Do not start containers on
screen.

---

## Opening (Brian Bradley) — 5 min

Set expectations and get the disclaimer out early, not as an afterthought:

> "What we're going to show you is running in a lab, not in your environment. We're showing
> the method and the art of the possible. The real work starts when we have your data."

Then hand to NetBox Labs.

---

## NetBox Labs — 20 min

Per the team's own plan: platform story, at altitude. Assurance, discovery, change management,
branching, visual explorer, and where it ties into Infoblox and ServiceNow.

**The trap Ben flagged and we must avoid:** do not show device creation or prefix allocation.
Nobody in that room cares how a prefix gets created. Show *value*.

The one line that matters most for this audience:

> "You're a ServiceNow shop. We're not asking you to leave it. NetBox is the configuration
> source of truth your automation reads — ServiceNow stays your change system of record."

---

## Handover to Brian Nelson — where the deck starts

The deck (`wiki/fmol-briefing.md`, rendered to `output/fmol-briefing.{html,pptx}`) picks up
exactly here. Slide order:

1. **From spreadsheets to a source of truth** — title
2. **Why we're here** — validate their network, then name the real problem
3. **The distinction that matters** — CMDB vs configuration source of truth ← *the slide that
   earns the meeting; it's what stops the CISO thinking we're competing with ServiceNow*
4. **Automation has four stages** — the framework Brian defined on the call ← *Ben and Chris
   explicitly asked for this, so do not skip it*
5. **Where your pain actually sits** — names their situation back to them
6. **Let me show you, not tell you** — divider
7. **The lab, honestly** — the disclaimer, again, deliberately
8. **The change you have today** — the Philips quote, verbatim
9. **What you'll see in the next ten minutes** — set the expectation
10. **What this does not do** — the honesty slide ← *CISO credibility is won or lost here*
11. **Demo** — divider, then switch to the browser
12. **Six weeks to value**
13. **What we'd ask for**
14. **Questions**

### The demo itself

Follow `wiki/Autonomy-Workbench.md`. The 15-minute version, in order:

1. **Fabric** tab — intended vs actual, all green
2. **Day-two change** — preview VLAN 150, read the diff aloud, approve with your name
3. **Fabric** — leaf-01 green, others still pending (say why that's deliberate)
4. **Day-two change** — inject drift, back to Fabric to see red, remediate, approve
5. **Lifecycle** — the CVE conversation, per device
6. **Two vendors** — the CTO's point; state the FRR limit out loud
7. **Audit trail** — the CISO's documentation answer

**If the lab fails:** the deck stands alone, and `python -m workbench.cli walkthrough` proves the
flow. Say so and move to the six-week slide. Do not debug a container in front of a CIO.

---

## The five questions to expect, and the answers

**"Are you replacing ServiceNow?"**
No. It stays your change system of record. NetBox holds configuration intent — what a device
should be, per port. Different question, different data.

**"Are you replacing Infoblox?"**
No. Infoblox stays the IPAM authority. We integrate. NetBox can hold prefixes; nobody is asking
you to move yours.

**"How long until we see value?"**
Six weeks to 80% of the estate modelled and one real change automated. The last 20% is the hard
part and we won't pretend otherwise.

**"What about our firewalls?"**
Palo Alto fits the same pattern — intent, approval, audit. We're not demoing it today because
90 minutes is 90 minutes.

**"Who owns the data?"**
You do. It lives in your NetBox, in your Git, under your change control.

---

## Things to not say

- Don't say "single pane of glass." Nobody in that room wants another one.
- Don't say "zero downtime" about anything we haven't proven. The CTO's north star is zero-downtime
  hospital; that's a switching-platform property, not ours, and conflating them is how we lose
  credibility with a CTO who already knows the difference.
- Don't demo a CLI. Brian Nelson was explicit: stay away from CLI in this room.
- Don't overclaim the FRR side. Read-and-diff, applied via its own reload path. Say it plainly.
- Don't say "it just works." Say what it does and what it doesn't.

---

## Before you dial in

```powershell
.\run.ps1 up                     # NetBox stack
.\run.ps1 deploy                 # the switches
.\run.ps1 apply                  # push configs
.\run.ps1 workbench-walk         # proves the flow, no lab needed
.\run.ps1 workbench-check        # the real rehearsal — must say READY
.\run.ps1 workbench              # leave this open on the Fabric tab
```

Open the deck in another window. Test screen-share with both. Then close everything you don't need.
