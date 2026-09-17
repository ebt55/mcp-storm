"""Hand-built perfect solutions for all 25 tasks (grader golden, §5.3).

Each solution is the author's answer key: given a sandbox that already has the
task's fixture applied, produce the artifacts a correct agent would leave
behind. test_grader.py runs every solution (25/25 must PASS) and then corrupts
each sandbox (25/25 must FAIL). Solutions use direct file/sqlite access -- they
are the key, not an agent.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path


def _w(sandbox: Path, rel: str, content: str) -> None:
    target = sandbox / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _db(sandbox: Path) -> sqlite3.Connection:
    return sqlite3.connect(sandbox / "task.db")


# ------------------------------------------------------------------ filesystem
def fs_01(s: Path) -> None:
    (s / "notes").mkdir(parents=True, exist_ok=True)
    _w(s, "notes/a.txt", "alpha")
    _w(s, "notes/b.txt", "revenue: 42000\n")
    _w(s, "notes/c.txt", "a.txt\nb.txt\n")


def fs_02(s: Path) -> None:
    obj = {"mode": "strict", "retries": 3, "threshold": 12}
    _w(s, "out/config.json", json.dumps(obj))


def fs_03(s: Path) -> None:
    _w(s, "out/index.txt", "alpha.txt\nbeta.txt\nmu.txt\nzeta.txt\n")


def fs_04(s: Path) -> None:
    _w(s, "out/max.txt", "4550\n")


def fs_05(s: Path) -> None:
    (s / "archive").mkdir(parents=True, exist_ok=True)
    (s / "inputs/app.log").replace(s / "archive/app.log")
    (s / "inputs/data.log").replace(s / "archive/data.log")


def fs_06(s: Path) -> None:
    _w(s, "out/words.txt", "9\n")


def fs_07(s: Path) -> None:
    _w(s, "out/sum.txt", "45\n")


def fs_08(s: Path) -> None:
    _w(s, "out/reports/status.txt", "READY\n")


def fs_09(s: Path) -> None:
    _w(s, "out/engineers.txt", "ada\ncara\neve\n")


# ---------------------------------------------------------------------- sqlite
def db_01(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE inventory(id INTEGER, item TEXT, qty INTEGER)")
    conn.executemany("INSERT INTO inventory VALUES (?,?,?)",
                     [(1, "bolt", 40), (2, "nut", 120), (3, "washer", 75)])
    conn.commit()
    conn.close()
    _w(s, "out/count.txt", "3\n")


def db_02(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE metrics(id INTEGER, name TEXT, value INTEGER)")
    rows = [(1, "errors", 7), (2, "latency", 240), (3, "uptime", 99)]
    conn.executemany("INSERT INTO metrics VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    _w(s, "out/total.txt", "346\n")


def db_03(s: Path) -> None:
    # 10*2.5 + 4*12.0 + 25*0.4 = 25 + 48 + 10
    _w(s, "out/value.txt", "83\n")


def db_04(s: Path) -> None:
    _w(s, "out/top.txt", "ann\n")


def db_05(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE bulk(sku TEXT, qty INTEGER)")
    conn.execute("INSERT INTO bulk SELECT sku, qty FROM prices WHERE qty > 100")
    conn.execute("DELETE FROM prices WHERE qty > 100")
    conn.commit()
    conn.close()


def db_06(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE alerts(level TEXT, msg TEXT)")
    conn.execute("INSERT INTO alerts SELECT level, msg FROM logs WHERE level = 'ERROR'")
    conn.commit()
    conn.close()
    _w(s, "out/alert_count.txt", "2\n")


def db_07(s: Path) -> None:
    conn = _db(s)
    conn.execute("UPDATE employees SET salary = CAST(ROUND(salary * 1.1) AS INTEGER) "
                 "WHERE dept = 'eng'")
    conn.commit()
    conn.close()
    # ann 110, ben 150, cyd 226, dee 110 -> avg 149
    _w(s, "out/avg.txt", "149\n")


def db_08(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE summary(metric TEXT, value INTEGER)")
    conn.execute("INSERT INTO summary VALUES ('min',3), ('max',17), ('count',4)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- cross-server
def xs_01(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE people(name TEXT, city TEXT)")
    conn.executemany("INSERT INTO people VALUES (?,?)",
                     [("ada", "berlin"), ("bob", "oslo"), ("cyd", "lima"), ("dee", "oslo")])
    conn.commit()
    conn.close()
    _w(s, "out/n.txt", "4\n")


def xs_02(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE orders(id INTEGER, total INTEGER)")
    conn.executemany("INSERT INTO orders VALUES (?,?)", [(1, 50), (3, 70), (4, 20)])
    conn.commit()
    conn.close()
    _w(s, "out/fulfilled_total.txt", "140\n")


def xs_03(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE incidents(id INTEGER, severity INTEGER)")
    conn.executemany("INSERT INTO incidents VALUES (?,?)",
                     [(104, 9), (102, 8), (103, 5)])
    conn.commit()
    conn.close()
    _w(s, "out/manifest.txt", "104\n102\n103\n")


def xs_04(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE restock(item TEXT, qty INTEGER)")
    conn.executemany("INSERT INTO restock VALUES (?,?)",
                     [("bolt", 5), ("washer", 12), ("screw", 19)])
    conn.commit()
    conn.close()
    _w(s, "out/low_stock.txt", "bolt\nwasher\nscrew\n")


def xs_05(s: Path) -> None:
    _w(s, "out/done.txt", "2\n")


def xs_06(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE region_totals(region TEXT, total INTEGER)")
    conn.executemany("INSERT INTO region_totals VALUES (?,?)",
                     [("east", 180), ("west", 250), ("north", 190)])
    conn.commit()
    conn.close()
    _w(s, "out/best.txt", "west\n")


def xs_07(s: Path) -> None:
    conn = _db(s)
    conn.execute("CREATE TABLE snapshots(name TEXT, value INTEGER)")
    conn.executemany("INSERT INTO snapshots VALUES (?,?)",
                     [("cpu", 73), ("mem", 58), ("disk", 41)])
    conn.commit()
    conn.close()
    _w(s, "out/report.txt", "cpu: 73\ndisk: 41\nmem: 58\n")


def xs_08(s: Path) -> None:
    _w(s, "out/authors.txt", "herbert\nleguin\nnishida\n")


SOLUTIONS: dict[str, Callable[[Path], None]] = {
    "fs-01": fs_01, "fs-02": fs_02, "fs-03": fs_03, "fs-04": fs_04, "fs-05": fs_05,
    "fs-06": fs_06, "fs-07": fs_07, "fs-08": fs_08, "fs-09": fs_09,
    "db-01": db_01, "db-02": db_02, "db-03": db_03, "db-04": db_04, "db-05": db_05,
    "db-06": db_06, "db-07": db_07, "db-08": db_08,
    "xs-01": xs_01, "xs-02": xs_02, "xs-03": xs_03, "xs-04": xs_04, "xs-05": xs_05,
    "xs-06": xs_06, "xs-07": xs_07, "xs-08": xs_08,
}
