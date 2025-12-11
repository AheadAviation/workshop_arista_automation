#!/bin/bash
# =============================================================================
# Push Generated Configs to Running ContainerLab Devices
# =============================================================================
# This script copies the generated configs to each cEOS container and applies
# them using 'configure replace'.
#
# Usage:
#   ./scripts/push_configs.sh
# =============================================================================

set -e

DEVICES=("spine-01" "spine-02" "leaf-01" "leaf-02" "leaf-03" "leaf-04")
CONFIG_DIR="output/configs"
CONTAINER_PREFIX="clab-netbox-arista-lab"

echo "=============================================="
echo "Pushing configs to running ContainerLab devices..."
echo "=============================================="

for device in "${DEVICES[@]}"; do
    config_file="${CONFIG_DIR}/${device}.cfg"
    container_name="${CONTAINER_PREFIX}-${device}"
    
    if [ ! -f "$config_file" ]; then
        echo "  ⚠ Warning: Config file not found: $config_file"
        continue
    fi
    
    echo -n "  Pushing config to ${device}..."
    
    # Copy config to container's flash
    cat "$config_file" | docker exec -i "$container_name" bash -c "cat > /mnt/flash/startup-config"
    
    # Apply config using configure replace
    docker exec "$container_name" Cli -p 15 -c "configure replace flash:startup-config" > /dev/null 2>&1
    
    echo " Done ✓"
done

echo "=============================================="
echo "All configurations pushed successfully!"
echo "=============================================="

