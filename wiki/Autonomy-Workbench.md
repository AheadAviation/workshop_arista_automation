# The Autonomy Workbench — the FMOL executive demo

A browser page that shows a hospital chain's network the way their executives need to see
it: what NetBox says the network should be, what the switches actually are, where those
differ, and one approved action that closes the gap.

Built for a 90-minute executive briefing. No terminal on screen, no AWX, no ticketing
system, no new containers.

**Provenance:** `INIT-001-autonomy-demo`.

---

## The 15-minute demo

The meeting is 90 minutes; this is your 15. Everything else in the room is NetBox Labs
talking and Q&A.

### Before they join (5 minutes)

```powershell
.\run.ps1 up                     # the NetBox stack
.\run.ps1 deploy                 # the six cEOS switches
.\run.ps1 apply                  # push the generated configs
.\run.ps1 workbench-walk         # proves the demo flow, needs no lab (start here)
.\run.ps1 workbench-check        # the rehearsal against the real lab — must say READY
.\run.ps1 workbench              # the page
```

`workbench-walk` is the one to run first, and again if anything feels wrong: it walks all
sixteen steps of the demo against a stand-in fabric, so it separates "the demo logic is
broken" from "the lab is down". `workbench-check` is the one that answers "is the lab up".

Open <http://127.0.0.1:8099>. Leave it on the **Fabric** tab.

If `workbench-check` says NOT READY it names the exact problem and the command that fixes
it. Fix it and run it again; do not start the meeting on a NOT READY.

### 1. The premise — "this is your network, as data" (2 min)

**Fabric** tab. Six devices, each card showing intended versus actual.

> "Everything you see here comes from two places: NetBox, which is what we *said* the
> network should be, and the switches themselves, which is what it *is*. Green means they
> agree. This is the whole idea — one place that knows what your network is meant to look
> like, and can prove it."

Point at a card. Note the software version row — you will come back to it.

### 2. The change they actually have (5 min)

The story from the call: a Philips clinical system arrives, needs a new VLAN, and that VLAN
has to land on a series of trunks.

**Day-two change** tab.

1. Switch: `leaf-01`. VLAN: `150`. Click **Preview change**.
2. *Nothing has happened yet.* Read the diff aloud: one VLAN created, and the allow-list
   added on `Ethernet5` **and** `Ethernet6` — both members of the server bond.
3. > "This is the answer to the thing you said in our last conversation. Today that is a
   > change request, a maintenance window, and someone typing on four switches and hoping
   > they got all of them. Here it is one object in the source of truth, and the tools work
   > out the rest."
4. Type your name in **Approved by**. Click **Approve and apply**.

### 3. The proof (3 min)

Back to **Fabric**. `leaf-01` is green. The others still show the change pending — which is
honest, and worth saying out loud:

> "The other leaves still need it. That is deliberate: I approved one switch, not the
> estate. When you want all of them, that is a policy decision you make, not a side effect
> of a button."

### 4. The loop — and the CVE conversation (5 min)

Still on **Day-two change**.

1. Click **Inject drift**. This is a *real* configuration change on a *real* switch.
2. Switch to **Fabric**. `leaf-01` is red, with the exact port and the exact missing VLAN.
   > "Nobody told the system. It found it."
3. Back to **Day-two change**, click **Remediate drift**, type your name, approve. Green
   again.
4. **Lifecycle** tab.
   > "Same engine, the fleet view. Which devices match what we said, and what are they
   > running. This is where your CVE problem stops being a spreadsheet exercise — when you
   > set a target version in NetBox, this becomes your upgrade worklist."

The CISO sentence, if she is in the room:

> "Every one of those changes — including the one I broke on purpose — is in an
> append-only record with my name on it and the switch's configuration before and after."

**Audit trail** tab, to prove it.

### 5. The CTO's point (2 min, optional)

**Two vendors** tab. The same intent, the same gate, an Arista switch and an FRRouting router.

