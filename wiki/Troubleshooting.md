# Troubleshooting

This page covers common issues and their solutions.

## Quick Diagnostics

### Check Service Status

```bash
# All Docker services
docker compose ps

# NetBox logs
docker compose logs netbox

# Ansible container logs
docker compose logs ansible

# ContainerLab containers
docker ps --filter "name=clab-netbox"
```

### Verify Network Connectivity

```bash
# From Ansible container to NetBox
docker compose run --rm ansible curl -s http://netbox:8080/api/ | head

# From Ansible container to cEOS devices
docker compose run --rm ansible ping -c 1 172.30.30.11
```

---

## NetBox Issues

### NetBox Won't Start

**Symptoms:**
- `docker compose logs netbox` shows errors
- Cannot access http://localhost:8888

**Solutions:**

1. **Wait longer on first start** (migrations take 2-3 minutes):
   ```bash
   docker compose logs -f netbox
   # Wait for: [INFO] Listening at: http://0.0.0.0:8080
   ```

2. **Check PostgreSQL is healthy:**
   ```bash
   docker compose ps postgres
   # Should show "healthy"
   ```

3. **Reset and try again:**
   ```bash
   docker compose down -v
   docker compose up -d
   ```

### Import Scripts Fail

**Symptom:** `python scripts/full_import.py` returns errors

**Solutions:**

1. **Check NetBox is ready:**
   ```bash
   docker compose run --rm ansible curl -s http://netbox:8080/api/status/
   ```

2. **Verify token is set:**
   ```bash
   # Check .env file
   cat .env | grep NETBOX_TOKEN
   ```

3. **Run with verbose output:**
   ```bash
   docker compose run --rm ansible python scripts/bootstrap_netbox.py --dry-run
   ```

### "Object with slug=X does not exist"

**Symptom:** Config context creation fails with tag reference error

**Cause:** Tags must be created before config contexts that reference them.

**Solution:** Run full import which handles ordering:
```bash
docker compose run --rm ansible python scripts/full_import.py
```

---

## ContainerLab Issues

### cEOS Image Not Found

**Symptom:**
```
Error: Image ceos:4.32.0F not found locally
```

**Solution:**
```bash
# Import the image
docker import docker/cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F

# Verify
docker images ceos
```

### Containers Not Starting

**Symptom:** `docker ps` shows containers exiting

**Solutions:**

1. **Check container logs:**
   ```bash
   docker logs clab-netbox-arista-lab-spine-01
   ```

2. **Verify image is valid:**
   ```bash
   docker run --rm ceos:4.32.0F cat /etc/EOS-release
   ```

3. **Check system resources:**
   - Each cEOS container needs ~1GB RAM
   - Total: ~6GB for the full lab

### Network Links Not Working

**Symptom:** Interfaces show "not connected" in EOS

**Solutions:**

1. **Verify links were created:**
   ```bash
   docker compose run --rm clab containerlab inspect \
       --topo /clab/topology/topology-docker.clab.yml
   ```

2. **Check inside container:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 ip link show
   ```

3. **Redeploy the lab:**
   ```bash
   docker compose run --rm clab containerlab destroy \
       --topo /clab/topology/topology-docker.clab.yml --cleanup
   docker compose run --rm clab containerlab deploy \
       --topo /clab/topology/topology-docker.clab.yml
   ```

### "Failed to create network namespace"

**Symptom:** ContainerLab deploy fails with namespace errors

**Cause:** Insufficient privileges for Docker-in-Docker

**Solution:** Ensure clab service is privileged:
```yaml
# docker-compose.yml
clab:
  privileged: true
  network_mode: host
```

---

## Configuration Issues

### Configs Not Generated

**Symptom:** `output/configs/` is empty after running generate playbook

**Solutions:**

1. **Check Ansible output for errors:**
   ```bash
   docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml -v
   ```

2. **Verify inventory works:**
   ```bash
   docker compose run --rm ansible ansible-inventory -i inventory/hosts.yml --list
   ```

3. **Check output directory permissions:**
   ```bash
   ls -la output/configs/
   ```

### Configs Not Applied

**Symptom:** Devices show default config after push

**Solutions:**

1. **Verify push script ran successfully:**
   ```powershell
   .\scripts\push_configs.ps1
   ```

2. **Check if config file exists in container:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 ls -la /mnt/flash/
   ```

