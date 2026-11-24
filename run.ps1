# =============================================================================
# NetBox Arista ContainerLab - Helper Script (PowerShell)
# =============================================================================
# Usage:
#   .\run.ps1 up              # Start NetBox stack
#   .\run.ps1 import          # Import data to NetBox
#   .\run.ps1 generate        # Generate configs
#   .\run.ps1 deploy          # Deploy ContainerLab
#   .\run.ps1 push            # Push config changes
#   .\run.ps1 destroy         # Destroy lab
#   .\run.ps1 down            # Stop NetBox stack
#   .\run.ps1 shell           # Open Ansible shell
#   .\run.ps1 logs            # View NetBox logs
# =============================================================================

param(
    [Parameter(Position=0)]
    [string]$Command = "help",

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$Rest = @()
)

# Continue (not Stop): docker writes progress such as "Container ... Running" to stderr,
# and Windows PowerShell 5.1 turns that into a terminating error under Stop.
$ErrorActionPreference = "Continue"

function Show-Help {
    Write-Host @"
NetBox Arista ContainerLab - Helper Commands

Usage: .\run.ps1 <command>

Infrastructure:
  up          Start NetBox stack (NetBox, PostgreSQL, Redis)
  down        Stop NetBox stack
  down-v      Stop and remove all data (volumes)
  logs        View NetBox logs
  status      Show container status

Automation (runs in Ansible container):
  import      Import fabric topology to NetBox
  generate    Generate Arista configs from NetBox
  inventory   Show Ansible inventory from NetBox
  shell       Open interactive Ansible shell

ContainerLab:
  deploy      Deploy ContainerLab topology (via clab runner container)
  apply       Apply generated configs to running nodes (live, keeps links)
  agent       Run the ZTP host agent (GUI Deploy button -> stands up devices)
  destroy     Destroy ContainerLab topology

Portal:
  collectstatic  Refresh the ZTP portal CSS/JS after editing them

Day 2 Operations:
  day2 <list|apply|revert|status> [scenario]  Make a documented NetBox change (and undo it)
  diff        Preview what a push would change on each switch (applies nothing)
  push        Push config changes to running devices
  test        Run the validation suite (reachability, BGP, MLAG, routes, VXLAN)
  backup      Save each switch's running config to its flash (for rollback)
  rollback    Restore each switch from that saved config
  pipeline    Run full pipeline (generate + deploy)

Dual-DC AVD track (labs/l3ls-dual-dc — does not touch the NetBox fabric):
  dualdc build    Render both sites and frr-core1 with AVD
  dualdc start    Deploy the 21-node ContainerLab topology
  dualdc apply    Push rendered EOS configs, then reload FRR
  dualdc stop     Destroy the dual-DC topology

Optional add-on - Autonomy Workbench (executive demo; see wiki/Autonomy-Workbench.md):
  workbench          Start the browser page on http://127.0.0.1:8099
  guide              Open the presenter's step-by-step page
  workbench-lab      Deploy the demo switches and prepare the trunk layer (start here)
  eapi               Turn eAPI on so NetBox can read the switches (run after workbench-lab)
  itsm               Start Zammad for the ticket story (http://localhost:8090)
  itsm-down          Stop Zammad, leaving NetBox and its data alone
  tickets            Seed the demo ticket queue (idempotent; --list, --reset)
  verify-change      Prove change control end to end, including the refusals
  verify-service     Prove the deliver-a-service flow: one sentence, one approval, N switches
  clean-fabric       Return the demo trunks to their starting state (--apply)
  ipam               Model the fabric in NetBox IPAM: VLANs, tenants, prefixes, trunks
  verify-ipam        Prove NetBox and the switches agree after a change
  workbench-live     The real rehearsal: walk the demo against the live switches
  workbench-walk     Prove the demo flow with no lab running (no switches needed)
  workbench-check    Is the lab ready to present from?
  workbench-reset    Put the lab back to intended state (the audit log is kept)
  workbench-status   The fabric in the terminal, for a quick smoke test
  deck               Render the briefing slides (output/fmol-briefing.pptx + .html)
  deck-show          Render the slides and open them

Optional add-on - Plants Demo (multi-site, owners, approvers; see wiki/Add-on-Plants-Demo.md):
  plants        Create the sample organisation  (extra args pass through, e.g. --link-fabric)
  plants-audit  Ownership & lifecycle report    (e.g. --scope all, --json, --fail-on-gaps)
  plants-import Validate a CSV asset register; add --apply to import the rows that pass
  plants-views  Create the saved filters in the NetBox UI (also done by 'plants')
  plants-impact <device>  Before a change: owners, approvers, what is downstream, rollout order
  plants-triage <device>  Level-1 card for a device that is down: path, likely cause, who to call
  plants-change <plan|approve|apply|verify|confirm|show|fault|reset> ...  A change with approvers, a window and evidence
  plants-l1 <find|reserve-ip|port-vlan|dhcp|reset> ...  Level 1/2 actions; add --as demo.helpdesk to run as that user
  plants-alert <simulate|poll|handle|resolve|show|list|reset> ...  A monitoring alert in, a ticket with NetBox context out
  plants-report Weekly estate report: every site on one page (output/reports)
  plants-ot [plant]  OT segmentation readiness: unknown devices, wrong VLANs, owners, flows to allow
  plants-scan <site|all>  MOCK network scan vs NetBox; add --apply to record unknown addresses
  plants-meraki Compare a MOCK Meraki dashboard with NetBox; add --apply to write what it saw
  plants-meraki-mock  Run the mock Meraki API on its own (http://localhost:8081, for curl)
  plants-remove Dry-run removal; add --yes to delete (and --purge-model for reference data)

"@
}

switch ($Command.ToLower()) {
    # Infrastructure
    "up" {
        Write-Host "Starting NetBox stack..." -ForegroundColor Cyan
        docker compose up -d
        Write-Host "`nNetBox starting... Wait 1-2 minutes for initialization" -ForegroundColor Yellow
        Write-Host "Access NetBox at: http://localhost:8888" -ForegroundColor Green
        Write-Host "AHEAD ZTP Portal:  http://localhost:8888/plugins/ztp/" -ForegroundColor Green
        Write-Host "Login: admin / admin" -ForegroundColor Green
    }
    
    "down" {
        Write-Host "Stopping NetBox stack..." -ForegroundColor Cyan
        docker compose down
    }
    
    "down-v" {
        Write-Host "Stopping NetBox stack and removing data..." -ForegroundColor Red
        docker compose down -v
    }
    
    "logs" {
        docker compose logs -f netbox
    }
    
    "status" {
        docker compose ps
    }
    
    # Automation
    "import" {
        Write-Host "Importing fabric topology to NetBox..." -ForegroundColor Cyan
        docker compose run --rm ansible python scripts/full_import.py
    }
    
    "bootstrap" {
        Write-Host "Bootstrapping NetBox data model..." -ForegroundColor Cyan
        docker compose run --rm ansible python scripts/bootstrap_netbox.py
    }
    
    "generate" {
        Write-Host "Generating Arista configurations..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
    }
    
    "inventory" {
        Write-Host "Showing Ansible inventory from NetBox..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-inventory --list
    }
    
    "shell" {
        Write-Host "Opening Ansible shell..." -ForegroundColor Cyan
        docker compose run --rm ansible /bin/bash
    }

    "collectstatic" {
        # Refresh the ZTP portal's CSS/JS in the running NetBox after editing them.
        Write-Host "Collecting plugin static into NetBox..." -ForegroundColor Cyan
        docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
        Write-Host "Done. Hard-refresh the browser (Ctrl+F5)." -ForegroundColor Green
    }
    
    # ContainerLab
    "deploy" {
        Write-Host "Deploying ContainerLab topology (via clab runner)..." -ForegroundColor Cyan
        $env:MSYS_NO_PATHCONV = "1"
        docker compose run --rm clab containerlab deploy --reconfigure --topo /clab/topology/topology-docker.clab.yml
        Write-Host "`nNodes are up. Now apply generated configs:  .\run.ps1 apply" -ForegroundColor Yellow
    }

    "agent" {
        # Host-side ZTP agent: watches NetBox for portal Deploy requests and runs
        # generate -> topology -> deploy -> apply. Leave running in a terminal.
        Write-Host "Starting AHEAD ZTP host agent (Ctrl+C to stop)..." -ForegroundColor Cyan
        Write-Host "Use the New Block page in the portal to click Deploy." -ForegroundColor Green
        python scripts/ztp_agent.py
    }

    "apply" {
        # The clab runner (docker-out-of-docker) cannot inject startup-config into
        # nodes at boot (its flash path isn't visible to the host daemon), so we
        # push each generated config into the running node's flash and apply it
        # live with `configure replace` - which keeps the ContainerLab links intact
        # (a docker restart would tear them down).
        Write-Host "Applying output/configs/*.cfg to running ContainerLab nodes..." -ForegroundColor Cyan
        python -c @"
from pathlib import Path
import subprocess
prefix = 'clab-netbox-arista-lab-'
for cfg in sorted(Path('output/configs').glob('*.cfg')):
    node = prefix + cfg.stem
    ps = subprocess.run(['docker','ps','--filter',f'name={node}','--format','{{.Names}}'], capture_output=True, text=True)
    if ps.stdout.strip() != node:
        continue
    body = 'configure\n' + cfg.read_text(encoding='utf-8')
    r = subprocess.run(['docker','exec','-i',node,'Cli','-p','15'], input=body, text=True, capture_output=True)
    blob = (r.stdout or '') + (r.stderr or '')
    ok = 'invalid input' not in blob.lower() and '% error' not in blob.lower()
    print(('  ' + cfg.stem + ' : applied') if ok else ('  ' + cfg.stem + ' : FAIL'))
    hn = 'configure\nhostname ' + cfg.stem + '\nend\n'
    subprocess.run(['docker','exec','-i',node,'Cli','-p','15'], input=hn, text=True, capture_output=True)
"@
        Write-Host "Done. Give BGP/MLAG ~60s to converge, then check on a node with 'docker exec <node> Cli -p 15 -c \"show ip bgp summary\"'." -ForegroundColor Yellow
    }
    
    "destroy" {
        Write-Host "Destroying ContainerLab topology..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-playbook playbooks/destroy_lab.yml
    }
    
    # Day 2
    "day2" {
        docker compose run --rm ansible python scripts/day2_demo.py @Rest
        exit $LASTEXITCODE
    }

    "diff" {
        & "$PSScriptRoot\scripts\config_diff.ps1" @Rest
    }

    "push" {
        Write-Host "Pushing configuration changes to devices..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-playbook playbooks/push_configs.yml
    }

    "test" {
        docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml @Rest
        exit $LASTEXITCODE
    }

    "backup" {
        foreach ($dev in @("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")) {
            docker exec "clab-netbox-arista-lab-$dev" Cli -p 15 -c "copy running-config flash:backup.cfg" | Out-Null
            Write-Host "  $dev -> flash:backup.cfg" -ForegroundColor Green
        }
    }

    "rollback" {
        foreach ($dev in @("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")) {
            $out = "configure replace flash:backup.cfg`nwrite memory" | docker exec -i "clab-netbox-arista-lab-$dev" Cli -p 15 2>&1 | Out-String
            if ($out -match '%') { Write-Host "  $dev : $($out.Trim())" -ForegroundColor Red } else { Write-Host "  $dev restored from flash:backup.cfg" -ForegroundColor Green }
        }
    }
    
    "pipeline" {
        Write-Host "Running full pipeline..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-playbook playbooks/full_pipeline.yml
    }
    
    # Optional add-on: Autonomy Workbench (executive demo). Runs on the host, because it
    # talks to the running containers and to NetBox from outside them.
    "workbench" {
        Write-Host "Starting the Autonomy Workbench on http://127.0.0.1:8099 ..." -ForegroundColor Cyan
        Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
        python -m workbench.cli serve
        exit $LASTEXITCODE
    }

    "workbench-check" {
        Write-Host "Rehearsing the Autonomy Workbench demo..." -ForegroundColor Cyan
        python -m workbench.cli check @Rest
        exit $LASTEXITCODE
    }

    "workbench-walk" {
        Write-Host "Walking the whole demo with no lab running..." -ForegroundColor Cyan
        python -m workbench.cli walkthrough
        exit $LASTEXITCODE
    }

    "workbench-lab" {
        Write-Host "Deploying the demo lab and preparing the trunk layer..." -ForegroundColor Cyan
        $env:MSYS_NO_PATHCONV = "1"
        python -m workbench.demo_lab @Rest
        exit $LASTEXITCODE
    }

    "eapi" {
        # Turning eAPI on needs the switch's CLI, which means docker exec, which NetBox
        # deliberately does not have. So this is the one lab step that stays on the host.
        # Without it every page shows six unreachable devices and nothing says why.
        Write-Host "Enabling eAPI on the lab switches..." -ForegroundColor Cyan
        python scripts/prepare_eapi.py @Rest
        exit $LASTEXITCODE
    }

    "itsm" {
        # BOTH compose files, in this order, and the order matters.
        #
        # Zammad is defined in docker-compose.itsm.yml, so `--profile itsm` against the main
        # file alone starts nothing. But starting it with the ITSM file alone puts the
        # containers on `workshop_arista_automation_default` while NetBox sits on
        # `netbox-arista-lab` -- and then the plugin's call to http://zammad-nginx:8080
        # cannot resolve. The Tickets tab says "ITSM is not configured" while Zammad is
        # clearly running on the host port, which is a confusing failure to debug live.
        #
        # Passing both merges them onto the main file's named network, which is where the
        # plugin expects to find it.
        Write-Host "Starting the ITSM stack (Zammad) on http://localhost:8090 ..." -ForegroundColor Cyan
        docker compose -f docker-compose.yml -f docker-compose.itsm.yml --profile itsm up -d @Rest
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        # Then finish Zammad's first-run setup. A stock Zammad shows a "Set up a new system"
        # wizard, has no admin, no API token and no `system_init_done` flag, so the Tickets tab
        # cannot work until this runs. It waits for the schema rather than assuming it.
        Write-Host "`nConfiguring Zammad (first run takes a few minutes)..." -ForegroundColor Cyan
        python scripts/zammad_setup.py
        exit $LASTEXITCODE
    }

    "tickets" {
        # The Tickets tab is a demo surface; an almost-empty list makes the integration look
        # theoretical. This seeds the queue a hospital network team would plausibly have open,
        # then applies the states the seed describes (Zammad always creates in `new`).
        python scripts/seed_tickets.py @Rest
        exit $LASTEXITCODE
    }

    "verify-change" {
        # Proves the gate refuses what it must, then walks a real change through a real ticket.
        # The refusals are the point: a demo of the happy path only proves the buttons work.
        Write-Host "Verifying change control end to end..." -ForegroundColor Cyan
        python scripts/verify_change_control.py @Rest
        exit $LASTEXITCODE
    }

    "clean-fabric" {
        # Removes the residue that testing leaves behind. `workbench-reset` is additive by
        # design and will never take a VLAN away, so this is the explicit opposite.
        python scripts/clean_demo_fabric.py @Rest
        exit $LASTEXITCODE
    }

    "ipam" {
        # Model the fabric properly in NetBox: IPAM VLANs, tenants, prefixes and trunk
        # membership. Without this NetBox has no VLANs at all, which is the "rogue system"
        # problem -- the switches carry things the source of truth has never heard of.
        Write-Host "Modelling the fabric in NetBox IPAM..." -ForegroundColor Cyan
        python scripts/seed_ipam.py @Rest
        exit $LASTEXITCODE
    }

    "verify-service" {
        # The day-two story: one sentence, one approval, both legs of a pair, and the change
        # recorded in NetBox before it reaches a switch.
        Write-Host "Verifying the deliver-a-service flow..." -ForegroundColor Cyan
        python scripts/verify_service_flow.py @Rest
        exit $LASTEXITCODE
    }

    "itsm-down" {
        Write-Host "Stopping the ITSM stack (NetBox is left running)..." -ForegroundColor Cyan
        # No -v, deliberately: `down -v` on this project also removes NetBox's volumes,
        # which wipes the source of truth. That mistake cost an hour once.
        docker compose -f docker-compose.yml -f docker-compose.itsm.yml --profile itsm stop zammad-nginx zammad-websocket zammad-railsserver zammad-init zammad-redis zammad-postgres
    }

    "guide" {
        Write-Host "Opening the presenter guide on http://127.0.0.1:8099/guide ..." -ForegroundColor Cyan
        Write-Host "The workbench must be running:  .\run.ps1 workbench" -ForegroundColor Yellow
        Start-Process "http://127.0.0.1:8099/guide"
    }

    "workbench-live" {
        Write-Host "Walking the demo against the live switches..." -ForegroundColor Cyan
        python -m workbench.live_check @Rest
        exit $LASTEXITCODE
    }

    "workbench-reset" {
        Write-Host "Returning the demo fabric to intended state (audit log is kept)..." -ForegroundColor Cyan
        python -m workbench.cli reset @Rest
        exit $LASTEXITCODE
    }

    "workbench-status" {
        python -m workbench.cli overview @Rest
        exit $LASTEXITCODE
    }

    # The briefing deck (Marp markdown -> PowerPoint and HTML).
    "deck" {
        Write-Host "Rendering the briefing slides..." -ForegroundColor Cyan
        npx --yes @marp-team/marp-cli@latest wiki/fmol-briefing.md --html --output output/fmol-briefing.html
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npx --yes @marp-team/marp-cli@latest wiki/fmol-briefing.md --pptx --output output/fmol-briefing.pptx
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host "Done: output\fmol-briefing.pptx and output\fmol-briefing.html" -ForegroundColor Green
    }

    "deck-show" {
        & $MyInvocation.MyCommand.Path deck
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Start-Process (Join-Path $PSScriptRoot "output\fmol-briefing.html")
    }

    # Optional add-on: Plants Demo (never touches the Arista fabric data)
    "plants" {
        Write-Host "Creating Plants Demo sample organisation..." -ForegroundColor Cyan
        docker compose run --rm ansible python scripts/plants_demo/cli.py setup @Rest
        exit $LASTEXITCODE
    }

    "plants-audit" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py audit @Rest
        exit $LASTEXITCODE
    }

    "plants-import" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py import-sheet @Rest
        exit $LASTEXITCODE
    }

    "plants-views" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py views
        exit $LASTEXITCODE
    }

    "plants-impact" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py impact @Rest
        exit $LASTEXITCODE
    }

    "plants-triage" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py triage @Rest
        exit $LASTEXITCODE
    }

    "plants-change" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py change @Rest
        exit $LASTEXITCODE
    }

    "plants-l1" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py l1 @Rest
        exit $LASTEXITCODE
    }

    "plants-alert" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py alert @Rest
        exit $LASTEXITCODE
    }

    "plants-report" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py report @Rest
        exit $LASTEXITCODE
    }

    "plants-ot" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py ot @Rest
        exit $LASTEXITCODE
    }

    "plants-scan" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py scan @Rest
        exit $LASTEXITCODE
    }

    "plants-meraki" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py meraki-sync @Rest
        exit $LASTEXITCODE
    }

    "plants-meraki-mock" {
        docker compose run --rm -p 8081:8081 ansible python scripts/plants_demo/cli.py meraki-mock --host 0.0.0.0 --port 8081
        exit $LASTEXITCODE
    }

    "plants-remove" {
        docker compose run --rm ansible python scripts/plants_demo/cli.py remove @Rest
        exit $LASTEXITCODE
    }

    # Dual-DC AVD track. Generation uses the Ansible image; push uses the host
    # Docker socket, same as "apply", because that image is not on the lab mgmt net.
    "dualdc" {
        $sub = if ($Rest.Count -gt 0) { $Rest[0].ToLower() } else { "help" }
        $lab = "/labs/l3ls-dual-dc"
        switch ($sub) {
            "build" {
                Write-Host "Rendering dual-DC configs with AVD..." -ForegroundColor Cyan
                $env:MSYS_NO_PATHCONV = "1"
                docker compose run --rm -w $lab -e "ANSIBLE_CONFIG=$lab/ansible.cfg" ansible ansible-playbook playbooks/build.yml -i sites/site_1/inventory.yml -e "target_hosts=SITE1_FABRIC"
                if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                docker compose run --rm -w $lab -e "ANSIBLE_CONFIG=$lab/ansible.cfg" ansible ansible-playbook playbooks/build.yml -i sites/site_2/inventory.yml -e "target_hosts=SITE2_FABRIC"
                if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                docker compose run --rm -w $lab -e "ANSIBLE_CONFIG=$lab/ansible.cfg" ansible ansible-playbook playbooks/build-frr.yml -i sites/core/frr-inv.yml -e "target_hosts=CORE"
                exit $LASTEXITCODE
            }
            "start" {
                Write-Host "Deploying dual-DC ContainerLab topology..." -ForegroundColor Cyan
                $env:MSYS_NO_PATHCONV = "1"
                docker compose run --rm clab containerlab deploy --reconfigure --topo /clab/dual-dc/topology.clab.yml
            }
            "stop" {
                Write-Host "Destroying dual-DC ContainerLab topology..." -ForegroundColor Cyan
                $env:MSYS_NO_PATHCONV = "1"
                docker compose run --rm clab containerlab destroy --cleanup --topo /clab/dual-dc/topology.clab.yml
            }
            "apply" {
                Write-Host "Applying rendered EOS configs, then FRR..." -ForegroundColor Cyan
                $root = Join-Path $PSScriptRoot "labs\l3ls-dual-dc"
                $cfgs = @(
                    Get-ChildItem "$root\sites\site_1\intended\configs\*.cfg" -ErrorAction SilentlyContinue
                    Get-ChildItem "$root\sites\site_2\intended\configs\*.cfg" -ErrorAction SilentlyContinue
                )
                if (-not $cfgs) {
                    Write-Host "No rendered configs. Run .\run.ps1 dualdc build first." -ForegroundColor Red
                    exit 1
                }
                foreach ($cfg in $cfgs) {
                    $node = $cfg.BaseName
                    $running = docker ps --filter "name=$node" --format "{{.Names}}"
                    $container = $running | Where-Object { $_ -eq $node -or $_ -eq "clab-ATD-Dual-DC-$node" } | Select-Object -First 1
                    if (-not $container) {
                        Write-Host "  ${node}: container not running" -ForegroundColor Red
                        continue
                    }
                    $body = "configure`n" + (Get-Content $cfg.FullName -Raw)
                    $out = $body | docker exec -i $container Cli -p 15 2>&1 | Out-String
                    if ($out -match 'invalid input|% Error|% Invalid') {
                        Write-Host "  ${node}: FAIL" -ForegroundColor Red
                    } else {
                        Write-Host "  ${node}: applied" -ForegroundColor Green
                    }
                }
                $frrCfg = Join-Path $root "sites\core\intended\configs\frr-core1.cfg"
                $frrRunning = docker ps --filter "name=frr-core1" --format "{{.Names}}"
                $frr = $frrRunning | Where-Object { $_ -eq "frr-core1" -or $_ -eq "clab-ATD-Dual-DC-frr-core1" } | Select-Object -First 1
                if ((Test-Path $frrCfg) -and $frr) {
                    docker cp $frrCfg "${frr}:/etc/frr/frr.conf"
                    docker exec $frr /usr/lib/frr/frr-reload.py --reload /etc/frr/frr.conf
                    Write-Host "  frr-core1: reloaded" -ForegroundColor Green
                } elseif (Test-Path $frrCfg) {
                    Write-Host "  frr-core1: config rendered, container not running" -ForegroundColor Red
                }
            }
            default {
                Write-Host "Usage: .\run.ps1 dualdc <build|start|apply|stop>" -ForegroundColor Yellow
                Write-Host "See labs/l3ls-dual-dc/WORKSHOP.md" -ForegroundColor Yellow
            }
        }
    }

    # Build
    "build" {
        Write-Host "Building NetBox (with ZTP plugin) and Ansible containers..." -ForegroundColor Cyan
        docker compose build netbox netbox-worker ansible
    }
    
    default {
        Show-Help
    }
}

