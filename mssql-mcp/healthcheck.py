"""Docker HEALTHCHECK: exit 0 when healthy, 1 otherwise.

Always does an MCP initialize against the local endpoint (and closes the session).
With MCP_HEALTHCHECK_DB=true it also runs SELECT 1 against SQL Server.
"""
import json
import os
import sys
import urllib.request

TIMEOUT = 5


def check_mcp() -> None:
    url = f"http://127.0.0.1:{os.getenv('MCP_PORT', '8000')}{os.getenv('MCP_PATH', '/mcp')}"
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "healthcheck", "version": "1"}},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        session_id = resp.headers.get("mcp-session-id")
        if b'"result"' not in resp.read():
            raise RuntimeError("initialize returned no result")

    if session_id:
        close = urllib.request.Request(url, method="DELETE", headers={"mcp-session-id": session_id})
        try:
            urllib.request.urlopen(close, timeout=TIMEOUT).close()
        except Exception:
            pass


def check_db() -> None:
    from pyodbc import connect
    from mssql_mcp_server.server import get_db_config

    _, connection_string = get_db_config()
    conn = connect(connection_string, timeout=TIMEOUT)
    try:
        conn.cursor().execute("SELECT 1").fetchone()
    finally:
        conn.close()


def main() -> int:
    try:
        check_mcp()
        if os.getenv("MCP_HEALTHCHECK_DB", "false").strip().lower() in ("1", "true", "yes", "on"):
            check_db()
    except Exception as e:
        print(f"unhealthy: {e}", file=sys.stderr)
        return 1
    print("healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
