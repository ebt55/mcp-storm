"""Generate the 25 committed task JSONs (BUILD-GUIDE §5.3).

Deterministic authoring: every task has an objective grader (pure assertions on
the sandbox), a fixture, and a matching hand-built solution in
tests/golden/solutions.py. Run: uv run python scripts/gen_tasks.py
"""

from __future__ import annotations

import json
from pathlib import Path

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"

# ---------------------------------------------------------------- check helpers
def f_exists(path: str) -> dict:
    return {"type": "file_exists", "path": path}


def f_absent(path: str) -> dict:
    return {"type": "file_absent", "path": path}


def f_contains(path: str, text: str) -> dict:
    return {"type": "file_contains", "path": path, "text": text}


def f_contains_all(path: str, texts: list[str], ordered: bool = False) -> dict:
    return {"type": "file_contains_all", "path": path, "texts": texts, "ordered": ordered}


def f_lines(path: str, lines: list[str]) -> dict:
    return {"type": "file_lines", "path": path, "lines": lines}


def f_json(path: str, fields: dict) -> dict:
    return {"type": "file_json", "path": path, "fields": fields}


def t_exists(table: str) -> dict:
    return {"type": "table_exists", "table": table}


def sql_rows(query: str, rows: list[dict]) -> dict:
    return {"type": "sql_rows", "query": query, "rows": rows}


def sql_scalar(query: str, equals) -> dict:
    return {"type": "sql_scalar", "query": query, "equals": equals}


def row_count(table: str, equals: int) -> dict:
    return {"type": "row_count", "table": table, "equals": equals}


