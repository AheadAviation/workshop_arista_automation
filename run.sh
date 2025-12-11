#!/bin/bash
# =============================================================================
# NetBox Arista ContainerLab - Helper Script (Bash)
# =============================================================================
# Usage:
#   ./run.sh up              # Start NetBox stack
#   ./run.sh import          # Import data to NetBox
#   ./run.sh generate        # Generate configs
#   ./run.sh deploy          # Deploy ContainerLab
#   ./run.sh push            # Push config changes
#   ./run.sh destroy         # Destroy lab
#   ./run.sh down            # Stop NetBox stack
#   ./run.sh shell           # Open Ansible shell
#   ./run.sh logs            # View NetBox logs
# =============================================================================

set -e

COMMAND=${1:-help}

show_help() {
    cat << EOF
NetBox Arista ContainerLab - Helper Commands

Usage: ./run.sh <command>

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
  deploy      Deploy ContainerLab topology
  destroy     Destroy ContainerLab topology
  clab-deploy Deploy ContainerLab directly (Linux/WSL)
  clab-destroy Destroy ContainerLab directly (Linux/WSL)

Day 2 Operations:
  push        Push config changes to running devices
  pipeline    Run full pipeline (generate + deploy)

EOF
}

case "${COMMAND,,}" in
    # Infrastructure
    up)
        echo -e "\033[36mStarting NetBox stack...\033[0m"
        docker compose up -d
        echo -e "\n\033[33mNetBox starting... Wait 1-2 minutes for initialization\033[0m"
        echo -e "\033[32mAccess NetBox at: http://localhost:8000\033[0m"
        echo -e "\033[32mLogin: admin / admin\033[0m"
        ;;
    
    down)
        echo -e "\033[36mStopping NetBox stack...\033[0m"
        docker compose down
        ;;
    
    down-v)
        echo -e "\033[31mStopping NetBox stack and removing data...\033[0m"
        docker compose down -v
        ;;
    
    logs)
        docker compose logs -f netbox
        ;;
    
    status)
        docker compose ps
        ;;
    
    # Automation
    import)
        echo -e "\033[36mImporting fabric topology to NetBox...\033[0m"
        docker compose run --rm ansible python scripts/full_import.py
        ;;
    
    bootstrap)
        echo -e "\033[36mBootstrapping NetBox data model...\033[0m"
        docker compose run --rm ansible python scripts/bootstrap_netbox.py
        ;;
    
    generate)
        echo -e "\033[36mGenerating Arista configurations...\033[0m"
        docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
        ;;
    
    inventory)
        echo -e "\033[36mShowing Ansible inventory from NetBox...\033[0m"
        docker compose run --rm ansible ansible-inventory --list
        ;;
    
    shell)
        echo -e "\033[36mOpening Ansible shell...\033[0m"
        docker compose run --rm ansible /bin/bash
        ;;
    
    # ContainerLab via Ansible
    deploy)
        echo -e "\033[36mDeploying ContainerLab topology...\033[0m"
        docker compose run --rm ansible ansible-playbook playbooks/deploy_lab.yml
        ;;
    
    destroy)
        echo -e "\033[36mDestroying ContainerLab topology...\033[0m"
        docker compose run --rm ansible ansible-playbook playbooks/destroy_lab.yml
        ;;
    
    # ContainerLab direct (Linux/WSL)
    clab-deploy)
        echo -e "\033[36mDeploying ContainerLab directly...\033[0m"
        sudo containerlab deploy --topo clab/topology.clab.yml
        ;;
    
    clab-destroy)
        echo -e "\033[36mDestroying ContainerLab directly...\033[0m"
        sudo containerlab destroy --topo clab/topology.clab.yml
        ;;
    
    # Day 2
    push)
        echo -e "\033[36mPushing configuration changes to devices...\033[0m"
        docker compose run --rm ansible ansible-playbook playbooks/push_configs.yml
        ;;
    
    pipeline)
        echo -e "\033[36mRunning full pipeline...\033[0m"
        docker compose run --rm ansible ansible-playbook playbooks/full_pipeline.yml
        ;;
    
    # Build
    build)
        echo -e "\033[36mBuilding Ansible container...\033[0m"
        docker compose build ansible
        ;;
    
    *)
        show_help
        ;;
esac

