# =============================================================================
# Custom NetBox Docker Image with Plugins
# =============================================================================
# Extends the official netboxcommunity/netbox image with plugins
#
# Installed Plugins:
#   - netbox-topology-views: Visual topology diagrams from cable data
# =============================================================================

FROM netboxcommunity/netbox:v4.1

# Install plugins
COPY plugin_requirements.txt /opt/netbox/
RUN /opt/netbox/venv/bin/pip install --no-cache-dir -r /opt/netbox/plugin_requirements.txt

# Create static directory for topology views plugin images
RUN mkdir -p /opt/netbox/netbox/static/netbox_topology_views/img && \
    chown -R unit:root /opt/netbox/netbox/static/netbox_topology_views

