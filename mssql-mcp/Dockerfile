FROM python:3.12-slim

# Microsoft ODBC Driver 18 for SQL Server
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg2 ca-certificates unixodbc && \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 && \
    apt-get purge -y gnupg2 && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "mssql-mcp-server==1.0.0" "mcp[cli]==2.3.0"

WORKDIR /app
COPY http_main.py healthcheck.py ./

RUN useradd --create-home --uid 10001 mcp
USER mcp

ENV PYTHONUNBUFFERED=1 \
    MSSQL_DRIVER="ODBC Driver 18 for SQL Server" \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_PATH=/mcp \
    MCP_READ_ONLY=true \
    MCP_HEALTHCHECK_DB=true

EXPOSE 8000

ENTRYPOINT ["python", "/app/http_main.py"]
