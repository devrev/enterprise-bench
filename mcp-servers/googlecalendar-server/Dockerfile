FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server.py .
COPY mcp_server.py .

# Data will be mounted at /data
VOLUME ["/data"]

# Environment defaults
ENV DATA_DIR=/data
ENV MCP_PORT=8016

EXPOSE 8016

# Like gmail-server and unlike the other three siblings there is no REST twin, so the MCP
# server is the default.
CMD ["python", "mcp_server.py"]
