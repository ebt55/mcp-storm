"""Offline filesystem MCP server — FIDELITY CLONE of the real official server
(TypeScript @modelcontextprotocol/server-filesystem 2025.7.1): same tool names,
arg names (move_file uses source/destination), result strings, JSON
directory_tree, and failure semantics (write_file does NOT create parent
directories; access outside the root is denied). Verified against the vendored
dist/index.js at bring-up (2026-09-18).

Run: python -m mcp_storm.testing.fake_fs_server <root>"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

argv = sys.argv[1:]
if "--root" in argv:
    root = Path(argv[argv.index("--root") + 1]).resolve()
elif argv and not argv[0].startswith("-"):
    root = Path(argv[0]).resolve()
else:
    root = Path.cwd()
mcp = FastMCP("secure-filesystem-server")


def _safe(rel: str) -> Path:
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"Access denied - path outside allowed directories: {p} not in {root}")
    return p


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create a new file or completely overwrite an existing file with new content."""
    p = _safe(path)  # real server: parent dirs are NOT created implicitly
    p.write_text(content, encoding="utf-8")
    return f"Successfully wrote to {path}"


@mcp.tool()
def read_file(path: str) -> str:
    """Read the complete contents of a file."""
    p = _safe(path)
    if not p.is_file():
        raise FileNotFoundError(f"Error reading file: File not found: {path}")
    return p.read_text(encoding="utf-8")


@mcp.tool()
def read_multiple_files(paths: list[str]) -> str:
    """Read multiple files; missing files reported inline."""
    parts = []
    for rel in paths:
        try:
            parts.append(read_file(rel))
        except FileNotFoundError:
            parts.append(f"File not found: {rel}")
    return "\n---\n".join(parts)


@mcp.tool()
def list_directory(path: str) -> str:
    """Get a detailed listing of files and directories."""
    p = _safe(path)
    if not p.is_dir():
        raise FileNotFoundError(f"Error listing directory: Path not found: {path}")
    lines = []
    for child in sorted(p.iterdir()):
        lines.append(f"[DIR] {child.name}" if child.is_dir() else f"[FILE] {child.name}")
    return "\n".join(lines) if lines else "(empty)"


@mcp.tool()
def directory_tree(path: str) -> str:
    """Get a recursive tree view of files and directories as JSON."""
    p = _safe(path)
    if not p.is_dir():
        raise FileNotFoundError(f"Error getting directory tree: Path not found: {path}")

    def build(d: Path) -> list[dict]:
        out: list[dict] = []
        for child in sorted(d.iterdir()):
            if child.is_dir():
                out.append({"name": child.name, "type": "directory", "children": build(child)})
            else:
                out.append({"name": child.name, "type": "file"})
        return out

    return json.dumps(build(p), indent=2, ensure_ascii=False)


@mcp.tool()
def create_directory(path: str) -> str:
    """Create a new directory or ensure a directory exists."""
    _safe(path).mkdir(parents=True, exist_ok=True)
    return f"Successfully created directory {path}"


@mcp.tool()
def move_file(source: str, destination: str) -> str:
    """Move a file or directory to a new location."""
    src, dst = _safe(source), _safe(destination)
    if not src.exists():
        raise FileNotFoundError(f"Error moving file: File not found: {source}")
    src.replace(dst)  # real server uses fs.rename: no implicit parent creation
    return f"Successfully moved {source} to {destination}"


@mcp.tool()
def get_file_info(path: str) -> str:
    """Retrieve detailed metadata (size, timestamps) about a file or directory."""
    import time

    p = _safe(path)
    if not p.exists():
        raise FileNotFoundError(f"Error getting file info: File not found: {path}")
    st = p.stat()
    return (
        f"size: {st.st_size}\n"
        f"created: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(st.st_ctime))}\n"
        f"modified: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(st.st_mtime))}\n"
        f"isDirectory: {p.is_dir()}\n"
        f"permissions: {oct(st.st_mode)[-3:]}"
    )


if __name__ == "__main__":
    mcp.run()
