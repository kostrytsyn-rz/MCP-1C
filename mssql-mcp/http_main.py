"""Run mssql_mcp_server over Streamable HTTP inside a container.

The upstream entry point always binds 127.0.0.1:8000, which is unreachable
through Docker port publishing, so this launcher sets host/port/path itself.
"""
import os

from mcp.server.transport_security import TransportSecuritySettings
from mssql_mcp_server import mcp


def env_flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    if env_flag("MCP_READ_ONLY", "true"):
        # Leaves list_tables, query_sql and the table resource.
        mcp.remove_tool("execute_sql")

    allowed_hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",") if h.strip()]

    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
        streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        ),
    )


if __name__ == "__main__":
    main()
