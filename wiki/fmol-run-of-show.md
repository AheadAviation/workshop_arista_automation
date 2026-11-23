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

The deck is `wiki/fmol-briefing.md`, rendered to `output/fmol-briefing.pptx` (14 slides).
It is built on **AHEAD's own frameworks**, not invented ones — the four categories and the
read-only → assisted → automated progression are Brian's, from the Takeda roadmap and
Ryan Alt's workshop deck. Slide order:

1. **Network Automation** — title
2. **Where we are on the journey** — current 96%, future 57%, **automation 1%** ← *uses their
   real project numbers; opens with credibility instead of a pitch*
3. **Configuration problem, not change-management** — ServiceNow vs source of truth ← *the
   slide that stops the CISO thinking we compete with their CMDB*
4. **Automation is a journey, not a product** — the four categories ← *Ben and Chris explicitly
   asked for this on the call*
5. **Read-only → assisted → automated** — the gated progression ← *the most important slide in
   the deck; it is why a security-conscious audience can say yes*
6. **Let me show you** — divider, then switch to the browser
7. **The change you are making right now** — the Philips quote, verbatim
8. **What you will see** — sets expectations
9. **The lifecycle half** — the CVE conversation
10. **What this changes about your switch refresh** — ties to their Q1 2027 refresh
11. **What this is not** — the honesty slide
12. **Six weeks to something real** — the plan
13. **What we would ask for**
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
should be, per port. Different question, different data. Slide 3 is this answer.

**"Are you replacing Infoblox?"**
No. Infoblox stays the IPAM authority. We integrate. Nobody is asking you to move yours.

**"How long until we see value?"**
Six weeks to 80% of the estate modeled and one real change automated. The last 20% is the hard
part and we won't pretend otherwise.

**"What about our firewalls?"**
Palo Alto fits the same pattern — intent, approval, audit. Not demoed today; 90 minutes is 90
minutes.

**"Who owns the data?"**
You do. In your NetBox, your Git, your change control.

### Two they may not ask, but you should raise

**"When does anything act on its own?"**
Not until you approve the gates. Slide 5 is the answer: read-only first, then assisted (the
system proposes, a human approves), and automated only inside a change path you have signed
off. That is deliberate, and it is the reason a CISO can say yes.

**"We're already mid-refresh. Does this wait?"**
No — it runs alongside. Your switch refresh starts Q1 2027, and a refresh done without a model
just replaces old drift with new drift (slide 10).

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
