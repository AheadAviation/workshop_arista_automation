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
