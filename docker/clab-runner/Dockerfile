# ContainerLab Runner Container
# This runs ContainerLab inside a privileged container with full networking access
# Solves the WSL2 namespace visibility issue on Windows

FROM debian:bookworm-slim

# Set environment to avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    iproute2 \
    iputils-ping \
    openssh-client \
    jq \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (to communicate with host Docker)
# hadolint ignore=DL3008
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.gpg && \
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg /tmp/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    rm /tmp/docker.gpg

# hadolint ignore=DL3008,SC2174
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
    | tee /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

# Install ContainerLab from netdevops apt repo
# hadolint ignore=DL3008
RUN echo "deb [trusted=yes] https://netdevops.fury.site/apt/ /" \
    | tee /etc/apt/sources.list.d/netdevops.list && \
    apt-get update && \
    apt-get install -y containerlab && \
    rm -rf /var/lib/apt/lists/*

# Create working directory
WORKDIR /clab

# Verify installation
RUN containerlab version

# Default command
CMD ["bash"]
