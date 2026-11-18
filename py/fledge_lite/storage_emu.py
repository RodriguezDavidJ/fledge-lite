"""In-process emulation of the Fledge storage microservice REST API on SQLite.

Real Fledge runs a separate C++ storage service. The Python core talks to it
over HTTP using aiohttp.ClientSession and a JSON query dialect (built by
fledge.common.storage_client.payload_builder). Here we answer those same HTTP
calls in-process, translating the JSON dialect to SQL. Only the subset of the
dialect used by the core REST API and the GUI is implemented.
"""

import datetime
import json
import re
import sqlite3
import time
import urllib.parse

TS_FMT = "%Y-%m-%d %H:%M:%S.%f"

_PG_FMT = [("SS.MS", "%f"), ("SS.US", "%f"), ("YYYY", "%Y"), ("HH24", "%H"), ("MM", "%m"), ("DD", "%d"),
           ("MI", "%M"), ("SS", "%S"), (".MS", ".%f"), (".US", ".%f")]


def _pg_to_strftime(fmt):
    out = fmt
    for a, b in _PG_FMT:
        out = out.replace(a, b)
    return out


def utc_now_str():
    return datetime.datetime.now(datetime.timezone.utc).strftime(TS_FMT)


def q(name):
    if name == "*":
        return "*"
    return '"' + str(name).replace('"', '""') + '"'


