# MCP server sandbox snapshot (shared-mcp-servers-v2), built in Daytona by
# `daytona/shared_mcp_daytona.py server-snapshot`. Docker-in-Docker plus the
# deploy files; `deploy` starts dockerd and pulls the digest-pinned images.
FROM docker:28.3.3-dind

WORKDIR /srv/enterprise-bench
COPY docker-compose.yaml Caddyfile images.env image-lock.json ./