3. **Manually apply config:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "
   configure replace flash:startup-config
   "
   ```

### BGP Not Establishing

**Symptom:** `show ip bgp summary` shows neighbors not Established

**Solutions:**

1. **Verify interfaces are configured:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip interface brief"
   ```

2. **Check BGP configuration:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config section bgp"
   ```

3. **Verify Layer 3 reachability:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "ping 10.0.0.1"
   ```

4. **Check BGP debugging:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp neighbor 10.0.0.1"
   ```

---

## Ansible Issues

### "Unable to determine host network os"

**Symptom:**
```
Unable to automatically determine host network os.
Please manually configure ansible_network_os value
```

**Solution:** Ensure `group_vars/all.yml` includes:
```yaml
ansible_network_os: arista.eos.eos
ansible_connection: ansible.netcommon.network_cli
```

### "Connection refused" to Devices

**Symptom:** Ansible can't connect to cEOS devices

**Solutions:**

1. **Verify devices are running:**
   ```bash
   docker ps --filter "name=clab-netbox"
   ```

2. **Check management IPs:**
   ```bash
   docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip int brief"
   ```

3. **Use the static inventory:**
   ```bash
   docker compose run --rm ansible ansible-playbook \
       -i inventory/hosts.yml playbooks/test_all.yml
   ```

### NetBox Inventory Plugin Errors

**Symptom:**
```
Error loading plugin 'netbox.netbox.nb_inventory'
```

**Solutions:**

1. **Check pynetbox is installed:**
   ```bash
   docker compose run --rm ansible pip show pynetbox
   ```

2. **Verify NetBox URL is correct:**
   ```bash
   docker compose run --rm ansible echo $NETBOX_URL
   ```

3. **Use static inventory as fallback:**
   ```bash
   docker compose run --rm ansible ansible-playbook \
       -i inventory/hosts.yml playbooks/generate_configs.yml
   ```

---

## Docker Issues

### Port Already in Use

**Symptom:**
```
Error: Bind for 0.0.0.0:8888 failed: port is already allocated
```

**Solution:** Change the port in `docker-compose.yml`:
```yaml
netbox:
  ports:
    - "8889:8080"  # Use different port
```

### Disk Space Issues

**Symptom:** Docker commands fail with "no space left on device"

**Solution:**
```bash
# Clean up unused Docker resources
docker system prune -a

# Remove old lab data
docker compose down -v
```

### Container Build Fails

**Symptom:** `docker compose build` fails

**Solutions:**

1. **Rebuild without cache:**
   ```bash
   docker compose build --no-cache
   ```

2. **Check Dockerfile syntax:**
   ```bash
   docker build -t test-ansible -f docker/ansible/Dockerfile .
   ```

---

## Validation Test Failures

### Tests Report "Unreachable"

**Symptom:** All tests fail with connection errors

**Solutions:**

1. **Use static inventory:**
   ```bash
   docker compose run --rm ansible ansible-playbook \
       -i inventory/hosts.yml playbooks/test_all.yml
   ```

2. **Verify SSH connectivity:**
   ```bash
   docker compose run --rm ansible ssh admin@172.30.30.11
   ```

### MLAG Tests Fail

**Symptom:** MLAG shows "inactive" or "disabled"

**Solutions:**

1. **Check MLAG config:**
   ```bash
   docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show mlag"
   ```

2. **Verify peer link:**
   ```bash
   docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show port-channel 2000"
   ```

3. **Check for config context:**
   - Verify `mlag-pair-1` tag is applied in NetBox
   - Verify `mlag_domain` config context exists

### VXLAN Tests Fail

**Symptom:** VTEPs not learning or no VXLAN tunnels

**Note:** Basic VXLAN may not fully function without hosts or traffic. Focus on:
- BGP EVPN routes (`show bgp evpn summary`)
- VXLAN interface configuration (`show interfaces vxlan1`)

---

## Reset Everything

When all else fails, start fresh:

```bash
# 1. Destroy ContainerLab
docker compose run --rm clab containerlab destroy \
    --topo /clab/topology/topology-docker.clab.yml --cleanup 2>/dev/null

# 2. Stop all containers and remove volumes
docker compose down -v

# 3. Remove generated files
rm -rf output/configs/*.cfg
rm -rf topology/*.clab.yml

# 4. Rebuild containers
docker compose build --no-cache

# 5. Start fresh
docker compose up -d
docker compose run --rm ansible python scripts/full_import.py
```

---

**Next:** [[Day 2 Operations]] - Making changes in NetBox and pushing updates