class StorageError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class SqliteStorage:
    def __init__(self, init_sql_path=None, init_sql=None):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        sql = init_sql if init_sql is not None else open(init_sql_path).read()
        sql = re.sub(r"\bfledge\.", "", sql)
        self.db.executescript(sql)
        self.db.executescript("""
            CREATE TABLE readings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_code TEXT NOT NULL,
                reading    JSON NOT NULL DEFAULT '{}',
                user_ts    TEXT NOT NULL,
                ts         TEXT NOT NULL
            );
            CREATE INDEX readings_ix1 ON readings(asset_code, user_ts);
        """)
        self.db.commit()
        self._types = {}
        self.log = []          # (method, path) of every storage call, for debugging
        self.on_readings = []  # callbacks fired after readings are appended

    # ------------------------------------------------------------------ utils
    def col_types(self, table):
        if table not in self._types:
            rows = self.db.execute("PRAGMA table_info({})".format(q(table))).fetchall()
            self._types[table] = {r[1]: (r[2] or "").upper() for r in rows}
        return self._types[table]

    def _rows(self, cur, table):
        types = self.col_types(table)
        names = [d[0] for d in cur.description]
        out = []
        for r in cur.fetchall():
            row = {}
            for n, v in zip(names, r):
                if isinstance(v, str) and (types.get(n, "").startswith("JSON") or n == "reading"):
                    try:
                        v = json.loads(v)
                    except ValueError:
                        pass
                elif isinstance(v, str) and v[:1] in "{[" and n not in types:
                    try:
                        v = json.loads(v)
                    except ValueError:
                        pass
                row[n] = v
            out.append(row)
        return out

    @staticmethod
    def _value(v, params):
        if isinstance(v, (dict, list)):
            params.append(json.dumps(v))
            return "?"
        if isinstance(v, bool):
            params.append("t" if v else "f")
            return "?"
        if isinstance(v, str) and v.lower() == "now()":
            params.append(utc_now_str())
            return "?"
        params.append(v)
        return "?"

    @staticmethod
    def _json_path(props):
        if isinstance(props, str):
            props = [props]
        return "$." + ".".join('"{}"'.format(p) for p in props)

    def _col(self, item, params, table=None, for_select=True):
        """Expression (and optional alias) for a "return"/"group" item."""
        if isinstance(item, str):
            return q(item), item
        if "json" in item:
            j = item["json"]
            expr = "json_extract({}, '{}')".format(q(j["column"]), self._json_path(j["properties"]))
            name = j["properties"] if isinstance(j["properties"], str) else j["properties"][-1]
        else:
            expr = q(item["column"])
            name = item["column"]
            if "format" in item:
                expr = "strftime('{}', {})".format(_pg_to_strftime(item["format"]), expr)
        return expr, item.get("alias", name)

    # ----------------------------------------------------------------- where
    def _where(self, w, params):
        if not w:
            return ""
        if "json" in w:
            col = "json_extract({}, '{}')".format(q(w["json"]["column"]), self._json_path(w["json"]["properties"]))
        elif re.fullmatch(r"\d+", str(w["column"])):
            col = str(w["column"])  # e.g. WHERE 1 = 1 placeholders
        else:
            col = q(w["column"])
        c = w["condition"].lower()
        v = w.get("value")
        if c in ("=", "!=", "<>", "<", ">", "<=", ">=", "like"):
            if isinstance(v, str) and v.endswith("+00:00"):
                v = v[:-6]
            sql = "{} {} {}".format(col, c.upper(), self._value(v, params))
        elif c in ("in", "not in"):
            vals = v if isinstance(v, list) else [v]
            sql = "{} {} ({})".format(col, c.upper(), ", ".join(self._value(x, params) for x in vals) or "NULL")
        elif c == "isnull":
            sql = "{} IS NULL".format(col)
        elif c == "notnull":
            sql = "{} IS NOT NULL".format(col)
        elif c in ("newer", "older"):
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=float(v))
            params.append(cutoff.strftime(TS_FMT))
            sql = "{} {} ?".format(col, ">" if c == "newer" else "<")
        else:
            raise StorageError(400, "Unsupported condition " + c)
        if "and" in w:
            sql = "({}) AND ({})".format(sql, self._where(w["and"], params))
        if "or" in w:
            sql = "({}) OR ({})".format(sql, self._where(w["or"], params))
        return sql

    # ----------------------------------------------------------------- query
    def query(self, table, p):
        params = []
        select = []
        group_exprs = []
        timebucket = p.get("timebucket")
        aggregate = p.get("aggregate")
        if isinstance(aggregate, dict):
            aggregate = [aggregate]

        if timebucket:
            size = float(timebucket.get("size", 1))
            ts = q(timebucket["timestamp"])
            bucket = "(CAST(strftime('%s', {ts}) AS INTEGER) / {s}) * {s}".format(ts=ts, s=size)
            fmt = _pg_to_strftime(timebucket.get("format", "YYYY-MM-DD HH24:MI:SS"))
            select.append("strftime('{}', {}, 'unixepoch') AS {}".format(fmt, bucket, q(timebucket.get("alias", "timestamp"))))
            group_exprs.append(bucket)

        if aggregate and aggregate[0].get("operation") == "all":
            return self._aggregate_all(table, p, params, select, group_exprs)

        if aggregate:
            for a in aggregate:
                op = a["operation"].upper()
                if "json" in a:
                    j = a["json"]
                    expr = "json_extract({}, '{}')".format(q(j["column"]), self._json_path(j["properties"]))
                    default_alias = '{}_{}'.format(a["operation"], j["properties"] if isinstance(j["properties"], str) else j["properties"][-1])
                else:
                    expr = q(a["column"])
                    default_alias = '{}_{}'.format(a["operation"], a["column"])
                if op == "COUNT" and a.get("column") == "*":
                    expr = "*"
                    default_alias = "count_*"
                select.append("{}({}) AS {}".format(op, expr, q(a.get("alias", default_alias))))
        elif "return" in p:
            for item in p["return"]:
                expr, alias = self._col(item, params, table)
                select.append("{} AS {}".format(expr, q(alias)))
        elif not timebucket:
            select.append("*")

        if "group" in p:
            g = p["group"]
            if isinstance(g, str):
                for col in [x.strip() for x in g.split(",")]:
                    select.insert(0, q(col))
                    group_exprs.append(q(col))
            else:
                expr, alias = self._col(g, params, table)
                select.insert(0, "{} AS {}".format(expr, q(alias)))
                group_exprs.append(expr)

        distinct = "DISTINCT " if p.get("modifier") == "distinct" else ""
        sql = "SELECT {}{} FROM {}".format(distinct, ", ".join(select), q(table))
        if "join" in p:
            sql = self._join_sql(table, p, params, select, distinct)
        where = self._where(p.get("where"), params)
        if where:
            sql += " WHERE " + where
        if group_exprs:
            sql += " GROUP BY " + ", ".join(group_exprs)
        sort = p.get("sort")
        if sort:
            if isinstance(sort, dict):
                sort = [sort]
            sql += " ORDER BY " + ", ".join("{} {}".format(q(s["column"]), s.get("direction", "ASC")) for s in sort)
        elif timebucket:
            sql += " ORDER BY {} DESC".format(group_exprs[0])
        if "limit" in p:
            sql += " LIMIT {}".format(int(p["limit"]))
            if "skip" in p:
                sql += " OFFSET {}".format(int(p["skip"]))
        elif "skip" in p:
            sql += " LIMIT -1 OFFSET {}".format(int(p["skip"]))
        try:
            cur = self.db.execute(sql, params)
        except sqlite3.Error as e:
            raise StorageError(400, "{} :: {}".format(e, sql))
        rows = self._rows(cur, table)
        return {"count": len(rows), "rows": rows}

    def _join_sql(self, table, p, params, select, distinct):
        j = p["join"]
        other = j["table"]["name"]
        other_col = j["table"].get("column", j["on"])
        sub = j.get("query", {})
        cols = ["t1.{}".format(s) if s != "*" else "t1.*" for s in select]
        for item in sub.get("return", []):
            expr, alias = self._col(item, params, other)
            cols.append("t2.{} AS {}".format(expr, q(alias)))
        return "SELECT {}{} FROM {} t1 JOIN {} t2 ON t1.{} = t2.{}".format(
            distinct, ", ".join(cols), q(table), q(other), q(j["on"]), q(other_col))

    def _aggregate_all(self, table, p, params, select, group_exprs):
        """min/max/average of every datapoint in each bucket (readings only)."""
        where = self._where(p.get("where"), params)
        sql = "SELECT {}, asset_code, reading FROM {}".format(select[0] if select else "user_ts AS timestamp", q(table))
        if where:
            sql += " WHERE " + where
        cur = self.db.execute(sql, params)
        buckets = {}
        for ts, asset, reading in cur.fetchall():
            key = (ts, asset)
            b = buckets.setdefault(key, {})
            for k, v in json.loads(reading).items():
                if isinstance(v, (int, float)):
                    s = b.setdefault(k, [v, v, 0.0, 0])
                    s[0] = min(s[0], v); s[1] = max(s[1], v); s[2] += v; s[3] += 1
        rows = []
        for (ts, asset), dps in sorted(buckets.items(), reverse=True):
            rows.append({"timestamp": ts, "asset_code": asset,
                         "reading": {k: {"min": s[0], "max": s[1], "average": s[2] / s[3]} for k, s in dps.items()}})
        if "limit" in p:
            rows = rows[:int(p["limit"])]
        return {"count": len(rows), "rows": rows}

    # ---------------------------------------------------------------- writes
    def insert(self, table, p):
        rows = p["inserts"] if "inserts" in p else [p]
        n = 0
        for r in rows:
            params = []
            ph = [self._value(v, params) for v in r.values()]
            sql = "INSERT INTO {} ({}) VALUES ({})".format(q(table), ", ".join(q(k) for k in r), ", ".join(ph))
            try:
                n += self.db.execute(sql, params).rowcount
            except sqlite3.IntegrityError as e:
                raise StorageError(400, str(e))
        self.db.commit()
        return {"response": "inserted", "rows_affected": n}

    def update(self, table, p):
        updates = p["updates"] if "updates" in p else [p]
        n = 0
        for u in updates:
            params, sets = [], []
            for k, v in u.get("values", {}).items():
                sets.append("{} = {}".format(q(k), self._value(v, params)))
            for e in u.get("expressions", []):
                sets.append("{c} = {c} {op} {v}".format(c=q(e["column"]), op=e["operator"], v=self._value(e["value"], params)))
            for jp in u.get("json_properties", []):
                sets.append("{c} = json_set({c}, '{path}', json({v}))".format(
                    c=q(jp["column"]), path=self._json_path(jp["path"]), v=self._value(json.dumps(jp["value"]), params)))
            sql = "UPDATE {} SET {}".format(q(table), ", ".join(sets))
            w = self._where(u.get("where") or u.get("condition"), params)
            if w:
                sql += " WHERE " + w
            n += self.db.execute(sql, params).rowcount
        self.db.commit()
        return {"response": "updated", "rows_affected": n}

    def delete(self, table, p):
        params = []
        sql = "DELETE FROM {}".format(q(table))
        w = self._where((p or {}).get("where") or (p or {}).get("condition"), params)
        if w:
            sql += " WHERE " + w
        n = self.db.execute(sql, params).rowcount
        self.db.commit()
        return {"response": "deleted", "rows_affected": n}

    # -------------------------------------------------------------- readings
    def append_readings(self, readings):
        now = utc_now_str()
        rows = []
        for r in readings:
            ts = r.get("user_ts") or now
            if ts.endswith("+00:00"):
                ts = ts[:-6]
            rows.append((r["asset_code"], json.dumps(r["reading"]), ts, now))
        self.db.executemany("INSERT INTO readings (asset_code, reading, user_ts, ts) VALUES (?,?,?,?)", rows)
        self.db.commit()
        for cb in self.on_readings:
            cb(readings)
        return {"response": "appended", "readings_added": len(rows)}

    def purge_readings(self, age=None, size=None):
        before = self.db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        if age is not None:
            cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=float(age))).strftime(TS_FMT)
            self.db.execute("DELETE FROM readings WHERE user_ts < ?", [cutoff])
        else:
            self.db.execute("DELETE FROM readings")
        self.db.commit()
        after = self.db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        return {"removed": before - after, "unsentPurged": 0, "unsentRetained": 0, "readings": after}

    # --------------------------------------------------------------- routing
    def handle(self, method, url, body):
        u = urllib.parse.urlsplit(url)
        path = u.path
        self.log.append((method, path))
        p = json.loads(body) if body else None
        parts = [x for x in path.split("/") if x]
        # /storage/table/{t}[/query]
        if parts[:2] == ["storage", "table"]:
            table = parts[2]
            if len(parts) == 4 and parts[3] == "query":
                return self.query(table, p or {})
            if len(parts) >= 4 and parts[3] == "snapshot":
                return {"created": {"id": str(int(time.time())), "table": table}}
            if method == "GET":
                qs = urllib.parse.parse_qsl(u.query)
                where = None
                for k, v in reversed(qs):
                    cond = {"column": k, "condition": "=", "value": v}
                    if where:
                        cond["and"] = where
                    where = cond
                return self.query(table, {"where": where} if where else {})
            if method == "POST":
                return self.insert(table, p)
            if method == "PUT":
                return self.update(table, p)
            if method == "DELETE":
                return self.delete(table, p)
        if parts[:2] == ["storage", "reading"]:
            if len(parts) == 2 and method == "POST":
                return self.append_readings(p["readings"])
            if len(parts) == 3 and parts[2] == "query":
                return self.query("readings", p or {})
            if len(parts) == 3 and parts[2] == "purge":
                qs = dict(urllib.parse.parse_qsl(u.query))
                return self.purge_readings(age=qs.get("age"), size=qs.get("size"))
            if len(parts) == 2 and method == "GET":
                qs = dict(urllib.parse.parse_qsl(u.query))
                cur = self.db.execute("SELECT * FROM readings WHERE id >= ? ORDER BY id LIMIT ?",
                                      [int(qs.get("id", 0)), int(qs.get("count", 100))])
                rows = self._rows(cur, "readings")
                return {"count": len(rows), "rows": rows}
        raise StorageError(404, "Storage emulator: unsupported {} {}".format(method, path))


# ---------------------------------------------------------------------------
# A drop-in replacement for the aiohttp.ClientSession used by storage_client.py

class _FakeResp:
    def __init__(self, status, doc):
        self.status = status
        self.reason = "OK" if status < 400 else "Error"
        self._doc = doc

    async def json(self, *a, **k):
        return self._doc

    async def text(self):
        return json.dumps(self._doc)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    storage = None  # set by boot

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def close(self):
        pass

    def _do(self, method, url, data=None, **kw):
        if isinstance(data, bytes):
            data = data.decode()
        try:
            doc = FakeSession.storage.handle(method, url, data)
            return _FakeResp(200, doc)
        except StorageError as e:
            import logging
            logging.getLogger("fledge_lite.storage").warning("%s %s -> %s | payload=%s", method, url, e.message, data)
            return _FakeResp(e.status, {"entryPoint": "storage-emulator", "message": e.message, "retryable": False})

    def get(self, url, **kw):
        return self._do("GET", url, **kw)

    def post(self, url, data=None, **kw):
        return self._do("POST", url, data, **kw)

    def put(self, url, data=None, **kw):
        return self._do("PUT", url, data, **kw)

    def delete(self, url, data=None, **kw):
        return self._do("DELETE", url, data, **kw)