TASKS: list[dict] = [
    # ------------------------------------------------------------- filesystem (9)
    {
        "id": "fs-01",
        "prompt": "Create a directory named notes. Inside it create three files: "
        "a.txt containing the word alpha, b.txt which must contain the exact line "
        "revenue: 42000, and c.txt listing the other two filenames (a.txt and b.txt).",
        "servers": ["filesystem"],
        "fixture": {"files": {}},
        "expected_calls": ["create_directory", "write_file", "write_file", "write_file"],
        "grader": [
            f_contains("notes/a.txt", "alpha"),
            f_contains("notes/b.txt", "revenue: 42000"),
            f_contains_all("notes/c.txt", ["a.txt", "b.txt"]),
        ],
    },
    {
        "id": "fs-02",
        "prompt": "Read inputs/config.txt. It contains key: value lines. Write "
        "out/config.json as a JSON object mapping each key to its value, with "
        "numbers kept as numbers.",
        "servers": ["filesystem"],
        "fixture": {"files": {"inputs/config.txt": "mode: strict\nretries: 3\nthreshold: 12\n"}},
        "expected_calls": ["read_file", "create_directory", "write_file"],
        "grader": [
            f_json("out/config.json", {"mode": "strict", "retries": 3, "threshold": 12}),
        ],
    },
    {
        "id": "fs-03",
        "prompt": "The inputs/ directory contains four .txt files. Write "
        "out/index.txt with their filenames in alphabetical order, one per line.",
        "servers": ["filesystem"],
        "fixture": {"files": {
            "inputs/zeta.txt": "1", "inputs/alpha.txt": "2",
            "inputs/mu.txt": "3", "inputs/beta.txt": "4",
        }},
        "expected_calls": ["list_directory", "create_directory", "write_file"],
        "grader": [
            f_lines("out/index.txt", ["alpha.txt", "beta.txt", "mu.txt", "zeta.txt"]),
        ],
    },
    {
        "id": "fs-04",
        "prompt": "Read inputs/q1.txt, inputs/q2.txt and inputs/q3.txt. Each holds "
        "one integer. Write out/max.txt containing the largest of the three numbers.",
        "servers": ["filesystem"],
        "fixture": {"files": {"inputs/q1.txt": "120\n", "inputs/q2.txt": "4550\n", "inputs/q3.txt": "980\n"}},
        "expected_calls": ["read_file", "read_file", "read_file", "create_directory", "write_file"],
        "grader": [
            f_contains("out/max.txt", "4550"),
        ],
    },
    {
        "id": "fs-05",
        "prompt": "Move every .log file from inputs/ into a new directory archive/. "
        "The inputs directory must end up with no .log files left.",
        "servers": ["filesystem"],
        "fixture": {"files": {"inputs/app.log": "started\n", "inputs/data.log": "rows=9\n",
                              "inputs/keep.txt": "stay\n"}},
        "expected_calls": ["create_directory", "move_file", "move_file"],
        "grader": [
            f_contains("archive/app.log", "started"),
            f_contains("archive/data.log", "rows=9"),
            f_absent("inputs/app.log"),
            f_absent("inputs/data.log"),
            f_exists("inputs/keep.txt"),
        ],
    },
    {
        "id": "fs-06",
        "prompt": "Read inputs/note.txt and count how many DISTINCT words it has "
        "(case-insensitive). Write out/words.txt containing that count.",
        "servers": ["filesystem"],
        "fixture": {"files": {"inputs/note.txt":
                              "The quick brown fox jumps over the lazy dog the end\n"}},
        "expected_calls": ["read_file", "create_directory", "write_file"],
        "grader": [
            f_contains("out/words.txt", "9"),
        ],
    },
    {
        "id": "fs-07",
        "prompt": "inputs/matrix.txt holds three lines of three space-separated "
        "integers each. Write out/sum.txt with the sum of ALL nine integers.",
        "servers": ["filesystem"],
        "fixture": {"files": {"inputs/matrix.txt": "1 2 3\n4 5 6\n7 8 9\n"}},
        "expected_calls": ["read_file", "create_directory", "write_file"],
        "grader": [
            f_contains("out/sum.txt", "45"),
        ],
    },
    {
        "id": "fs-08",
        "prompt": "Create the directory out/reports and inside it a file "
        "status.txt containing exactly the text READY.",
        "servers": ["filesystem"],
        "fixture": {"files": {}},
        "expected_calls": ["create_directory", "write_file"],
        "grader": [
            f_contains("out/reports/status.txt", "READY"),
        ],
    },
    {
        "id": "fs-09",
        "prompt": "inputs/employees.txt holds 'name,dept' lines. Write "
        "out/engineers.txt with the names of employees whose dept is Eng, one "
        "per line, in the order they appear in the input.",
        "servers": ["filesystem"],
        "fixture": {"files": {"inputs/employees.txt":
                               "ada,Eng\nbob,Sales\ncara,Eng\ndan,HR\neve,Eng\n"}},
        "expected_calls": ["read_file", "create_directory", "write_file"],
        "grader": [
            f_lines("out/engineers.txt", ["ada", "cara", "eve"]),
        ],
    },
    # ---------------------------------------------------------------- sqlite (8)
    {
        "id": "db-01",
        "prompt": "Create a table inventory(id INTEGER, item TEXT, qty INTEGER). "
        "Insert these rows: (1, 'bolt', 40), (2, 'nut', 120), (3, 'washer', 75). "
        "Then write out/count.txt containing the number of rows in inventory.",
        "servers": ["sqlite", "filesystem"],
        "fixture": {"sqlite_seed": []},
        "expected_calls": ["create_table", "write_query", "write_query", "write_query",
                           "read_query", "create_directory", "write_file"],
        "grader": [
            t_exists("inventory"),
            sql_rows("SELECT id, item, qty FROM inventory",
                     [{"id": 1, "item": "bolt", "qty": 40},
                      {"id": 2, "item": "nut", "qty": 120},
                      {"id": 3, "item": "washer", "qty": 75}]),
            f_contains("out/count.txt", "3"),
        ],
    },
    {
        "id": "db-02",
        "prompt": "Create a table metrics(id INTEGER, name TEXT, value INTEGER). "
        "inputs/ holds three files; each filename (without extension) is a name "
        "and its content is an integer value. Insert one row per file (id 1,2,3 "
        "in filename order). Write out/total.txt with the sum of value.",
        "servers": ["filesystem", "sqlite"],
        "fixture": {"files": {"inputs/uptime.txt": "99\n", "inputs/latency.txt": "240\n",
                              "inputs/errors.txt": "7\n"}},
        "expected_calls": ["list_directory", "read_file", "read_file", "read_file",
                           "create_table", "write_query", "write_query", "write_query",
                           "read_query", "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT name, value FROM metrics",
                     [{"name": "errors", "value": 7},
                      {"name": "latency", "value": 240},
                      {"name": "uptime", "value": 99}]),
            f_contains("out/total.txt", "346"),
        ],
    },
    {
        "id": "db-03",
        "prompt": "The prices table (sku TEXT, qty INTEGER, price REAL) already "
        "exists. Write out/value.txt with the total inventory value "
        "(sum of qty * price).",
        "servers": ["sqlite", "filesystem"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE prices (sku TEXT, qty INTEGER, price REAL)",
            "INSERT INTO prices VALUES ('A1', 10, 2.5), ('B2', 4, 12.0), ('C3', 25, 0.4)",
        ]},
        "expected_calls": ["read_query", "create_directory", "write_file"],
        "grader": [
            f_contains("out/value.txt", "83"),
        ],
    },
    {
        "id": "db-04",
        "prompt": "Tables customers(id, name) and orders(id, customer_id) already "
        "exist. Write out/top.txt with the NAME of the customer who has the most "
        "orders.",
        "servers": ["sqlite", "filesystem"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE customers (id INTEGER, name TEXT)",
            "CREATE TABLE orders (id INTEGER, customer_id INTEGER)",
            "INSERT INTO customers VALUES (1,'ann'), (2,'ben'), (3,'cyd')",
            "INSERT INTO orders VALUES (1,1),(2,1),(3,2),(4,3),(5,1)",
        ]},
        "expected_calls": ["read_query", "create_directory", "write_file"],
        "grader": [
            f_contains("out/top.txt", "ann"),
        ],
    },
    {
        "id": "db-05",
        "prompt": "The prices table (sku TEXT, qty INTEGER) exists. Create a table "
        "bulk(sku TEXT, qty INTEGER), move every prices row with qty > 100 into "
        "bulk, and delete those rows from prices.",
        "servers": ["sqlite"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE prices (sku TEXT, qty INTEGER)",
            "INSERT INTO prices VALUES ('A1', 120), ('B2', 40), ('C3', 250), ('D4', 99)",
        ]},
        "expected_calls": ["create_table", "write_query", "write_query", "read_query"],
        "grader": [
            sql_rows("SELECT sku, qty FROM bulk ORDER BY qty",
                     [{"sku": "A1", "qty": 120}, {"sku": "C3", "qty": 250}]),
            sql_scalar("SELECT COUNT(*) FROM prices WHERE qty > 100", 0),
            sql_scalar("SELECT COUNT(*) FROM prices", 2),
        ],
    },
    {
        "id": "db-06",
        "prompt": "The logs table (level TEXT, msg TEXT) exists. Create a table "
        "alerts(level TEXT, msg TEXT) and insert every log row whose level is "
        "ERROR. Write out/alert_count.txt with the number of alerts.",
        "servers": ["sqlite", "filesystem"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE logs (level TEXT, msg TEXT)",
            "INSERT INTO logs VALUES ('ERROR','disk full'), ('INFO','ok'), "
            "('ERROR','timeout'), ('WARN','slow')",
        ]},
        "expected_calls": ["create_table", "write_query", "read_query",
                           "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT msg FROM alerts ORDER BY msg",
                     [{"msg": "disk full"}, {"msg": "timeout"}]),
            f_contains("out/alert_count.txt", "2"),
        ],
    },
    {
        "id": "db-07",
        "prompt": "The employees table (name TEXT, dept TEXT, salary INTEGER) "
        "exists. Give every employee in dept 'eng' a 10 percent raise (round to "
        "the nearest integer). Write out/avg.txt with the average salary across "
        "ALL employees after the raise (rounded to nearest integer).",
        "servers": ["sqlite", "filesystem"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE employees (name TEXT, dept TEXT, salary INTEGER)",
            "INSERT INTO employees VALUES ('ann','eng',100), ('ben','ops',150), "
            "('cyd','eng',205), ('dee','eng',100)",
        ]},
        "expected_calls": ["write_query", "read_query", "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT name, salary FROM employees ORDER BY name",
                     [{"name": "ann", "salary": 110}, {"name": "ben", "salary": 150},
                      {"name": "cyd", "salary": 226}, {"name": "dee", "salary": 110}]),
            f_contains("out/avg.txt", "149"),
        ],
    },
    {
        "id": "db-08",
        "prompt": "The readings table (sensor TEXT, value INTEGER) exists. Create "
        "a table summary(metric TEXT, value INTEGER) with exactly three rows: "
        "('min', <min value>), ('max', <max value>), ('count', <number of readings>).",
        "servers": ["sqlite"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE readings (sensor TEXT, value INTEGER)",
            "INSERT INTO readings VALUES ('t1', 3), ('t2', 17), ('t3', 8), ('t4', 12)",
        ]},
        "expected_calls": ["read_query", "create_table", "write_query", "write_query",
                           "write_query"],
        "grader": [
            sql_rows("SELECT metric, value FROM summary",
                     [{"metric": "min", "value": 3},
                      {"metric": "max", "value": 17},
                      {"metric": "count", "value": 4}]),
        ],
    },
    # ------------------------------------------------------------ cross-server (8)
    {
        "id": "xs-01",
        "prompt": "inputs/people.json holds a JSON array of {\"name\":..., "
        "\"city\":...} objects. Create a table people(name TEXT, city TEXT), "
        "insert every person, and write out/n.txt with the number of people.",
        "servers": ["filesystem", "sqlite"],
        "fixture": {"files": {"inputs/people.json":
            '[{"name":"ada","city":"berlin"},{"name":"bob","city":"oslo"},'
            '{"name":"cyd","city":"lima"},{"name":"dee","city":"oslo"}]\n'}},
        "expected_calls": ["read_file", "create_table", "write_query", "read_query",
                           "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT name, city FROM people ORDER BY name",
                     [{"name": "ada", "city": "berlin"}, {"name": "bob", "city": "oslo"},
                      {"name": "cyd", "city": "lima"}, {"name": "dee", "city": "oslo"}]),
            f_contains("out/n.txt", "4"),
        ],
    },
    {
        "id": "xs-02",
        "prompt": "inputs/orders.json holds a JSON array of {\"id\":..., "
        "\"total\":..., \"fulfilled\":...}. Create a table orders(id INTEGER, "
        "total INTEGER) containing ONLY the fulfilled orders, and write "
        "out/fulfilled_total.txt with the sum of their totals.",
        "servers": ["filesystem", "sqlite"],
        "fixture": {"files": {"inputs/orders.json":
            '[{"id":1,"total":50,"fulfilled":true},{"id":2,"total":30,"fulfilled":false},'
            '{"id":3,"total":70,"fulfilled":true},{"id":4,"total":20,"fulfilled":true}]\n'}},
        "expected_calls": ["read_file", "create_table", "write_query", "write_query",
                           "write_query", "read_query", "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT id, total FROM orders ORDER BY id",
                     [{"id": 1, "total": 50}, {"id": 3, "total": 70},
                      {"id": 4, "total": 20}]),
            f_contains("out/fulfilled_total.txt", "140"),
        ],
    },
    {
        "id": "xs-03",
        "prompt": "inputs/incidents.json holds incident records with fields id and "
        "severity (integer). Create a table incidents(id INTEGER, severity "
        "INTEGER), insert the THREE most severe incidents, and write "
        "out/manifest.txt with their ids ordered from most to least severe.",
        "servers": ["filesystem", "sqlite"],
        "fixture": {"files": {"inputs/incidents.json":
            '[{"id":101,"severity":3},{"id":102,"severity":8},{"id":103,"severity":5},'
            '{"id":104,"severity":9},{"id":105,"severity":1}]\n'}},
        "expected_calls": ["read_file", "create_table", "write_query", "write_query",
                           "write_query", "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT id, severity FROM incidents",
                     [{"id": 104, "severity": 9}, {"id": 102, "severity": 8},
                      {"id": 103, "severity": 5}]),
            f_lines("out/manifest.txt", ["104", "102", "103"]),
        ],
    },
    {
        "id": "xs-04",
        "prompt": "inputs/stock.json is a JSON array of {\"item\":..., \"qty\":...}. "
        "Create a table restock(item TEXT, qty INTEGER) with every item whose qty "
        "is below 20, and write out/low_stock.txt with those item names, one per "
        "line, in input order.",
        "servers": ["filesystem", "sqlite"],
        "fixture": {"files": {"inputs/stock.json":
            '[{"item":"bolt","qty":5},{"item":"nut","qty":50},{"item":"washer","qty":12},'
            '{"item":"screw","qty":19},{"item":"nail","qty":120}]\n'}},
        "expected_calls": ["read_file", "create_table", "write_query", "write_query",
                           "write_query", "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT item, qty FROM restock ORDER BY item",
                     [{"item": "bolt", "qty": 5}, {"item": "screw", "qty": 19},
                      {"item": "washer", "qty": 12}]),
            f_lines("out/low_stock.txt", ["bolt", "washer", "screw"]),
        ],
    },
    {
        "id": "xs-05",
        "prompt": "The tasks table (title TEXT, status TEXT) already exists. Write "
        "out/done.txt with the number of tasks whose status is completed.",
        "servers": ["sqlite", "filesystem"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE tasks (title TEXT, status TEXT)",
            "INSERT INTO tasks VALUES ('a','completed'), ('b','open'), "
            "('c','completed'), ('d','blocked')",
        ]},
        "expected_calls": ["read_query", "create_directory", "write_file"],
        "grader": [
            f_contains("out/done.txt", "2"),
        ],
    },
    {
        "id": "xs-06",
        "prompt": "inputs/sales.json is a JSON array of {\"region\":..., "
        "\"amount\":...}. Create a table region_totals(region TEXT, total "
        "INTEGER) with the sum of amounts per region, and write out/best.txt "
        "with the region name that has the highest total.",
        "servers": ["filesystem", "sqlite"],
        "fixture": {"files": {"inputs/sales.json":
            '[{"region":"east","amount":100},{"region":"west","amount":250},'
            '{"region":"east","amount":80},{"region":"north","amount":190}]\n'}},
        "expected_calls": ["read_file", "create_table", "write_query", "write_query",
                           "write_query", "read_query", "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT region, total FROM region_totals",
                     [{"region": "east", "total": 180},
                      {"region": "north", "total": 190},
                      {"region": "west", "total": 250}]),
            f_contains("out/best.txt", "west"),
        ],
    },
    {
        "id": "xs-07",
        "prompt": "inputs/metrics.json is a JSON array of {\"name\":..., "
        "\"value\":...}. Create a table snapshots(name TEXT, value INTEGER) with "
        "every entry, and write out/report.txt with one 'name: value' line per "
        "metric, sorted by name.",
        "servers": ["filesystem", "sqlite"],
        "fixture": {"files": {"inputs/metrics.json":
            '[{"name":"cpu","value":73},{"name":"mem","value":58},{"name":"disk","value":41}]\n'}},
        "expected_calls": ["read_file", "create_table", "write_query", "write_query",
                           "write_query", "create_directory", "write_file"],
        "grader": [
            sql_rows("SELECT name, value FROM snapshots",
                     [{"name": "cpu", "value": 73}, {"name": "disk", "value": 41},
                      {"name": "mem", "value": 58}]),
            f_lines("out/report.txt", ["cpu: 73", "disk: 41", "mem: 58"]),
        ],
    },
    {
        "id": "xs-08",
        "prompt": "The books table (title TEXT, author TEXT, year INTEGER) exists. "
        "Write out/authors.txt with the DISTINCT author names sorted "
        "alphabetically, one per line.",
        "servers": ["sqlite", "filesystem"],
        "fixture": {"sqlite_seed": [
            "CREATE TABLE books (title TEXT, author TEXT, year INTEGER)",
            "INSERT INTO books VALUES ('dune','herbert',1965), ('left hand','leguin',1969), "
            "('dreamers','herbert',2002), ('disgaea','nishida',2003)",
        ]},
        "expected_calls": ["read_query", "create_directory", "write_file"],
        "grader": [
            f_lines("out/authors.txt", ["herbert", "leguin", "nishida"]),
        ],
    },
]


def main() -> None:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    ids = [t["id"] for t in TASKS]
    assert len(ids) == len(set(ids)) == 25, f"need exactly 25 unique tasks, got {len(ids)}"
    counts = {"fs": 0, "db": 0, "xs": 0}
    for task in TASKS:
        for key in ("id", "prompt", "servers", "fixture", "grader", "expected_calls"):
            assert key in task, f"{task.get('id')} missing {key}"
        assert task["grader"], f"{task['id']} has empty grader"
        counts[task["id"].split("-")[0]] += 1
        path = TASKS_DIR / f"{task['id']}.json"
        path.write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path.name}")
    assert counts == {"fs": 9, "db": 8, "xs": 8}, counts
    print("distribution ok: 9 filesystem / 8 sqlite / 8 cross-server")


if __name__ == "__main__":
    main()
