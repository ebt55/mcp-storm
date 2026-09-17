"""Offline sqlite MCP server (tool names/args mirror mcp-server-sqlite's subset
used by the 25 tasks). Run: python -m mcp_storm.testing.fake_sqlite_server --db-path <db>"""

from __future__ import annotations

import json
import sqlite3
import sys

from mcp.server.fastmcp import FastMCP

db_path = sys.argv[sys.argv.index("--db-path") + 1]
mcp = FastMCP("storm-fake-sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def read_query(query: str) -> str:
    """Run a read-only SQL query; rows returned as a JSON array."""
    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(query).fetchall()]
        return json.dumps(rows, separators=(",", ":"), ensure_ascii=False, default=str)
    finally:
        conn.close()


@mcp.tool()
def write_query(query: str) -> str:
    """Run a write SQL statement (INSERT/UPDATE/DELETE); returns rows affected."""
    conn = _connect()
    try:
        cur = conn.execute(query)
        conn.commit()
        return f"rows_affected: {cur.rowcount}"
    finally:
        conn.close()


@mcp.tool()
def create_table(query: str) -> str:
    """Create a table (CREATE TABLE ...); returns ok."""
    conn = _connect()
    try:
        conn.execute(query)
        conn.commit()
        return "table created"
    finally:
        conn.close()


@mcp.tool()
def list_tables() -> str:
    """List all table names, one per line."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return "\n".join(r["name"] for r in rows) or "(no tables)"
    finally:
        conn.close()


@mcp.tool()
def describe_table(table_name: str) -> str:
    """Describe a table's columns (name, type)."""
    conn = _connect()
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return "\n".join(f"{r['name']} {r['type']}" for r in rows) or "(no such table)"
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
