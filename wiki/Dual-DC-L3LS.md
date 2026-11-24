# Dual-DC L3LS (AVD track)

A second lab, kept beside the NetBox workshop rather than folded into it.

The NetBox track answers "what if NetBox is the source of truth and Ansible templates render one fabric?" This track answers "what if Arista AVD renders two fabrics and an FRR router stitches them together?"

The files live in [`labs/l3ls-dual-dc`](../labs/l3ls-dual-dc). Read [`WORKSHOP.md`](../labs/l3ls-dual-dc/WORKSHOP.md) for how the two tracks stay apart, then [`day0.md`](../labs/l3ls-dual-dc/day0.md) for the lab itself.

## Why it is not generated from NetBox

`scripts/netbox_to_avd.py` and `avd/group_vars/` already project the **single** 2-spine/4-leaf fabric into AVD. The dual-DC design adds a second site, border leaves, an EVPN gateway, and an FRR node that AVD does not render natively (`templates/frr.conf.j2` does). Teaching that design by rewriting the NetBox model would hide the AVD data model the lab exists to show. The two tracks share the Ansible container and ContainerLab runner, and nothing else.

## Commands

From the repository root:

```powershell
.\run.ps1 dualdc build
.\run.ps1 dualdc start
.\run.ps1 dualdc apply
.\run.ps1 dualdc stop
```

`build` only renders configs. `start` creates the containers. `apply` pushes the rendered EOS configs with `Cli` and reloads FRR. Re-run `build` and `apply` after each Day 0 edit.
