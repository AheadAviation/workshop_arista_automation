# =============================================================================
# Ansible Automation Container
# =============================================================================
# This container provides a complete Ansible environment for:
# - Running NetBox import scripts
# - Generating Arista configurations
# - Managing ContainerLab deployments
# - NAPALM-based config deployment with diff/rollback
#
# Build:
#   docker compose build ansible
#
# Usage:
#   docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
#   docker compose run --rm ansible python scripts/full_import.py
#   docker compose run --rm ansible ansible-playbook playbooks/push_configs_napalm.yml
# =============================================================================

FROM python:3.11-slim-bookworm

LABEL maintainer="Network Automation Demo"
LABEL description="Ansible automation container for NetBox + Arista + ContainerLab + NAPALM"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    sshpass \
    curl \
    jq \
    iputils-ping \
    diffutils \
    && rm -rf /var/lib/apt/lists/*

# Create ansible user first
RUN useradd -m -s /bin/bash ansible

# Set working directory
WORKDIR /ansible

# Copy requirements first for layer caching
COPY requirements.txt /ansible/
COPY ansible-requirements.yml /ansible/

# Install Python dependencies (as root, globally available)
# Including NAPALM for safe config deployment
# Note: paramiko is installed before napalm to ensure SSH transport works
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    paramiko>=3.0.0 \
    ansible-core>=2.15.0 \
    ansible-lint \
    pynetbox>=7.3.0 \
    pyyaml>=6.0 \
    jinja2>=3.1.0 \
    rich>=13.0 \
    python-dotenv>=1.0 \
    requests>=2.31 \
    netaddr>=0.10 \
    pytz \
    napalm>=4.1.0

# Install Ansible collections globally
RUN ansible-galaxy collection install \
    netbox.netbox \
    arista.eos \
    ansible.netcommon \
    community.general \
    ansible.utils \
    --collections-path /usr/share/ansible/collections

# Set environment variables
ENV ANSIBLE_HOST_KEY_CHECKING=False
ENV ANSIBLE_PYTHON_INTERPRETER=auto_silent
ENV PYTHONUNBUFFERED=1
ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections

# Copy project files and set ownership
COPY --chown=ansible:ansible . /ansible/

# Ensure output directories exist and are writable
RUN mkdir -p /ansible/output/configs /ansible/output/network_state && \
    chown -R ansible:ansible /ansible

# Switch to ansible user
USER ansible

# Default command
CMD ["ansible", "--version"]
