#!/usr/bin/env python3
"""Minimal personal expense tracker. Python 3 stdlib only."""

import os
import random
import re
import sqlite3
import string
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

DB = "expenses.db"
PAGE_SIZE = 20
BASE_URL = os.environ.get("EXPENSES_BASE_URL", "http://localhost:8000").rstrip("/")


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL REFERENCES groups(id),
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL REFERENCES groups(id),
            paid_by INTEGER NOT NULL REFERENCES users(id),
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expense_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_id INTEGER NOT NULL REFERENCES expenses(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            percentage REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_expenses_group ON expenses(group_id);
        CREATE INDEX IF NOT EXISTS idx_shares_expense ON expense_shares(expense_id);
    """)
    conn.commit()
    conn.close()


def gen_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def compute_debts(group_id):
    """Compute net balances: positive = owed money, negative = owes money."""
    conn = get_db()
    users = conn.execute("SELECT * FROM users WHERE group_id=?", (group_id,)).fetchall()

    balances = {u["id"]: 0.0 for u in users}

    # Credit payers
    for row in conn.execute(
        "SELECT paid_by, SUM(amount) as total FROM expenses WHERE group_id=? GROUP BY paid_by",
        (group_id,),
    ).fetchall():
        balances[row["paid_by"]] += row["total"]

    # Debit shares
    for row in conn.execute(
        "SELECT es.user_id, SUM(e.amount * es.percentage / 100.0) as owed FROM expense_shares es JOIN expenses e ON es.expense_id=e.id WHERE e.group_id=? GROUP BY es.user_id",
        (group_id,),
    ).fetchall():
        balances[row["user_id"]] -= row["owed"]

    conn.close()

    # Simplify debts
    user_map = {u["id"]: u["name"] for u in users}
    debts = []
    creditors = [(uid, bal) for uid, bal in balances.items() if bal > 0.01]
    debtors = [(uid, -bal) for uid, bal in balances.items() if bal < -0.01]
    creditors.sort(key=lambda x: -x[1])
    debtors.sort(key=lambda x: -x[1])

    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        debts.append((user_map[debtors[i][0]], user_map[creditors[j][0]], amount))
        debtors[i] = (debtors[i][0], debtors[i][1] - amount)
        creditors[j] = (creditors[j][0], creditors[j][1] - amount)
        if debtors[i][1] < 0.01:
            i += 1
        if creditors[j][1] < 0.01:
            j += 1
    return debts


CSS = """
*{box-sizing:border-box;margin:0;}
body{font-family:system-ui,sans-serif;max-width:900px;margin:0 auto;padding:1rem;color:#222;background:#fafafa}
h1,h2{margin-bottom:.5rem}
a{color:#0066cc}
form{margin-bottom:1rem}
label{display:block;margin:.3rem 0 .1rem;font-size:1rem}
input[type=text],input[type=number],input[type=date],select{width:100%;padding:.4rem;border:1px solid #ccc;border-radius:4px;font-size:1rem}
input[type=date]{min-width:100%;height:33px;}
button{padding:.5rem 1rem;background:#0066cc;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:1rem;margin-top:.5rem}
button:hover{background:#0052a3}
.section{background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:1rem;margin-bottom:1rem}
table{width:100%;border-collapse:collapse;font-size:1rem}
th,td{text-align:left;padding:.2rem .4rem .2rem 0;white-space:nowrap}
tr{border-bottom:1px solid #eee}
td:nth-child(3),th:nth-child(3){max-width:300px;white-space:normal;word-break:break-word}
.shares{display:flex;flex-direction:column;gap:.3rem}
.share-item{display:flex;align-items:center;gap:.3rem}
.share-item input[type=number]{width:4rem;margin-left:auto}
.row{display:flex;gap:.5rem}.row>div{flex:1}
.btn-del{background:none;border:none;color:#c00;cursor:pointer;padding:0;margin:0;}
.actions{white-space:nowrap;vertical-align:middle}
.actions a,.actions button{padding:8px;font-size:1.2rem;text-decoration:none;}
.actions form{margin:0;}
.page-nav{display:flex;gap:1rem;justify-content:center;margin-top:.5rem}
.debt{padding:.3rem 0}
.nav{margin-bottom:1rem;display:flex;align-items:center}
.nav h1{margin:0}
.nav .links{margin-left:auto;display:flex;gap:1rem}
.landing{margin:0 -1rem}
@media(min-width:700px){.landing{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin:0}.landing .log{grid-column:1/3}}
select{height:33px;}
"""


def html(title, body, group_id=None):
    nav = ""
    if group_id:
        nav = f'<div class="nav"><h1>{title} <small style="font-weight:normal;font-size:.5em;color:#888">{group_id}</small></h1><div class="links"><a href="/{group_id}">Expenses</a> <a href="/{group_id}/settings">Settings</a></div></div>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="view-transition" content="same-origin"><title>{title} - {group_id}</title><style>{CSS}</style></head><body>{nav}{body}</body></html>"""


def escape(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/new":
            return self.new_group()

        m = re.match(r"^/([a-z0-9]+)$", path)
        if m:
            return self.landing(m.group(1), qs)

        m = re.match(r"^/([a-z0-9]+)/settings$", path)
        if m:
            return self.settings(m.group(1))

        self.send_response(302)
        self.send_header("Location", "/new")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        data = parse_qs(body)

        path = urlparse(self.path).path.rstrip("/")

        m = re.match(r"^/([a-z0-9]+)/add$", path)
        if m:
            return self.add_expense(m.group(1), data)

        m = re.match(r"^/([a-z0-9]+)/add-user$", path)
        if m:
            return self.add_user(m.group(1), data)

        m = re.match(r"^/([a-z0-9]+)/delete/(\d+)$", path)
        if m:
            return self.delete_expense(m.group(1), int(m.group(2)))

        self.send_response(400)
        self.end_headers()

    def respond(self, code, content):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def new_group(self):
        gid = gen_id()
        conn = get_db()
        conn.execute("INSERT INTO groups(id) VALUES(?)", (gid,))
        conn.commit()
        conn.close()
        self.send_response(302)
        self.send_header("Location", f"/{gid}/settings")
        self.end_headers()

    def landing(self, gid, qs):
        conn = get_db()
        group = conn.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
        if not group:
            conn.close()
            return self.respond(404, html("Not Found", "<h1>Group not found</h1>"))

        users = conn.execute("SELECT * FROM users WHERE group_id=?", (gid,)).fetchall()
        page = int(qs.get("page", ["1"])[0])
        offset = (page - 1) * PAGE_SIZE
        total = conn.execute(
            "SELECT COUNT(*) as c FROM expenses WHERE group_id=?", (gid,)
        ).fetchone()["c"]
        expenses = conn.execute(
            "SELECT e.*, u.name as payer FROM expenses e JOIN users u ON e.paid_by=u.id WHERE e.group_id=? ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
            (gid, PAGE_SIZE, offset),
        ).fetchall()

        # Get shares for prefilling (from copy param or last expense)
        copy_id = qs.get("copy", [None])[0]
        copy_exp = None
        prefill_shares = {}
        prefill_desc = ""
        prefill_amount = ""
        prefill_payer = None
        prefill_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if copy_id:
            copy_exp = conn.execute(
                "SELECT * FROM expenses WHERE id=? AND group_id=?", (copy_id, gid)
            ).fetchone()
        if copy_exp:
            prefill_desc = copy_exp["description"]
            prefill_amount = f"{copy_exp['amount']:.2f}"
            prefill_payer = copy_exp["paid_by"]
            prefill_date = copy_exp["created_at"][:10]
            for s in conn.execute(
                "SELECT user_id, percentage FROM expense_shares WHERE expense_id=?",
                (copy_exp["id"],),
            ).fetchall():
                prefill_shares[s["user_id"]] = s["percentage"]
        else:
            last_exp = conn.execute(
                "SELECT id FROM expenses WHERE group_id=? ORDER BY created_at DESC LIMIT 1",
                (gid,),
            ).fetchone()
            if last_exp:
                for s in conn.execute(
                    "SELECT user_id, percentage FROM expense_shares WHERE expense_id=?",
                    (last_exp["id"],),
                ).fetchall():
                    prefill_shares[s["user_id"]] = s["percentage"]

        # Add expense form
        form = '<div class="section"><h2>Add expense</h2>'
        if not users:
            form += (
                "<p>Add users in <a href='/{}/settings'>settings</a> first.</p>".format(
                    gid
                )
            )
        else:
            form += f'<form method="post" action="/{gid}/add">'
            form += '<label>Paid by</label><select name="paid_by">'
            for u in users:
                sel = "selected" if u["id"] == prefill_payer else ""
                form += f'<option value="{u["id"]}" {sel}>{escape(u["name"])}</option>'
            form += "</select>"
            form += f'<label>Description</label><input type="text" name="description" value="{escape(prefill_desc)}" required>'
            form += f'<div class="row"><div><label>Amount</label><input type="number" name="amount" step="0.01" min="0.01" value="{prefill_amount}" required></div>'
            form += f'<div><label>Date</label><input type="date" name="date" value="{prefill_date}" required></div></div>'
            form += '<label>Split between</label><div class="shares">'
            for u in users:
                checked = (
                    "checked"
                    if (not prefill_shares or u["id"] in prefill_shares)
                    else ""
                )
                if u["id"] in prefill_shares:
                    p = prefill_shares[u["id"]]
                    pv = int(p) if p == int(p) else f"{p:.2f}"
                    pct_val = f' value="{pv}"'
                else:
                    pct_val = ""
                form += f'<div class="share-item"><input type="checkbox" name="share_{u["id"]}" value="1" {checked}>'
                form += f"<span>{escape(u['name'])}</span>"
                form += f'<input type="number" name="pct_{u["id"]}" step="0.01" min="0" max="100" required{pct_val}> %</div>'
            form += "</div>"
            form += "<button>Add Expense</button></form>"
        form += "</div>"

        # Debt status
        debts = compute_debts(gid)
        debt_html = '<div class="section"><h2>Balances</h2>'
        if not debts:
            debt_html += "<p>All settled up!</p>"
        else:
            for frm, to, amt in debts:
                debt_html += f'<div class="debt"><strong>{escape(frm)}</strong> owes <strong>{escape(to)}</strong> {amt:.2f}</div>'
        debt_html += "</div>"

        # Expense log
        log_html = '<div class="section log"><h2>Expenses</h2>'
        if expenses:
            log_html += "<table><tr><th>Date</th><th>Payer</th><th>Description</th><th>Amount</th><th></th></tr>"
            for e in expenses:
                d = e["created_at"][:10]
                log_html += f"<tr><td>{d}</td><td>{escape(e['payer'])}</td><td>{escape(e['description'])}</td><td>{e['amount']:.2f}</td>"
                log_html += (
                    f'<td class="actions"><a href="/{gid}?copy={e["id"]}">✎</a> '
                )
                log_html += f'<form method="post" action="/{gid}/delete/{e["id"]}" style="display:inline"><button type="submit" class="btn-del">&times;</button></form></td></tr>'
            log_html += "</table>"
            # Pagination
            pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            if pages > 1:
                log_html += '<div class="page-nav">'
                if page > 1:
                    log_html += f'<a href="/{gid}?page={page - 1}">&laquo; Prev</a>'
                log_html += f" Page {page}/{pages} "
                if page < pages:
                    log_html += f'<a href="/{gid}?page={page + 1}">Next &raquo;</a>'
                log_html += "</div>"
        else:
            log_html += "<p>No expenses yet.</p>"
        log_html += "</div>"

        body = f'<div class="landing">{form}{debt_html}{log_html}</div>'
        conn.close()
        self.respond(200, html("Expenses", body, gid))

    def settings(self, gid):
        conn = get_db()
        group = conn.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
        if not group:
            conn.close()
            return self.respond(404, html("Not Found", "<h1>Group not found</h1>"))
        users = conn.execute("SELECT * FROM users WHERE group_id=?", (gid,)).fetchall()
        conn.close()

        user_list = ""
        for u in users:
            user_list += f"<li>{escape(u['name'])}</li>"

        body = f"""<div class="section"><h2>Members</h2>
        <ul style="margin:1rem 0">{user_list if user_list else "<li>No members yet</li>"}</ul>
        <form method="post" action="/{gid}/add-user" style="margin-top:1rem;display:flex;gap:.5rem;align-items:end">
        <div style="flex:1"><label>Add member</label><input type="text" name="name" required></div>
        <button>Add</button></form></div>
        <div class="section"><h2>Share Link</h2><p>Anyone with this link can view, add and delete expenses in this group:</p>
        <a href="{BASE_URL}/{gid}" style="display:block;margin:.5rem 0">{BASE_URL}/{gid}</a></div>
        <div class="section"><h2>New Group</h2><p>Start a separate expense group with its own members and history.</p><a href="/new" style="display:block;margin:.5rem 0">Create new group</a></div>"""
        self.respond(200, html("Settings", body, gid))

    def add_expense(self, gid, data):
        paid_by = int(data["paid_by"][0])
        description = data["description"][0].strip()
        amount = round(float(data["amount"][0]), 2)

        conn = get_db()
        users = conn.execute("SELECT * FROM users WHERE group_id=?", (gid,)).fetchall()

        # Determine participants and percentages
        participants = []
        for u in users:
            if f"share_{u['id']}" in data:
                pct_val = data.get(f"pct_{u['id']}", [""])[0].strip()
                participants.append((u["id"], float(pct_val) if pct_val else 0))

        if not participants:
            conn.close()
            self.send_response(302)
            self.send_header("Location", f"/{gid}")
            self.end_headers()
            return

        final = [(uid, pct) for uid, pct in participants if pct > 0]

        now = data.get("date", [datetime.now(timezone.utc).strftime("%Y-%m-%d")])[0]
        cur = conn.execute(
            "INSERT INTO expenses(group_id, paid_by, description, amount, created_at) VALUES(?,?,?,?,?)",
            (gid, paid_by, description, amount, now),
        )
        exp_id = cur.lastrowid
        for uid, pct in final:
            conn.execute(
                "INSERT INTO expense_shares(expense_id, user_id, percentage) VALUES(?,?,?)",
                (exp_id, uid, pct),
            )
        conn.commit()
        conn.close()

        self.send_response(302)
        self.send_header("Location", f"/{gid}")
        self.end_headers()

    def add_user(self, gid, data):
        name = data["name"][0].strip()
        if name:
            conn = get_db()
            conn.execute("INSERT INTO users(group_id, name) VALUES(?,?)", (gid, name))
            conn.commit()
            conn.close()
        self.send_response(302)
        self.send_header("Location", f"/{gid}/settings")
        self.end_headers()

    def delete_expense(self, gid, exp_id):
        conn = get_db()
        conn.execute("DELETE FROM expense_shares WHERE expense_id=?", (exp_id,))
        conn.execute("DELETE FROM expenses WHERE id=? AND group_id=?", (exp_id, gid))
        conn.commit()
        conn.close()
        self.send_response(302)
        self.send_header("Location", f"/{gid}")
        self.end_headers()

    def address_string(self):
        return self.client_address[0]

    def log_message(self, format, *args):
        pass  # Suppress request logs


if __name__ == "__main__":
    init_db()
    port = 8000
    print(f"Running on http://localhost:{port}")
    print("Visit http://localhost:{}/new to create a group".format(port))
    HTTPServer(("", port), Handler).serve_forever()
