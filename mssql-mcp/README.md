# mssql-mcp in Docker

[mssql-mcp-server](https://github.com/JexinSam/mssql_mcp_server) 1.0.0 running as an
HTTP MCP server, next to the existing `1c-rag` server (`http://localhost:9000/mcp`).

| | |
|---|---|
| Endpoint | `http://localhost:9001/mcp` (bound to 127.0.0.1 only) |
| Tools | `list_tables`, `query_sql` (SELECT only); `execute_sql` only when `MCP_READ_ONLY=false` |
| Auth to SQL Server | SQL login + password (Windows auth is not possible from a Linux container) |

## Setup

1. On the SQL Server create a read-only login:

   ```sql
   CREATE LOGIN claude_reader WITH PASSWORD = '<strong password>';
   USE your_database;
   CREATE USER claude_reader FOR LOGIN claude_reader;
   ALTER ROLE db_datareader ADD MEMBER claude_reader;
   ```

   The server needs SQL Server and Windows Authentication mode, TCP/IP enabled and
   port 1433 open in Windows Firewall.

2. Copy `.env.example` to `.env` and fill it in.

3. Build and start together with the 1C services (from `D:\1C\mcp-1c-rag-server`):

   ```bash
   start.bat
   ```

   or only this service: `docker compose up -d --build mssql-mcp`.
   It is part of the root `docker-compose.yml` and shares the `mcp-network` network
   (Docker name `mcp-1c-network`) with the 1C containers.

4. Register it in Claude Code (same way as 1c-rag):

   ```bash
   claude mcp add --transport http --scope user mssql http://localhost:9001/mcp
   ```

## Healthcheck

Every 30 s `healthcheck.py` does an MCP `initialize` against the local endpoint.
With `MCP_HEALTHCHECK_DB=true` it also runs `SELECT 1` on SQL Server.

```bash
docker inspect --format "{{.State.Health.Status}}" mssql-mcp
```

Docker does not restart an unhealthy container by itself; it only reports the state.

## Troubleshooting

- `docker logs mssql-mcp` shows connection errors when a tool is called.
- Certificate error: set `TrustServerCertificate=yes`.
- SQL Server on the same PC: `MSSQL_HOST=host.docker.internal`.
- Named instance (`SERVER\SQLEXPRESS`): use its fixed TCP port instead, e.g. `MSSQL_HOST=192.168.1.10,1433`;
  the SQL Browser lookup does not work reliably from a container.
