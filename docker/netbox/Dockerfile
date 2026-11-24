# =============================================================================
# Custom NetBox Docker Image with Plugins
# =============================================================================
# Extends the official netboxcommunity/netbox image with plugins.
#
# Build context is the repository root (see docker-compose.yml) so this can
# COPY the local plugin package.
#
# Installed Plugins:
#   - netbox-topology-views : Visual topology diagrams from cable data (PyPI)
#   - netbox_ztp_portal      : AHEAD ZTP Portal (local, editable install)
# =============================================================================

FROM netboxcommunity/netbox:v4.1

# --- Pinned PyPI plugins ---
COPY docker/netbox/plugin_requirements.txt /opt/netbox/
RUN /opt/netbox/venv/bin/pip install --no-cache-dir -r /opt/netbox/plugin_requirements.txt

# --- AHEAD ZTP Portal (local plugin, editable) ---
COPY plugins/netbox_ztp_portal /opt/netbox/plugins/netbox_ztp_portal
RUN /opt/netbox/venv/bin/pip install --no-cache-dir -e /opt/netbox/plugins/netbox_ztp_portal

# --- NetBox Ops (local plugin, editable) ---
# Day-two operations: devices, container labs, changes, approvals, AWX and ITSM.
# Bind-mounted in docker-compose.yml during development so code changes need only a
# container restart, not a rebuild.
COPY plugins/netbox_ops /opt/netbox/plugins/netbox_ops
RUN /opt/netbox/venv/bin/pip install --no-cache-dir -e /opt/netbox/plugins/netbox_ops

# --- The workbench engine ---
# `netbox_ops` is a surface on the engine in `workbench/`, which already does the drift
# comparison, the preview-without-apply executor, the approval gate and the audit record.
#
# Installed as a real package into the same venv NetBox runs from, rather than via
# PYTHONPATH: NetBox's Unit-launched worker does not reliably inherit PYTHONPATH, so an
# engine reachable from `manage.py shell` was NOT reachable from the request path — which
# showed up as a 500 on the page and nothing in the logs. Installing it the same way the
# plugins are installed removes that whole class of problem.
COPY workbench /opt/netbox/engine/workbench
COPY docker/netbox/engine_pyproject.toml /opt/netbox/engine/pyproject.toml
RUN /opt/netbox/venv/bin/pip install --no-cache-dir -e /opt/netbox/engine

# Verify the engine is importable at build time, so a missing copy fails the build rather
# than the first page load in front of a customer.
RUN /opt/netbox/venv/bin/python -c "import workbench, workbench.contracts, workbench.drift; print('engine ok')"

# --- Seed templates ---
# The NetBox ConfigTemplate that makes the native "Render Config" tab work, plus the bootstrap
# that imports the fabric and assigns it. Baked into the image so the `netbox-bootstrap`
# one-shot service runs them with no manual step — a fresh `docker compose up` renders configs.
COPY docker/configs/templates /opt/netbox/templates

# --- Importer dependencies ---
# The bootstrap runs the repo's own `scripts/full_import.py`, which needs these. NetBox's venv
# already has rich, yaml, requests and netaddr; pynetbox and python-dotenv it does not.
RUN /opt/netbox/venv/bin/pip install --no-cache-dir pynetbox python-dotenv

# Pre-place the plugin's static assets into the served static root. The runner's
# startup collectstatic doesn't reliably pick up bind-mounted plugin static, and
# without this the portal loads with no CSS/JS (looks unstyled). During dev, after
# editing CSS/JS, refresh with: ./run.ps1 collectstatic
RUN mkdir -p /opt/netbox/netbox/static/netbox_ztp_portal && \
    cp -r /opt/netbox/plugins/netbox_ztp_portal/netbox_ztp_portal/static/netbox_ztp_portal/* \
          /opt/netbox/netbox/static/netbox_ztp_portal/ && \
    chown -R unit:root /opt/netbox/netbox/static/netbox_ztp_portal

# Create static directory for topology views plugin images
RUN mkdir -p /opt/netbox/netbox/static/netbox_topology_views/img && \
    chown -R unit:root /opt/netbox/netbox/static/netbox_topology_views
