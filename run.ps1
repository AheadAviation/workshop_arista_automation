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

    # Build
    "build" {
        Write-Host "Building NetBox (with ZTP plugin) and Ansible containers..." -ForegroundColor Cyan
        docker compose build netbox netbox-worker ansible
    }
    
    default {
        Show-Help
    }
}

