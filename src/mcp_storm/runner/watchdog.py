"""Watchdog: kill a whole process tree on Windows (BUILD-GUIDE §6.3, §9).

taskkill /F /T kills the tree rooted at a pid -- required after kills/timeouts
because orphaned servers hold task.db locks and poison resumed runs.
"""

from __future__ import annotations

import subprocess
import sys


def kill_tree(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            import signal

            subprocess.run(["kill", "-9", str(pid)], check=False)
            return True
        except OSError:
            return False
    proc = subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        text=True,
        shell=False,
    )
    return proc.returncode == 0
