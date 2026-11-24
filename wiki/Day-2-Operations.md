# Day 2 Operations

> **Run Day 2 operations from NetBox (no CLI required):** Open **Network Ops → Workshop** (`http://localhost:8888/plugins/ops/workshop/`) and use the **Day 2 Operations** panel to edit Devices/Config Contexts, Regenerate Configs, Diff against Running Config, Backup, Push, Validate, and Rollback.

This page covers how to make changes in NetBox and push them to the running lab.

## The Source of Truth Workflow

The core principle: **All changes start in NetBox, then propagate to devices.**

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Change    │────▶│  Regenerate │────▶│    Push     │────▶│   Validate  │
│  in NetBox  │     │   Configs   │     │  to Devices │     │   Network   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

---

## Common Day 2 Tasks

### 1. Change Device Description

**Step 1: Update in NetBox**

1. Open NetBox: http://localhost:8888
2. Go to **Devices → Devices**
3. Click on a device (e.g., `spine-01`)
4. Click **Edit**
5. Change the **Description** field
6. Click **Save**

**Step 2: Regenerate Configs**

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
```

**Step 3: Push to Devices**

```powershell
# Windows
.\scripts\push_configs.ps1

# Linux/Mac
./scripts/push_configs.sh
```

**Step 4: Verify**

```bash
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config | include description"
```

---

### 2. Change BGP ASN

**Step 1: Update in NetBox**

1. Go to **Devices → Devices → spine-01**
2. Scroll to **Custom Fields**
3. Change **BGP ASN** value
4. Click **Save**

**Step 2: Regenerate and Push**

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
.\scripts\push_configs.ps1
```

**Step 3: Verify**

```bash
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"
```

---

### 3. Add a New VLAN

**Step 1: Update Config Context in NetBox**

1. Go to **Extras → Config Contexts**
2. Edit **tenant-core**
3. In the JSON data, add a new VLAN to `l2_segments`:

```json
{
  "tenants": [
    {
      "name": "CORE",
      "id": 1,
      "l2_segments": [
        {
          "vlan_number": 100,
          "vlan_name": "SERVERS",
          "vlan_ip": "10.100.100.1/24"
        },
        {
          "vlan_number": 150,
          "vlan_name": "NEW_VLAN",
          "vlan_ip": "10.100.150.1/24",
          "mtu": 9000,
          "description": "New application VLAN"
        }
      ]
    }
  ]
}
```

4. Click **Save**

**Step 2: Regenerate and Push**

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
.\scripts\push_configs.ps1
```

**Step 3: Verify**

```bash
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show vlan"
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show ip interface vlan 150"
```

---

### 4. Change Management Gateway

**Step 1: Update Config Context**

1. Go to **Extras → Config Contexts**
2. Edit **fabric-global**
3. Update the `management.gateway` value
4. Click **Save**

**Step 2: Regenerate and Push**

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
.\scripts\push_configs.ps1
```

---

### 5. Add NTP Server

**Step 1: Update Config Context**

1. Go to **Extras → Config Contexts**
2. Edit **fabric-global**
3. Add to the `management.ntp_servers` list:

```json
{
  "management": {
    "ntp_servers": ["172.30.30.1", "pool.ntp.org"],
    ...
  }
}
```

**Step 2: Regenerate and Push**

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
.\scripts\push_configs.ps1
```

**Step 3: Verify**

```bash
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ntp status"
```

---

## Batch Changes

For multiple changes, make all updates in NetBox first, then regenerate once:

1. Make all changes in NetBox
2. Run regenerate once:
   ```bash
   docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
   ```
3. Review changes (diff):
   ```bash
   git diff output/configs/
   ```
4. Push all configs:
   ```powershell
   .\scripts\push_configs.ps1
   ```
5. Validate:
   ```bash
   docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
   ```

---

## Configuration Diff

Before pushing, you can see what changed:

### Compare to Running Config

```bash
# Get running config
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config" > /tmp/running.cfg

# Compare with generated
diff /tmp/running.cfg output/configs/spine-01.cfg
```

### Git Diff of Generated Configs

If you're tracking `output/configs/` in git:

```bash
# See changes since last commit
git diff output/configs/

# After regenerating
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
git diff output/configs/
```

---

## Rollback

### Option 1: Revert in NetBox

1. Change the value back in NetBox
2. Regenerate and push

### Option 2: Use Config Backup

```bash
# Save current running config
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "copy running-config flash:backup.cfg"

# Later, restore if needed
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "configure replace flash:backup.cfg"
```

### Option 3: Git Revert

If configs are in git:

```bash
# Revert to previous commit
git checkout HEAD~1 -- output/configs/

# Push old configs
.\scripts\push_configs.ps1
```

---

## Scheduled Changes

For complex changes that require a maintenance window:

### 1. Prepare Changes Offline

```bash
# Make changes in NetBox
# Regenerate configs
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml

# Review and commit
git diff output/configs/
git add output/configs/
git commit -m "Prepared changes for VLAN 150 addition"
```

### 2. During Maintenance Window

```bash
# Push prepared configs
.\scripts\push_configs.ps1

# Validate immediately
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

---

## Validation After Changes

Always validate after pushing changes:

### Quick Validation

```bash
# Basic reachability
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml --tags quick
```

### Full Validation

```bash
# All tests
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

### Specific Checks

```bash
# BGP only
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"

# MLAG only
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show mlag"

# VXLAN only
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show vxlan vtep"
```

---

## CI/CD Integration

For production workflows, integrate with CI/CD:

### Pull Request Workflow

1. Create branch for changes
2. Make changes in NetBox
3. Regenerate configs
4. Create PR with config diff
5. Review and approve
6. Merge triggers push to devices

### GitHub Actions Example

```yaml
name: Network Config Deployment

on:
  push:
    branches: [main]
    paths:
      - 'output/configs/**'

jobs:
  deploy:
    runs-on: self-hosted  # On-prem runner with network access
    steps:
      - uses: actions/checkout@v4
      
      - name: Push configs to devices
        run: |
          ./scripts/push_configs.sh
      
      - name: Validate network
        run: |
          ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

---

## Best Practices

### 1. Always Use NetBox First

Never make changes directly on devices. Always:
1. Update NetBox
2. Regenerate
3. Push

### 2. Test in Lab First

Before production:
1. Make change in lab NetBox
2. Push to lab devices
3. Validate
4. Repeat in production

### 3. Small, Incremental Changes

Avoid large batch changes. Smaller changes are easier to:
- Review
- Validate
- Rollback

### 4. Document Changes

Use NetBox's built-in features:
- **Comments** on devices
- **Journal entries** for change history
- **Tags** for change tracking

### 5. Validate, Validate, Validate

After every change:
```bash
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

---

**Back to:** [[Home]] | [[Quick Start]]

