"""Offline sqlite MCP server — FIDELITY CLONE of the official archived Python
server (mcp-server-sqlite@2025.4.25): same tool names, arg shapes (query,
table_name), Python-repr result strings (str(list-of-dicts)), and query-class
enforcement (read_query SELECT-only, write_query non-SELECT, create_table
CREATE TABLE only). Verified against the cached upstream source at bring-up
(2026-09-18).

Run: python -m mcp_storm.testing.fake_sqlite_server --db-path <db>"""

from __future__ import annotations

import sqlite3
import sys

from mcp.server.fastmcp import FastMCP

db_path = sys.argv[sys.argv.index("--db-path") + 1]
mcp = FastMCP("sqlite-manager")


def _execute(query: str) -> list[dict]:
    """Upstream behavior: one connection per query, row_factory=Row, writes
    return [{'affected_rows': n}], reads return list-of-dicts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(query)
        if query.strip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER")
        ):
            conn.commit()
            return [{"affected_rows": cur.rowcount}]
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@mcp.tool()
def read_query(query: str) -> str:
    """Execute a SELECT query on the SQLite database."""
    if not query.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed for read_query")
    return str(_execute(query))


@mcp.tool()
def write_query(query: str) -> str:
    """Execute an INSERT, UPDATE, or DELETE query on the SQLite database."""
    if query.strip().upper().startswith("SELECT"):
        raise ValueError("SELECT queries are not allowed for write_query")
    return str(_execute(query))


@mcp.tool()
def create_table(query: str) -> str:
    """Create a new table in the SQLite database."""
    if not query.strip().upper().startswith("CREATE TABLE"):
        raise ValueError("Only CREATE TABLE statements are allowed")
    _execute(query)
    return "Table created successfully"


@mcp.tool()
def list_tables() -> str:
    """Show all existing tables in the database."""
    return str(_execute("SELECT name FROM sqlite_master WHERE type='table'"))


@mcp.tool()
def describe_table(table_name: str) -> str:
    """Show the schema for a specific table."""
    return str(_execute(f"PRAGMA table_info({table_name})"))


if __name__ == "__main__":
    mcp.run()
