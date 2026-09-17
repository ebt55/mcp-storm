"""Offline filesystem MCP server. Tool names/args mirror the REAL official
server (TypeScript @modelcontextprotocol/server-filesystem 2025.7.1, verified
at bring-up: read_file / write_file / ... -- NOT the archived Python server's
read_text_file). Root is a positional CLI arg like the real server.

Run: python -m mcp_storm.testing.fake_fs_server <root>"""

from __future__ import annotations

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
mcp = FastMCP("storm-fake-filesystem")


def _safe(rel: str) -> Path:
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"Access denied - path outside allowed directories: {rel}")
    return p


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with the provided content."""
    p = _safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Successfully wrote {len(content)} characters to {path}"


@mcp.tool()
def read_file(path: str) -> str:
    """Read the complete contents of a file."""
    p = _safe(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")
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
    """List a directory's entries ([FILE]/[DIR] one per line)."""
    p = _safe(path)
    if not p.is_dir():
        raise FileNotFoundError(f"Path not found: {path}")
    lines = []
    for child in sorted(p.iterdir()):
        lines.append(f"[DIR] {child.name}" if child.is_dir() else f"[FILE] {child.name}")
    return "\n".join(lines) if lines else "(empty)"


@mcp.tool()
def directory_tree(path: str) -> str:
    """Recursive listing, indented."""
    p = _safe(path)
    if not p.is_dir():
        raise FileNotFoundError(f"Path not found: {path}")
    lines: list[str] = []

    def walk(d: Path, depth: int) -> None:
        for child in sorted(d.iterdir()):
            lines.append("  " * depth + child.name + ("/" if child.is_dir() else ""))
            if child.is_dir():
                walk(child, depth + 1)

    walk(p, 0)
    return "\n".join(lines) if lines else "(empty)"


@mcp.tool()
def create_directory(path: str) -> str:
    """Create a directory (and parents)."""
    _safe(path).mkdir(parents=True, exist_ok=True)
    return f"Successfully created directory {path}"


@mcp.tool()
def move_file(source_path: str, destination_path: str) -> str:
    """Move or rename a file or directory."""
    src, dst = _safe(source_path), _safe(destination_path)
    if not src.exists():
        raise FileNotFoundError(f"File not found: {source_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dst)
    return f"Successfully moved {source_path} -> {destination_path}"


@mcp.tool()
def get_file_info(path: str) -> str:
    """File metadata (size, created, modified, isDirectory)."""
    import time

    p = _safe(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    st = p.stat()
    return (
        f"size: {st.st_size}\n"
        f"created: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(st.st_ctime))}\n"
        f"modified: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(st.st_mtime))}\n"
        f"isDirectory: {p.is_dir()}"
    )


if __name__ == "__main__":
    mcp.run()
