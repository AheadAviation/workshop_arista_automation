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
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"

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

ContainerLab (requires WSL2 on Windows):
  deploy      Deploy ContainerLab topology
  destroy     Destroy ContainerLab topology

Day 2 Operations:
  push        Push config changes to running devices
  pipeline    Run full pipeline (generate + deploy)

"@
}

switch ($Command.ToLower()) {
    # Infrastructure
    "up" {
        Write-Host "Starting NetBox stack..." -ForegroundColor Cyan
        docker compose up -d
        Write-Host "`nNetBox starting... Wait 1-2 minutes for initialization" -ForegroundColor Yellow
        Write-Host "Access NetBox at: http://localhost:8000" -ForegroundColor Green
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
    
    # ContainerLab
    "deploy" {
        Write-Host "Deploying ContainerLab topology..." -ForegroundColor Cyan
        Write-Host "Note: ContainerLab must run in WSL2 on Windows" -ForegroundColor Yellow
        docker compose run --rm ansible ansible-playbook playbooks/deploy_lab.yml
    }
    
    "destroy" {
        Write-Host "Destroying ContainerLab topology..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-playbook playbooks/destroy_lab.yml
    }
    
    # Day 2
    "push" {
        Write-Host "Pushing configuration changes to devices..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-playbook playbooks/push_configs.yml
    }
    
    "pipeline" {
        Write-Host "Running full pipeline..." -ForegroundColor Cyan
        docker compose run --rm ansible ansible-playbook playbooks/full_pipeline.yml
    }
    
    # Build
    "build" {
        Write-Host "Building Ansible container..." -ForegroundColor Cyan
        docker compose build ansible
    }
    
    default {
        Show-Help
    }
}

