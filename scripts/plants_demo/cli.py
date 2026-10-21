#!/usr/bin/env python3
"""Plants Demo add-on - optional multi-site / ownership layer for the NetBox lab.

Adds a fictional multi-plant manufacturer (Meraki-centric, plus Juniper and
Ubiquiti) with owners, approvers, change windows, IP plans and cabling ON TOP of
the core Arista fabric lab. It does not modify, and is invisible to, the core
lab's inventory, config generation and ContainerLab topology.

Usage:
    python scripts/plants_demo/cli.py setup                 # model + data + demo RBAC
    python scripts/plants_demo/cli.py setup --link-fabric   # also give DC1 owners/approvers
    python scripts/plants_demo/cli.py audit                 # ownership & lifecycle report
    python scripts/plants_demo/cli.py import-sheet          # validate a CSV asset register
    python scripts/plants_demo/cli.py import-sheet --apply  # ...and import the rows that pass
    python scripts/plants_demo/cli.py meraki-sync           # compare a (mock) Meraki dashboard
    python scripts/plants_demo/cli.py meraki-sync --apply   # ...and write what it observed
    python scripts/plants_demo/cli.py views                 # saved filters in the NetBox UI
    python scripts/plants_demo/cli.py impact us-plt2-sw01   # change-impact card
    python scripts/plants_demo/cli.py triage us-plt2-ap02   # level-1 card for a down device
    python scripts/plants_demo/cli.py change plan us-hq-sw02   # a change: plan/approve/apply/verify
    python scripts/plants_demo/cli.py l1 find 10.2.100.11      # level 1/2: find, reserve-ip, port-vlan, dhcp
    python scripts/plants_demo/cli.py alert simulate us-plt2-ap02   # an alert in, an enriched ticket out
    python scripts/plants_demo/cli.py alert poll                    # watch the Juniper/Ubiquiti gear too
    python scripts/plants_demo/cli.py report                # weekly estate report, every site
    python scripts/plants_demo/cli.py ot                    # OT segmentation readiness
    python scripts/plants_demo/cli.py scan us-plt2          # what answers that NetBox does not know
    python scripts/plants_demo/cli.py remove --yes          # remove the sample data
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.panel import Panel  # noqa: E402

from plants_demo import (  # noqa: E402
    alerts,
    audit,
    cards,
    change,
    common,
    l1,
    meraki_mock,
    meraki_sync,
    model,
    ot,
    populate,
    rbac,
    remove,
    report,
    scan,
    sheet,
    views,
)


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--url", help="NetBox URL (default: $NETBOX_URL)")
    shared.add_argument("--token", help="NetBox API token (default: $NETBOX_TOKEN)")
    shared.add_argument(
        "--plan", type=Path, help="Data file (default: netbox_data/plants_demo.yml)"
    )

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", parents=[shared], help="model + sample data + demo RBAC")
    setup.add_argument("--link-fabric", action="store_true", help="add owners to core site DC1")
    setup.add_argument("--skip-rbac", action="store_true", help="do not create demo users/groups")
    setup.add_argument("--reset-passwords", action="store_true")

    sub.add_parser(
        "model", parents=[shared], help="only the reference data (device types, fields...)"
    )

    pop = sub.add_parser("populate", parents=[shared], help="only the sample sites/devices/owners")
    pop.add_argument("--link-fabric", action="store_true", help="add owners to core site DC1")

    rb = sub.add_parser("rbac", parents=[shared], help="only the demo groups, permissions, users")
    rb.add_argument("--reset-passwords", action="store_true")

    aud = sub.add_parser("audit", parents=[shared], help="ownership & lifecycle report")
    aud.add_argument("--scope", choices=["plants", "all"], default="plants")
    aud.add_argument("--json", action="store_true", help="machine-readable output")
    aud.add_argument("--horizon-days", type=int, default=180, help="upcoming-EOL window")
    aud.add_argument("--fail-on-gaps", action="store_true", help="exit 1 if devices lack an owner")

    imp = sub.add_parser(
        "import-sheet", parents=[shared], help="validate / import a CSV asset register"
    )
    imp.add_argument(
        "file",
        nargs="?",
        type=Path,
        default=sheet.DEFAULT_SHEET,
        help="CSV file (default: the sample register in netbox_data/samples/)",
    )
    imp.add_argument(
        "--apply", action="store_true", help="write the rows that pass (default: dry run)"
    )
    imp.add_argument("--json", action="store_true", help="machine-readable output")
    imp.add_argument("--fail-on-errors", action="store_true", help="exit 1 if any row is rejected")

    sub.add_parser(
        "views", parents=[shared], help="create the saved filters (also done by 'setup')"
    )

    imp_card = sub.add_parser(
        "impact", parents=[shared], help="before a change: owners, approvers, what is downstream"
    )
    imp_card.add_argument("device", help="device name, e.g. us-plt2-sw01")
    imp_card.add_argument("--json", action="store_true", help="machine-readable output")

    tri = sub.add_parser(
        "triage", parents=[shared], help="level-1 card for a device that is down or suspect"
    )
    tri.add_argument("device", help="device name, e.g. us-plt2-ap02")
    tri.add_argument("--json", action="store_true", help="machine-readable output")

    chg = sub.add_parser(
        "change",
        parents=[shared],
        help="a change with approvers, a window, a baseline and evidence (firmware, mock)",
    )
    steps = chg.add_subparsers(dest="action", required=True)
    plan_p = steps.add_parser("plan", help="who approves, what is affected, is the window open")
    plan_p.add_argument("device", help="a Meraki device; its whole product type at the site is in scope")
    plan_p.add_argument("--to", help="firmware version (default: the fleet's current one)")
    plan_p.add_argument("--summary", default="", help="one line for the ticket")
    plan_p.add_argument("--at", help="planned time at the site, YYYY-MM-DD HH:MM (default: now)")
    plan_p.add_argument("--by", default="netops", help="who is asking")
    appr = steps.add_parser("approve", help="record an approval (must be a recorded approver)")
    appr.add_argument("id")
    appr.add_argument("--by", required=True, help="the approver's name")
    app = steps.add_parser("apply", help="refuses until approved and inside the window")
    app.add_argument("id")
    app.add_argument(
        "--override-window", metavar="REASON", help="go ahead outside the window, on the record"
    )
    app.add_argument("--as-of", help="simulated site time, YYYY-MM-DD HH:MM (for demos)")
    app.add_argument(
        "--simulate-fault",
        action="append",
        metavar="DEVICE",
        help="demo lever: this device goes offline after the change (repeatable)",
    )
    app.add_argument(
        "--simulate-no-clients",
        action="append",
        metavar="AP",
        help="demo lever: this access point stays up but no client can join (repeatable)",
    )
    ver = steps.add_parser(
        "verify", help="run the checks, compare with the baseline, write evidence"
    )
    ver.add_argument("id")
    cnf = steps.add_parser("confirm", help="an owner confirms what the dashboard cannot see")
    cnf.add_argument("id")
    cnf.add_argument("--by", required=True, help="the owner's name")
    cnf.add_argument("--note", help="what they checked")
    shw = steps.add_parser("show", help="print the ticket text (or the evidence)")
    shw.add_argument("id")
    shw.add_argument("--evidence", action="store_true")
    flt = steps.add_parser("fault", help="demo lever: a device goes offline (or --clear: back)")
    flt.add_argument("device")
    flt.add_argument("--clear", action="store_true")
    flt.add_argument("--no-clients", action="store_true", help="the AP stays up but no client can join")
    rst = steps.add_parser("reset", help="undo the demo: dashboard, NetBox firmware, records")
    rst.add_argument("--yes", action="store_true")

    l1p = sub.add_parser(
        "l1", parents=[shared], help="level 1/2 runbook actions: find, reserve-ip, port-vlan"
    )
    l1p.add_argument(
        "--as", dest="as_user", metavar="USER", help="run as a demo user, e.g. demo.helpdesk"
    )
    acts = l1p.add_subparsers(dest="action", required=True)
    fnd = acts.add_parser("find", help="what is this IP or device, where is it, who owns it")
    fnd.add_argument("what", help="an IP address or a device name")
    rsv = acts.add_parser("reserve-ip", help="reserve the next free static address in a VLAN")
    rsv.add_argument("site", help="site code, e.g. us-plt2")
    rsv.add_argument("vlan", type=int, help="VLAN id, e.g. 100")
    rsv.add_argument("--for", dest="label", required=True, help="what it is for")
    rsv.add_argument("--dry-run", action="store_true")
    pvl = acts.add_parser("port-vlan", help="move an access port to another VLAN (L2)")
    pvl.add_argument("switch")
    pvl.add_argument("port", help="port number, e.g. 12")
    pvl.add_argument("vlan", type=int)
    pvl.add_argument("--label", help="what is plugged in (port name and NetBox description)")
    pvl.add_argument("--dry-run", action="store_true")
    dhc = acts.add_parser("dhcp", help="how full each DHCP scope at a site is, and what to do")
    dhc.add_argument("site", help="site code, e.g. us-plt2")
    dhc.add_argument("--warn", type=int, default=80, help="percent in use to call 'filling'")
    dhc.add_argument("--full", type=int, default=95, help="percent in use to call 'full'")
    l1_rst = acts.add_parser("reset", help="undo the demo: delete reservations, restore ports")
    l1_rst.add_argument("--yes", action="store_true")

    alp = sub.add_parser(
        "alert", parents=[shared], help="a monitoring alert in, an enriched ticket out (mock)"
    )
    alp.add_argument(
        "--since-hours", type=int, default=72, help="how far back to look for related changes"
    )
    alerts_sub = alp.add_subparsers(dest="action", required=True)
    sim = alerts_sub.add_parser("simulate", help="the mock dashboard raises an alert, then handle it")
    sim.add_argument("device", help="a device in the mock dashboard, e.g. us-plt2-ap02")
    sim.add_argument(
        "--type",
        choices=["down", "dhcp", "uplink"],
        default="down",
        help="down: the device; dhcp: a full scope (MX); uplink: the WAN1 circuit fails (MX)",
    )
    hnd = alerts_sub.add_parser("handle", help="handle a webhook body saved to a file")
    hnd.add_argument("file", type=Path)
    pol = alerts_sub.add_parser(
        "poll", help="one monitoring pass: Juniper/Ubiquiti reachability, circuit usage, DHCP (mock)"
    )
    pol.add_argument("--simulate-down", action="append", metavar="DEVICE", help="demo lever: it stops answering")
    pol.add_argument("--simulate-up", action="append", metavar="DEVICE", help="demo lever: it answers again")
    pol.add_argument("--uplink-pct", type=int, help="circuit usage that opens a ticket (default: data file, 80)")
    res = alerts_sub.add_parser("resolve", help="close an incident, saying what fixed it")
    res.add_argument("id")
    res.add_argument("--fix", required=True, help="what fixed it: the next person to get this alert reads it")
    res.add_argument("--by", help="who fixed it (default: the service desk)")
    one = alerts_sub.add_parser("show", help="one incident's card")
    one.add_argument("id")
    alerts_sub.add_parser("list", help="the incidents so far")
    al_rst = alerts_sub.add_parser("reset", help="delete incidents, undo simulated outages")
    al_rst.add_argument("--yes", action="store_true")

    sub.add_parser(
        "report", parents=[shared], help="weekly estate report: every site on one page (Markdown)"
    )
    otp = sub.add_parser(
        "ot", parents=[shared], help="OT segmentation readiness per plant: unknowns, owners, flows"
    )
    otp.add_argument("site", nargs="?", help="one plant (default: every plant)")
    scn = sub.add_parser(
        "scan", parents=[shared], help="what answers on a site's networks vs what NetBox records (mock)"
    )
    scn.add_argument("site", help="a site code, or 'all'")
    scn.add_argument("--apply", action="store_true", help="record unknown addresses (tagged scan-found)")
    scn.add_argument("--forget", action="store_true", help="delete the addresses earlier scans recorded")

    mer = sub.add_parser(
        "meraki-sync",
        parents=[shared],
        help="sync Meraki dashboard facts into NetBox (a built-in mock unless --base-url)",
    )
    mer.add_argument("--base-url", help="Meraki API base URL (default: the built-in mock)")
    mer.add_argument("--api-key", help="Meraki API key (default: $MERAKI_API_KEY)")
    mer.add_argument("--org-id", default=None, help="organization id (default: $MERAKI_ORG_ID)")
    mer.add_argument(
        "--apply", action="store_true", help="write the changes (default: validate only)"
    )
    mer.add_argument("--json", action="store_true", help="machine-readable output")
    mer.add_argument("--fail-on-drift", action="store_true", help="exit 1 if anything drifted")
    mer.add_argument(
        "--mock-rate-limit",
        type=int,
        default=0,
        metavar="N",
        help="make the built-in mock answer HTTP 429 on every Nth call (shows retries)",
    )

    srv = sub.add_parser(
        "meraki-mock",
        parents=[shared],
        help="run the mock Meraki API on its own, for use with --base-url or curl",
    )
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8081)
    srv.add_argument("--api-key", default=meraki_mock.DEFAULT_KEY)
    srv.add_argument("--page-cap", type=int, default=30, help="max items per page")

    rem = sub.add_parser("remove", parents=[shared], help="remove the add-on's data")
    rem.add_argument("--yes", action="store_true", help="actually delete (otherwise dry run)")
    rem.add_argument("--purge-model", action="store_true", help="also remove reference data")
    return parser


def _serve_mock(plan: dict, args: argparse.Namespace) -> int:
    """Run the mock Meraki API until interrupted."""
    world = meraki_mock.build_world(plan)
    server = meraki_mock.MockMeraki(
        (args.host, args.port), world, api_key=args.api_key, page_cap=args.page_cap
    )
    common.console.print(
        f"[bold]Mock Meraki API[/bold] (not a real dashboard) on http://{args.host}:{args.port}"
        f"/api/v1\n  organization {world['org']['id']}: {len(world['networks'])} networks, "
        f"{len(world['devices'])} devices\n  API key: {args.api_key}   (Ctrl+C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "json", False):
        common.console.quiet = True  # keep stdout clean for piping

    plan = common.load_plan(args.plan)
    if args.command == "meraki-mock":
        return _serve_mock(plan, args)  # needs no NetBox
    nb = common.connect(args.url, args.token)
    errors = 0

    if args.command in ("setup", "model"):
        errors += model.ModelBuilder(nb, plan).run().errors
    if args.command in ("setup", "populate"):
        errors += populate.Populator(nb, plan).run(link_fabric=args.link_fabric).errors
    if args.command == "rbac" or (args.command == "setup" and not args.skip_rbac):
        errors += rbac.RbacBuilder(nb, plan).run(reset_passwords=args.reset_passwords).errors

    if args.command == "setup" or args.command == "views":
        errors += views.run(nb, plan).errors
    if args.command == "impact":
        return cards.run_impact(nb, plan, args.device, args.json)
    if args.command == "triage":
        return cards.run_triage(nb, plan, args.device, args.json)

    if args.command == "change":
        return change.run(nb, plan, args)
    if args.command == "l1":
        return l1.run(nb, plan, args)
    if args.command == "alert":
        return alerts.run(nb, plan, args)
    if args.command == "report":
        return report.run(nb, plan)
    if args.command == "ot":
        return ot.run(nb, plan, args.site)
    if args.command == "scan":
        return scan.run(nb, plan, args.site, args.apply, args.forget)

    if args.command == "audit":
        return audit.run(
            nb,
            plan,
            scope=args.scope,
            as_json=args.json,
            horizon_days=args.horizon_days,
            fail_on_gaps=args.fail_on_gaps,
        )
    if args.command == "import-sheet":
        return sheet.run(nb, plan, args.file, args.apply, args.json, args.fail_on_errors)
    if args.command == "meraki-sync":
        return meraki_sync.run(
            nb,
            plan,
            base_url=args.base_url,
            api_key=args.api_key,
            org_id=args.org_id or os.environ.get("MERAKI_ORG_ID"),
            apply=args.apply,
            as_json=args.json,
            fail_on_drift=args.fail_on_drift,
            mock_rate_limit=args.mock_rate_limit,
        )
    if args.command == "remove":
        errors += remove.run(nb, plan, args.yes, args.purge_model).errors

    if args.command == "setup":
        common.console.print(
            Panel.fit(
                "[bold green]Plants Demo add-on ready[/bold green]\n"
                "Run the report:  cli.py audit\n"
                "Undo it later:   cli.py remove --yes",
                border_style="green",
            )
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