> "You said the vendor label on the box shouldn't matter. This is the same source of truth,
> the same approval, the same record, driving two different vendors. Arista is fully read,
> diffed and applied here. FRR is read and diffed, and applied through its own reload path —
> I'm telling you which parts are done rather than showing you a slide."

That last half-sentence matters more than the demo. Do not skip it.

---

## What it will not do

Say these plainly if asked. A demo that overclaims is worse than a smaller honest one.

### One lab limitation you must know about

On this cEOS image, **ZeroTouch is enabled and blocks persisting the configuration**
(`Cannot copy to startup-config when ZeroTouch is enabled`). The consequence: a change
applied during the demo lives in running-config and will be silently reverted after a few
minutes, and the switch returns to hostname `localhost`.

**What this means for tomorrow:**

- Do the whole demo inside one sitting. The change holds while you're talking; it will not
  survive lunch.
- If you need to re-run, redeploy the lab (`.\run.ps1 workbench-lab`) and start again.
- `workbench live_check` will tell you the truth before you present.

The workbench now **raises** rather than reporting success when the save fails, so you'll
see it in the rehearsal rather than discovering it mid-sentence. Fixing it properly means
disabling ZeroTouch and adjusting the authorization policy on the cEOS image — a lab-image
change, not a workbench one, and not yet done.

- **It does not replace ServiceNow.** It reads and writes configuration intent; your CMDB
  stays your CMDB.
- **It does not replace Infoblox.** It is not an IPAM authority. NetBox can hold prefixes;
  the demo does not ask you to move yours.
- **It does not touch firewalls.** Palo Alto is where your security budget sits and this
  demo does not show it. It *could*, through the same intent-and-approval pattern, but it
  does not today and it will not pretend to.
- **It is a lab.** Not a customer network, and it says so in the footer.
- **The trunk layer is a lab overlay.** The shipped fabric has no server-facing trunks, so
  the demo adds one real server trunk per leaf (`Ethernet5`, in
  `clab/topology-demo.clab.yml`) rather than rewriting the workshop's fabric data.

---

## If something breaks

| Symptom | Do this |
|---|---|
| Page says "Could not read intended state" | `.\run.ps1 up`, wait ~90s, then **Refresh** |
| Page says a device "did not answer" | `.\run.ps1 status`, then `.\run.ps1 deploy` |
| Nothing changes when you approve | Check the audit tab — if it says "nothing was sent", the device already matched |
| `workbench-check` says a preview failed | `.\run.ps1 apply` to get the switches onto the rendered config |
| You want to start over | `.\run.ps1 workbench-reset` — puts the fabric back, **keeps the audit** |

`workbench-reset` deliberately never clears the audit log. A reset returns the lab to its
starting state; it does not rewrite what happened.

---

## Running it twice

```powershell
.\run.ps1 workbench-reset        # fabric back to intended state
```

Then start again at the Fabric tab. The audit log keeps both runs, which is usually a good
thing to show — it is the record of two full change cycles.

---

## Where things live

| Path | What |
|---|---|
| `workbench/contracts.py` | The four shapes everything else agrees on |
| `workbench/intent_source.py` | Reading what the network should be (NetBox, and the AVD vars for FRR) |
| `workbench/device_reader.py` | Reading what a switch actually is |
| `workbench/drift.py` | The comparison — pure, and the heart of the demo |
| `workbench/eos_executor.py` | Preview and apply, via an EOS configuration session |
| `workbench/gate.py` | The approval gate and the audit log |
| `workbench/frr_executor.py` | The second vendor, read and diff |
| `workbench/scenarios.py` | The stories, end to end |
| `workbench/web.py` | The page and its API |
| `workbench/static/` | The page itself — hand-written, no build step |
| `output/workbench/audit.jsonl` | The audit record |

Tests: `python -m pytest tests/test_contracts.py tests/test_drift.py tests/test_device_reader.py tests/test_intent_source.py tests/test_eos_executor.py tests/test_gate.py tests/test_scenarios.py tests/test_frr_executor.py tests/test_web.py tests/test_cli.py -q`
