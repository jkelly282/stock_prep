#!/usr/bin/env python3
"""PrepStock - emergency / preparedness inventory manager.

Pure standard library (Tkinter + SQLite). Run with:  python prepstock.py
Data is stored in ~/.prepstock.db  (an old ~/.prepstock.json is imported once).
"""
import csv
import json
import re
import sqlite3
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

DB_FILE = Path.home() / ".prepstock.db"
OLD_JSON = Path.home() / ".prepstock.json"
WINDOWS = {"1 week": 7, "2 weeks": 14, "1 month": 30, "3 months": 91,
           "6 months": 182, "1 year": 365}
TYPES = ["Food", "Water", "Medicine"]
PALETTES = {
    "light": dict(bg="#f4f5f7", fg="#1c1c1e", card="#ffffff", accent="#2f6fed",
                  field="#ffffff", warn="#fff1cc", bad="#ffd9d6", ok="#dcf3e1",
                  row0="#ffffff", row1="#e9eef7"),
    "dark": dict(bg="#1e1f24", fg="#e8e8ea", card="#2a2c33", accent="#5b95ff",
                 field="#33363e", warn="#5a4a1c", bad="#6a2a28", ok="#24472f",
                 row0="#2a2c33", row1="#353944"),
}

# table -> columns (besides id)
SECTIONS = {
    "consumables": ["name", "type", "qty", "kcal", "litres", "expiry", "location", "notes"],
    "maintenance": ["name", "last_checked", "every_days", "location", "notes"],
    "gear": ["name", "owned", "required", "location", "notes"],
    "people": ["name", "kcal_per_day", "water_per_day"],
}
INT_COLS = {"qty", "kcal", "every_days", "kcal_per_day", "owned", "required"}
REAL_COLS = {"litres", "water_per_day"}
CSV_COLS = ["section", "name", "type", "qty", "kcal", "litres", "expiry", "location",
            "notes", "last_checked", "every_days", "owned", "required", "kcal_per_day", "water_per_day"]

# what a dashboard card can show (key, description shown in the customise dialog)
METRICS = [
    ("food_days", "Days of food (for everyone)"),
    ("food_kcal", "Total food calories in date"),
    ("food_units", "Total food units in date"),
    ("water_litres", "Total water (litres) in date"),
    ("water_days", "Days of water (for everyone)"),
    ("med_units", "Total medicine units in date"),
    ("expiring_count", "Items expired or expiring soon"),
    ("expired_count", "Items already expired"),
    ("checks_overdue", "Routine checks overdue"),
    ("gear_missing", "Gear items below required number"),
    ("people_count", "Number of people"),
    ("daily_kcal", "Calories needed per day (everyone)"),
    ("daily_water", "Water needed per day (everyone)"),
]
DEFAULT_CARDS = [
    {"title": "Food", "metrics": ["food_days", "food_kcal"]},
    {"title": "Water", "metrics": ["water_litres", "water_days"]},
    {"title": "Medicine", "metrics": ["med_units"]},
    {"title": "Expiring soon", "metrics": ["expiring_count"]},
    {"title": "Checks & gear", "metrics": ["checks_overdue", "gear_missing"]},
]


def parse_date(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def blank(col):
    return 0 if col in INT_COLS else 0.0 if col in REAL_COLS else ""


def sort_key(v):
    s = str(v).strip()
    if s.upper().startswith("EXPIRED"):
        return (0, -1e9, "")
    m = re.match(r"^-?\d+(\.\d+)?", s)
    if m:
        return (0, float(m.group()), s.lower())
    if s in ("", "-"):
        return (2, 0, "")
    return (1, 0, s.lower())


# ------------------------------------------------------------------ database
class DB:
    def __init__(self, path=DB_FILE):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        for t, cols in SECTIONS.items():
            defs = ", ".join(f"{c} {self.sqltype(c)}" for c in cols)
            self.c.execute(f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY AUTOINCREMENT, {defs})")
            have = {r[1] for r in self.c.execute(f"PRAGMA table_info({t})")}
            for c in cols:  # upgrade older databases that lack a column
                if c not in have:
                    self.c.execute(f"ALTER TABLE {t} ADD COLUMN {c} {self.sqltype(c)}")
                    if c == "required" and "have" in have:  # convert old Yes/No gear rows
                        self.c.execute("UPDATE gear SET required=1, "
                                       "owned=CASE WHEN have='Yes' THEN 1 ELSE 0 END")
                    if c == "water_per_day":
                        self.c.execute("UPDATE people SET water_per_day=?",
                                       (float(self.setting("water_per_day", 3.0)),))
        self.c.commit()
        if self.setting("initialised") is None:
            self.first_run()

    @staticmethod
    def sqltype(c):
        if c == "water_per_day":
            return "REAL DEFAULT 3"
        if c in INT_COLS:
            return "INTEGER DEFAULT 0"
        if c in REAL_COLS:
            return "REAL DEFAULT 0"
        return "TEXT DEFAULT ''"

    def first_run(self):
        """Import the old JSON file if there is one, otherwise seed one person."""
        people = []
        try:
            old = json.loads(OLD_JSON.read_text())
            for t in ("consumables", "maintenance", "gear"):
                for it in old.get(t, []):
                    if t == "gear":
                        it = {**it, "owned": 1 if it.get("have") == "Yes" else 0, "required": 1}
                    self.add(t, it)
            st = old.get("settings", {})
            people = [{"name": f"Person {i + 1}", "kcal_per_day": st.get("kcal_per_day", 2000),
                       "water_per_day": st.get("water_per_day", 3.0)}
                      for i in range(st.get("people", 1))]
            for k in ("window", "dark", "water_per_day"):
                if k in st:
                    self.set_setting(k, st[k])
        except (OSError, ValueError):
            people = [{"name": "Person 1", "kcal_per_day": 2000, "water_per_day": 3.0}]
        for p in people:
            self.add("people", p)
        self.set_setting("initialised", 1)

    def all(self, t):
        return [dict(r) for r in self.c.execute(f"SELECT * FROM {t} ORDER BY id")]

    def add(self, t, d):
        cols = SECTIONS[t]
        cur = self.c.execute(
            f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [d.get(c, blank(c)) for c in cols])
        self.c.commit()
        return cur.lastrowid

    def update(self, t, id_, d):
        cols = SECTIONS[t]
        self.c.execute(f"UPDATE {t} SET {','.join(c + '=?' for c in cols)} WHERE id=?",
                       [d.get(c, blank(c)) for c in cols] + [id_])
        self.c.commit()

    def delete(self, t, id_):
        self.c.execute(f"DELETE FROM {t} WHERE id=?", (id_,))
        self.c.commit()

    def clear(self, t):
        self.c.execute(f"DELETE FROM {t}")
        self.c.commit()

    def setting(self, key, default=None):
        r = self.c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_setting(self, key, value):
        self.c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        self.c.commit()


class AutoScrollbar(ttk.Scrollbar):
    """Scrollbar (placed with grid) that hides itself when everything fits."""

    def set(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(lo, hi)


# ------------------------------------------------------------------ dialog
class ItemDialog(tk.Toplevel):
    """Add/edit form. fields = (key, label, kind, opts).
    opts: required, default, choices, show=(other_key, value) to show conditionally."""

    def __init__(self, app, title, fields, item=None):
        super().__init__(app)
        self.title(title)
        self.result = None
        self.fields, self.vars, self.rows = fields, {}, {}
        self.configure(bg=app.pal["bg"])
        self.transient(app)
        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        for r, (key, label, kind, opts) in enumerate(fields):
            val = (item or {}).get(key, opts.get("default", ""))
            if kind in ("int", "float") and val in (0, 0.0):
                val = ""
            var = tk.StringVar(value=str(val))
            self.vars[key] = var
            lab = ttk.Label(frm, text=label)
            lab.grid(row=r, column=0, sticky="w", pady=5, padx=(0, 12))
            if kind == "choice":
                w = ttk.Combobox(frm, textvariable=var, values=opts["choices"], state="readonly", width=30)
            else:
                w = ttk.Entry(frm, textvariable=var, width=34)
            w.grid(row=r, column=1, sticky="ew", pady=5)
            hint = None
            if kind == "date":
                hint = ttk.Label(frm, text="YYYY-MM-DD", style="Hint.TLabel")
                hint.grid(row=r, column=2, padx=8)
            self.rows[key] = (lab, w, hint, opts.get("show"))
        for key, (_, _, _, show) in self.rows.items():
            if show:
                self.vars[show[0]].trace_add("write", lambda *a: self.update_visibility())
        self.update_visibility()
        btns = ttk.Frame(frm)
        btns.grid(row=len(fields), column=0, columnspan=3, pady=(14, 0), sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = app.winfo_rootx() + max(0, (app.winfo_width() - self.winfo_reqwidth()) // 2)
        y = app.winfo_rooty() + 60
        self.geometry(f"+{x}+{y}")
        self.grab_set()
        self.wait_window()

    def visible(self, key):
        show = self.rows[key][3]
        return show is None or self.vars[show[0]].get() == show[1]

    def update_visibility(self):
        for key, widgets in self.rows.items():
            for w in widgets[:3]:
                if w is not None:
                    w.grid() if self.visible(key) else w.grid_remove()
        self.update_idletasks()

    def save(self):
        out = {}
        for key, label, kind, opts in self.fields:
            if not self.visible(key):
                out[key] = blank(key)
                continue
            raw = self.vars[key].get().strip()
            if opts.get("required") and not raw:
                return messagebox.showwarning("Missing", f"'{label}' is required.", parent=self)
            try:
                if kind == "int":
                    out[key] = int(raw or 0)
                elif kind == "float":
                    out[key] = float(raw or 0)
                elif kind == "date":
                    if raw and not parse_date(raw):
                        raise ValueError
                    out[key] = raw
                else:
                    out[key] = raw
            except ValueError:
                return messagebox.showwarning("Invalid", f"Check the value for '{label}'.", parent=self)
        self.result = out
        self.destroy()


# ------------------------------------------------------------------ card editor
class CardsDialog(tk.Toplevel):
    """Choose which cards appear on the Overview and what each one shows."""

    def __init__(self, app):
        super().__init__(app)
        self.title("Customise overview cards")
        self.result, self.cur = None, None
        self.cfg = json.loads(json.dumps(app.cards_cfg))
        self.configure(bg=app.pal["bg"])
        self.transient(app)
        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)
        left = ttk.Frame(frm)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 18))
        ttk.Label(left, text="Cards", style="Head.TLabel").pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(left, columns=("t",), show="", height=7, selectmode="browse")
        self.tree.column("t", width=190)
        self.tree.pack()
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="+ Add", command=self.add).pack(side="left")
        ttk.Button(row, text="Delete", command=self.delete).pack(side="left", padx=4)
        ttk.Button(row, text="▲", width=3, command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(row, text="▼", width=3, command=lambda: self.move(1)).pack(side="left", padx=4)
        ttk.Button(left, text="Reset to defaults", command=self.reset).pack(anchor="w", pady=(8, 0))

        right = ttk.Frame(frm)
        right.grid(row=0, column=1, sticky="n")
        ttk.Label(right, text="Card title").pack(anchor="w")
        self.title_var = tk.StringVar()
        ttk.Entry(right, textvariable=self.title_var, width=36).pack(anchor="w", pady=(2, 10))
        ttk.Label(right, text="Show on this card:").pack(anchor="w", pady=(0, 4))
        self.mvars = {}
        for key, label in METRICS:
            self.mvars[key] = tk.BooleanVar()
            ttk.Checkbutton(right, text=label, variable=self.mvars[key]).pack(anchor="w", pady=1)

        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.fill(0)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 80}+{app.winfo_rooty() + 40}")
        self.grab_set()
        self.wait_window()

    def fill(self, select):
        self.cur = None
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.cfg):
            self.tree.insert("", "end", iid=str(i), values=(c["title"],))
        self.tree.selection_set(str(select))

    def commit(self):
        if self.cur is not None and self.cur < len(self.cfg):
            self.cfg[self.cur] = {"title": self.title_var.get().strip() or "Card",
                                  "metrics": [k for k, _ in METRICS if self.mvars[k].get()]}
            self.tree.item(str(self.cur), values=(self.cfg[self.cur]["title"],))

    def on_select(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        self.commit()
        self.cur = int(sel[0])
        card = self.cfg[self.cur]
        self.title_var.set(card["title"])
        for k, var in self.mvars.items():
            var.set(k in card["metrics"])

    def add(self):
        self.commit()
        self.cfg.append({"title": "New card", "metrics": []})
        self.fill(len(self.cfg) - 1)

    def delete(self):
        if len(self.cfg) <= 1 or self.cur is None:
            return
        del self.cfg[self.cur]
        self.fill(min(self.cur, len(self.cfg) - 1))

    def move(self, d):
        self.commit()
        i, j = self.cur, (self.cur or 0) + d
        if i is None or not 0 <= j < len(self.cfg):
            return
        self.cfg[i], self.cfg[j] = self.cfg[j], self.cfg[i]
        self.fill(j)

    def reset(self):
        self.cfg = json.loads(json.dumps(DEFAULT_CARDS))
        self.fill(0)

    def save(self):
        self.commit()
        empty = [c["title"] for c in self.cfg if not c["metrics"]]
        if empty:
            return messagebox.showwarning("Empty card", f"Tick at least one item for: {', '.join(empty)}", parent=self)
        self.result = self.cfg
        self.destroy()


# ------------------------------------------------------------------ table tab
class Section(ttk.Frame):
    def __init__(self, app, key, columns, fields, row_fn, extra_buttons=(), type_filter=None):
        super().__init__(app.nb, padding=10)
        self.app, self.key, self.fields, self.row_fn = app, key, fields, row_fn
        self.columns, self.sort_col, self.sort_rev = columns, None, False
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="+ Add", style="Accent.TButton", command=self.add).pack(side="left")
        ttk.Button(bar, text="Edit", command=self.edit).pack(side="left", padx=6)
        ttk.Button(bar, text="Delete", command=self.delete).pack(side="left")
        for text, fn in extra_buttons:
            ttk.Button(bar, text=text, command=lambda f=fn: self.act(f)).pack(side="left", padx=(12, 0))
        # second row: type filter (left) and search (right) - wraps nothing, so it never overflows
        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(0, 8))
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *a: self.refresh())
        ttk.Button(row2, text="✕", width=3, command=lambda: self.search.set("")).pack(side="right")
        ttk.Entry(row2, textvariable=self.search, width=22).pack(side="right", padx=4)
        ttk.Label(row2, text="Search:").pack(side="right")

        self.filter_var = tk.StringVar(value="All")
        self.filter_btns = {}
        self.filter_key = type_filter[0] if type_filter else None
        if type_filter:
            ttk.Label(row2, text="Show:").pack(side="left", padx=(0, 8))
            for opt in type_filter[1]:
                b = ttk.Radiobutton(row2, text=opt, value=opt, variable=self.filter_var,
                                    style="Toolbutton", command=self.refresh)
                b.pack(side="left", padx=(0, 4))
                self.filter_btns[opt] = b

        self.count = ttk.Label(self, style="Hint.TLabel")
        self.count.pack(side="bottom", anchor="e", pady=(6, 0))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True)
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        vsb = AutoScrollbar(box, orient="vertical", command=self.tree.yview)
        hsb = AutoScrollbar(box, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        for c, head, _ in columns:
            self.tree.heading(c, text=head, command=lambda c=c: self.sort_by(c))
        self.tree.bind("<Double-1>", lambda e: self.edit())

    def sort_by(self, col):
        self.sort_rev = (not self.sort_rev) if self.sort_col == col else False
        self.sort_col = col
        self.refresh()

    def refresh(self):
        q = self.search.get().lower().strip()
        rows = []
        fv = self.filter_var.get()
        for item in self.app.data[self.key]:
            if fv != "All" and item[self.filter_key] != fv:
                continue
            values, tag = self.row_fn(item, self.app)
            if q and q not in " ".join(map(str, values)).lower():
                continue
            rows.append((item["id"], values, tag))
        ids = [c[0] for c in self.columns]
        if self.sort_col:
            i = ids.index(self.sort_col)
            rows.sort(key=lambda r: sort_key(r[1][i]), reverse=self.sort_rev)
        self.tree.delete(*self.tree.get_children())
        for n, (id_, values, tag) in enumerate(rows):
            self.tree.insert("", "end", iid=str(id_), values=values, tags=(tag or f"row{n % 2}",))
        if self.filter_btns:
            allitems = self.app.data[self.key]
            for opt, b in self.filter_btns.items():
                cnt = len(allitems) if opt == "All" else sum(1 for i in allitems if i[self.filter_key] == opt)
                b.config(text=f"{opt} ({cnt})")
        f = self.app.font
        for i, (c, head, minw) in enumerate(self.columns):
            arrow = (" ▼" if self.sort_rev else " ▲") if c == self.sort_col else ""
            self.tree.heading(c, text=head + arrow)
            w = max([f.measure(head) + 44] + [f.measure(str(r[1][i])) + 26 for r in rows] + [minw])
            self.tree.column(c, width=min(w, 420), minwidth=60, stretch=(i == len(self.columns) - 1))
        total = len(self.app.data[self.key])
        self.count.config(text=f"{len(rows)} of {total}" if (q or fv != "All") else f"{total} item(s)")

    def selected(self, warn=True):
        sel = self.tree.selection()
        item = next((i for i in self.app.data[self.key] if sel and i["id"] == int(sel[0])), None)
        if item is None and warn:
            messagebox.showinfo("Select an item", "Click a row in the table first.", parent=self.app)
        return item

    def reselect(self, id_):
        iid = str(id_)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def add(self):
        res = ItemDialog(self.app, "Add item", self.fields).result
        if res:
            new_id = self.app.db.add(self.key, res)
            self.app.changed()
            self.reselect(new_id)

    def edit(self):
        item = self.selected()
        if item:
            res = ItemDialog(self.app, "Edit item", self.fields, item).result
            if res:
                self.app.db.update(self.key, item["id"], res)
                self.app.changed()
                self.reselect(item["id"])

    def delete(self):
        item = self.selected()
        if item and messagebox.askyesno("Delete", f"Delete '{item['name']}'?", parent=self.app):
            self.app.db.delete(self.key, item["id"])
            self.app.changed()

    def act(self, fn):
        item = self.selected()
        if item:
            fn(item)
            self.app.db.update(self.key, item["id"], item)
            self.app.changed()
            self.reselect(item["id"])


# ---------- row formatters: return (values, colour-tag)
def consumable_row(it, app):
    exp = parse_date(it.get("expiry", ""))
    days = (exp - date.today()).days if exp else None
    window = WINDOWS[app.window.get()]
    tag = "" if days is None else "bad" if days < 0 else "warn" if days <= window else ""
    left = "" if days is None else ("EXPIRED" if days < 0 else f"{days} d")
    size = f"{it['kcal']} kcal" if it["type"] == "Food" and it["kcal"] else \
        f"{it['litres']:g} L" if it["type"] == "Water" and it["litres"] else ""
    return (it["name"], it["type"], it["qty"], size, exp.isoformat() if exp else "-", left,
            it["location"], it["notes"]), tag


def maintenance_row(it, app):
    last, every = parse_date(it["last_checked"]), it["every_days"]
    if not last:
        status, tag, due_s = "never checked", "bad", "-"
    elif every:
        due = last + timedelta(days=every)
        left = (due - date.today()).days
        status = "OVERDUE" if left < 0 else f"due in {left} d"
        tag = "bad" if left < 0 else "warn" if left <= 14 else "ok"
        due_s = due.isoformat()
    else:
        status, tag, due_s = "no schedule", "", "-"
    return (it["name"], it["last_checked"] or "-", every or "-", due_s, status,
            it["location"], it["notes"]), tag


def gear_row(it, app):
    owned, req = it["owned"], it["required"] or 1
    if owned >= req:
        status, tag = "✔ Enough" + (f" (+{owned - req} spare)" if owned > req else ""), "ok"
    else:
        status, tag = f"✘ Need {req - owned} more", "warn" if owned > 0 else "bad"
    return (it["name"], f"{owned} / {req}", status, it["location"], it["notes"]), tag


def people_row(it, app):
    return (it["name"], f"{it['kcal_per_day']} kcal/day", f"{it['water_per_day']:g} L/day"), ""


# ------------------------------------------------------------------ app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PrepStock - Emergency Supplies Inventory")
        self.geometry("1150x720")
        self.minsize(900, 560)
        self.db = DB()
        self.dark = bool(self.db.setting("dark", False))
        self.pal = PALETTES["dark" if self.dark else "light"]
        self.fam = tkfont.nametofont("TkDefaultFont").actual("family")
        self.font = tkfont.Font(family=self.fam, size=10)
        self.window = tk.StringVar(value=self.db.setting("window", "1 month"))
        self.f_big = tkfont.Font(family=self.fam, size=22, weight="bold")
        self.f_lab = tkfont.Font(family=self.fam, size=10)
        self.f_title = tkfont.Font(family=self.fam, size=11, weight="bold")
        valid = {k for k, _ in METRICS}
        cfg = self.db.setting("cards", DEFAULT_CARDS)
        self.cards_cfg = [{"title": c.get("title", "Card"),
                           "metrics": [k for k in c.get("metrics", []) if k in valid]}
                          for c in cfg] or json.loads(json.dumps(DEFAULT_CARDS))
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.data = {}
        self.reload()
        self.build()
        self.apply_theme()
        self.refresh_all()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def adjust_gear(self, it):
        n = simpledialog.askinteger(
            "Adjust amount", f"Add (positive) or remove (negative) from '{it['name']}'.\nCurrently {it['owned']}:",
            parent=self, initialvalue=1, minvalue=-100000, maxvalue=100000)
        if n:
            it["owned"] = max(0, it["owned"] + n)

    def reload(self):
        self.data = {t: self.db.all(t) for t in SECTIONS}

    # ---------- UI
    def build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        ttk.Label(top, text="PrepStock", style="Title.TLabel").pack(side="left")
        self.theme_btn = ttk.Button(top, command=self.toggle_theme)
        self.theme_btn.pack(side="right")
        ttk.Button(top, text="Export CSV", command=self.export_csv).pack(side="right", padx=6)
        ttk.Button(top, text="Import CSV", command=self.import_csv).pack(side="right")
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=12)

        self.dash = ttk.Frame(self.nb)
        self.nb.add(self.dash, text="  Overview  ")
        self.build_dashboard()

        self.sections = [
            Section(self, "consumables",
                    [("name", "Item", 170), ("type", "Type", 70), ("qty", "Qty", 40),
                     ("size", "Size (each)", 90), ("exp", "Expiry", 100), ("left", "Left", 70),
                     ("loc", "Location", 100), ("notes", "Notes", 160)],
                    [("name", "Name", "text", {"required": True}),
                     ("type", "Type", "choice", {"choices": TYPES, "default": "Food"}),
                     ("qty", "Quantity", "int", {"default": 1}),
                     ("kcal", "Calories per unit", "int", {"show": ("type", "Food")}),
                     ("litres", "Litres per unit", "float", {"show": ("type", "Water")}),
                     ("expiry", "Expiry date (optional)", "date", {}),
                     ("location", "Location", "text", {}),
                     ("notes", "Notes", "text", {})],
                    consumable_row, type_filter=("type", ["All"] + TYPES)),
            Section(self, "maintenance",
                    [("name", "Item", 180), ("last", "Last checked", 110), ("every", "Every (days)", 100),
                     ("due", "Next due", 100), ("status", "Status", 110),
                     ("loc", "Location", 100), ("notes", "Notes", 160)],
                    [("name", "Name", "text", {"required": True}),
                     ("last_checked", "Last checked", "date", {"default": date.today().isoformat()}),
                     ("every_days", "Check every (days)", "int", {"default": 90}),
                     ("location", "Location", "text", {}),
                     ("notes", "Notes", "text", {})],
                    maintenance_row,
                    [("Mark checked today",
                      lambda it: it.update(last_checked=date.today().isoformat()))]),
            Section(self, "gear",
                    [("name", "Item", 220), ("count", "Have / Need", 110), ("status", "Status", 160),
                     ("loc", "Location", 120), ("notes", "Notes", 200)],
                    [("name", "Name", "text", {"required": True}),
                     ("owned", "Quantity you have", "int", {"default": 1}),
                     ("required", "Required amount", "int", {"default": 1}),
                     ("location", "Location", "text", {}),
                     ("notes", "Notes", "text", {})],
                    gear_row,
                    [("+ 1", lambda it: it.update(owned=it["owned"] + 1)),
                     ("− 1", lambda it: it.update(owned=max(0, it["owned"] - 1))),
                     ("Adjust…", self.adjust_gear)]),
            Section(self, "people",
                    [("name", "Person", 220), ("kcal", "Daily calories", 140), ("water", "Daily water", 140)],
                    [("name", "Name", "text", {"required": True}),
                     ("kcal_per_day", "Calories needed per day", "int", {"default": 2000}),
                     ("water_per_day", "Water needed per day (litres)", "float", {"default": 3})],
                    people_row),
        ]
        for sec, label in zip(self.sections, ["Food, Water & Medicine", "Routine Checks",
                                               "Other Gear Checklist", "People"]):
            self.nb.add(sec, text=f"  {label}  ")

    def build_dashboard(self):
        self.canvas = tk.Canvas(self.dash, highlightthickness=0, bd=0, yscrollincrement=20)
        vsb = AutoScrollbar(self.dash, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.dash.columnconfigure(0, weight=1)
        self.dash.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        d = self.inner = ttk.Frame(self.canvas, padding=12)
        self.win = self.canvas.create_window((0, 0), window=d, anchor="nw")
        self._fit = (0, 0)
        self.canvas.bind("<Configure>", self.on_canvas)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, self.on_wheel)
        d.columnconfigure(0, weight=1)
        d.rowconfigure(1, weight=2)
        d.rowconfigure(3, weight=3)
        bar = ttk.Frame(d)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="Flag expiring within").pack(side="left", padx=(0, 4))
        cb = ttk.Combobox(bar, textvariable=self.window, values=list(WINDOWS), state="readonly", width=10)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda ev: self.settings_changed())
        ttk.Label(bar, text="   People, calories and water: see the People tab",
                  style="Hint.TLabel").pack(side="left")
        ttk.Button(bar, text="⚙ Customise cards", command=self.customise_cards).pack(side="right")

        self.cards_frame = ttk.Frame(d)
        self.cards_frame.grid(row=1, column=0, sticky="nsew", pady=8)
        self.card_widgets, self.card_titles, self.card_frames, self._ncols = [], [], [], 0
        self.rebuild_cards()

        ttk.Label(d, text="Needs attention", style="Head.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 4))
        box = ttk.Frame(d)
        box.grid(row=3, column=0, sticky="nsew")
        cols = ("item", "kind", "info", "loc")
        self.attn = ttk.Treeview(box, columns=cols, show="headings", selectmode="none", height=5)
        for c, h, w in [("item", "Item", 280), ("kind", "Type", 110), ("info", "Detail", 280), ("loc", "Location", 160)]:
            self.attn.heading(c, text=h)
            self.attn.column(c, width=w)
        sb = ttk.Scrollbar(box, command=self.attn.yview)
        self.attn.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.attn.pack(fill="both", expand=True)

    def rebuild_cards(self):
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self.card_widgets, self.card_titles, self.card_frames = [], [], []
        for card in self.cards_cfg:
            frame = ttk.Frame(self.cards_frame, style="Card.TFrame", padding=16)
            inner = ttk.Frame(frame, style="Card.TFrame")
            inner.pack(anchor="nw")
            title = ttk.Label(inner, text=card["title"], style="CardTitle.TLabel", justify="left")
            title.pack(anchor="w", pady=(0, 6))
            self.card_titles.append(title)
            ms = []
            for key in card["metrics"]:
                v = ttk.Label(inner, style="Big.TLabel", justify="left")
                v.pack(anchor="w")
                lab = ttk.Label(inner, style="Card.TLabel", justify="left")
                lab.pack(anchor="w", pady=(0, 8))
                ms.append((key, v, lab))
            self.card_widgets.append(ms)
            self.card_frames.append(frame)
        self._ncols = 0
        self.rescale()

    def layout_cards(self, ncols):
        """Arrange cards in as few, evenly filled rows as the width allows."""
        cf, n = self.cards_frame, len(self.card_frames)
        ncols = max(1, min(ncols, n))
        ncols = -(-n // (-(-n // ncols)))
        if ncols == self._ncols:
            return
        self._ncols = ncols
        for i in range(30):
            cf.columnconfigure(i, weight=0, uniform="")
            cf.rowconfigure(i, weight=0, uniform="")
        for i, f in enumerate(self.card_frames):
            r, c = divmod(i, ncols)
            f.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
        for c in range(ncols):
            cf.columnconfigure(c, weight=1, uniform="c")
        for r in range(-(-n // ncols)):
            cf.rowconfigure(r, weight=1, uniform="r")

    def on_canvas(self, event=None):
        self.rescale()
        self.fit_inner()

    def fit_inner(self):
        """Overview fills the window, but grows (and scrolls) if the cards need more room."""
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 50:
            return
        h = max(ch, self.inner.winfo_reqheight())
        if (cw, h) != self._fit:
            self._fit = (cw, h)
            self.canvas.itemconfigure(self.win, width=cw, height=h)
            self.canvas.configure(scrollregion=(0, 0, cw, h))

    def on_wheel(self, e):
        if str(self.nb.select()) != str(self.dash) or e.widget is self.attn:
            return
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
        self.canvas.yview_scroll(-3 if up else 3, "units")

    def rescale(self, event=None):
        """Bigger window = bigger card text; text wraps to the card width."""
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 100:
            return
        s = max(1.0, min(2.2, w / 1000, h / 620))
        for f, base in ((self.f_big, 22), (self.f_lab, 10), (self.f_title, 11)):
            if f.cget("size") != int(base * s):
                f.configure(size=int(base * s))
        self.layout_cards(int((w - 24) / (200 * s)))
        wrap = max(110, int((w - 24) / self._ncols) - 12 - 32 - 14)
        for ms in self.card_widgets:
            for _, v, lab in ms:
                v.configure(wraplength=wrap)
                lab.configure(wraplength=wrap)
        for t in self.card_titles:
            t.configure(wraplength=wrap)
        self.after(30, self.fit_inner)

    def customise_cards(self):
        res = CardsDialog(self).result
        if res:
            self.cards_cfg = res
            self.db.set_setting("cards", res)
            self.rebuild_cards()
            self.update_dashboard()

    # ---------- theme
    def apply_theme(self):
        p, st, fam = self.pal, self.style, self.fam
        self.configure(bg=p["bg"])
        self.canvas.configure(bg=p["bg"])
        st.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                     bordercolor=p["card"], lightcolor=p["bg"], darkcolor=p["bg"], font=(fam, 10))
        st.configure("TFrame", background=p["bg"])
        st.configure("TLabel", background=p["bg"], foreground=p["fg"])
        st.configure("Title.TLabel", font=(fam, 18, "bold"))
        st.configure("Head.TLabel", font=(fam, 12, "bold"))
        st.configure("Hint.TLabel", foreground="#888888")
        st.configure("Card.TFrame", background=p["card"])
        st.configure("Card.TLabel", background=p["card"], foreground=p["fg"], font=self.f_lab)
        st.configure("CardTitle.TLabel", background=p["card"], foreground="#8a8f98", font=self.f_title)
        st.configure("Big.TLabel", background=p["card"], foreground=p["accent"], font=self.f_big)
        st.configure("TButton", background=p["card"], foreground=p["fg"], padding=6, borderwidth=1)
        st.map("TButton", background=[("active", p["field"])])
        st.configure("Accent.TButton", background=p["accent"], foreground="#ffffff")
        st.map("Accent.TButton", background=[("active", p["accent"])])
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"])
        st.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"], background=p["card"],
                     arrowcolor=p["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["fg"])])
        st.configure("TNotebook", background=p["bg"], borderwidth=0, bordercolor=p["bg"],
                     lightcolor=p["bg"], darkcolor=p["bg"])
        st.configure("TNotebook.Tab", background=p["card"], foreground=p["fg"], padding=(14, 8),
                     bordercolor=p["bg"], lightcolor=p["card"], darkcolor=p["card"])
        st.map("TNotebook.Tab", background=[("selected", p["accent"])], foreground=[("selected", "#ffffff")])
        st.configure("Treeview", background=p["card"], fieldbackground=p["card"], foreground=p["fg"],
                     rowheight=self.font.metrics("linespace") + 20, borderwidth=0)
        st.configure("Treeview.Heading", background=p["field"], foreground=p["fg"], font=(fam, 10, "bold"),
                     padding=(8, 8))
        st.configure("Toolbutton", background=p["card"], foreground=p["fg"], padding=(12, 6), borderwidth=1)
        st.map("Toolbutton", background=[("selected", p["accent"]), ("active", p["field"])],
               foreground=[("selected", "#ffffff")])
        st.map("Treeview", background=[("selected", p["accent"])], foreground=[("selected", "#ffffff")])
        for sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            st.configure(sb, background=p["field"], troughcolor=p["bg"], arrowcolor=p["fg"],
                         bordercolor=p["bg"], lightcolor=p["field"], darkcolor=p["field"])
        st.configure("Treeview.Heading", bordercolor=p["bg"], lightcolor=p["field"], darkcolor=p["field"])
        st.configure("Treeview", bordercolor=p["bg"], lightcolor=p["card"], darkcolor=p["card"])
        st.configure("TButton", bordercolor=p["bg"], lightcolor=p["card"], darkcolor=p["card"])
        st.configure("Toolbutton", bordercolor=p["bg"], lightcolor=p["card"], darkcolor=p["card"])
        st.configure("TEntry", bordercolor=p["card"], lightcolor=p["field"], darkcolor=p["field"])
        for t in [s.tree for s in self.sections] + [self.attn]:
            for tag in ("bad", "warn", "ok", "row0", "row1"):
                t.tag_configure(tag, background=p[tag], foreground=p["fg"])
        self.option_add("*TCombobox*Listbox.background", p["field"])
        self.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.theme_btn.configure(text="☀ Light mode" if self.dark else "🌙 Dark mode")

    def toggle_theme(self):
        self.dark = not self.dark
        self.pal = PALETTES["dark" if self.dark else "light"]
        self.apply_theme()
        self.db.set_setting("dark", self.dark)

    # ---------- logic
    def settings_changed(self):
        self.db.set_setting("window", self.window.get())
        self.refresh_all()

    def changed(self):
        self.reload()
        self.refresh_all()

    def refresh_all(self):
        for sec in self.sections:
            sec.refresh()
        self.update_dashboard()

    def update_dashboard(self):
        today, window = date.today(), WINDOWS[self.window.get()]
        people = self.data["people"]
        n = len(people)
        need = sum(p["kcal_per_day"] for p in people)
        need_w = sum(p["water_per_day"] for p in people)
        kcal = litres = food_units = med_units = n_expired = 0
        attention = []
        for it in self.data["consumables"]:
            exp = parse_date(it["expiry"])
            days = (exp - today).days if exp else None
            expired = days is not None and days < 0
            if expired:
                n_expired += 1
            else:
                if it["type"] == "Food":
                    kcal += it["qty"] * it["kcal"]
                    food_units += it["qty"]
                elif it["type"] == "Water":
                    litres += it["qty"] * it["litres"]
                else:
                    med_units += it["qty"]
            if days is not None and days <= window:
                info = f"EXPIRED {-days} d ago" if expired else f"expires in {days} d ({exp})"
                attention.append((days, (it["name"], it["type"], info, it["location"]),
                                  "bad" if expired else "warn"))
        n_exp, overdue = len(attention), 0
        for it in self.data["maintenance"]:
            last, every = parse_date(it["last_checked"]), it["every_days"]
            if not last:
                overdue += 1
                attention.append((-9999, (it["name"], "Check", "never checked", it["location"]), "bad"))
            elif every and (last + timedelta(days=every) - today).days < 0:
                late = (today - last - timedelta(days=every)).days
                overdue += 1
                attention.append((-1000 - late, (it["name"], "Check", f"check overdue by {late} d",
                                                 it["location"]), "bad"))
        missing = [it for it in self.data["gear"] if it["owned"] < (it["required"] or 1)]
        for it in missing:
            req = it["required"] or 1
            attention.append((9999, (it["name"], "Gear", f"have {it['owned']} of {req} (need {req - it['owned']} more)",
                                     it["location"]), "warn"))

        def days_of(have, per_day):
            return f"{have / per_day:.1f} days" if per_day else "-"
        who = f"for {n} {'person' if n == 1 else 'people'}" if n else "(add people on the People tab)"
        m = {
            "food_days": (days_of(kcal, need), f"of food {who}"),
            "food_kcal": (f"{kcal:,.0f}", "kcal of food in date"),
            "food_units": (f"{food_units:,}", "food units in date"),
            "water_litres": (f"{litres:,.1f} L", "of water in date"),
            "water_days": (days_of(litres, need_w), f"of water {who}"),
            "med_units": (f"{med_units:,}", "medicine units in date"),
            "expiring_count": (str(n_exp), f"item(s) expired or expiring within {self.window.get()}"),
            "expired_count": (str(n_expired), "item(s) already expired"),
            "checks_overdue": (f"{overdue}/{len(self.data['maintenance'])}", "routine checks overdue"),
            "gear_missing": (str(len(missing)), "gear item(s) below required number"),
            "people_count": (str(n), "people"),
            "daily_kcal": (f"{need:,}", "kcal needed per day, everyone"),
            "daily_water": (f"{need_w:g} L", "of water needed per day, everyone"),
        }
        for ms in self.card_widgets:
            for key, v, lab in ms:
                val, text = m.get(key, ("-", key))
                v.config(text=val)
                lab.config(text=text)

        self.attn.delete(*self.attn.get_children())
        for _, vals, tag in sorted(attention, key=lambda a: a[0]):
            self.attn.insert("", "end", values=vals, tags=(tag,))
        if not attention:
            self.attn.insert("", "end", values=("✔ Nothing needs attention right now", "", "", ""), tags=("ok",))
        self.after(30, self.fit_inner)

    # ---------- CSV
    def export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv", filetypes=[("CSV files", "*.csv")],
            initialfile=f"prepstock_backup_{date.today().isoformat()}.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
                w.writeheader()
                for t, rows in self.data.items():
                    for r in rows:
                        w.writerow({"section": t, **{k: v for k, v in r.items() if k != "id"}})
        except OSError as e:
            return messagebox.showerror("Export failed", str(e))
        messagebox.showinfo("Exported", f"Saved to:\n{path}")

    def import_csv(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            return messagebox.showerror("Import failed", str(e))
        rows = [r for r in rows if r.get("section") in SECTIONS and (r.get("name") or "").strip()]
        if not rows:
            return messagebox.showwarning("Nothing to import", "No valid rows found. Use a file made by Export CSV.")
        choice = messagebox.askyesnocancel(
            "Import CSV", f"Found {len(rows)} item(s).\n\nYes = replace existing data in those sections\n"
                          "No = add to existing data\nCancel = abort")
        if choice is None:
            return
        if choice:
            for t in {r["section"] for r in rows}:
                self.db.clear(t)
        for r in rows:
            item = {}
            for c in SECTIONS[r["section"]]:
                raw = (r.get(c) or "").strip()
                try:
                    item[c] = int(float(raw or 0)) if c in INT_COLS else float(raw or 0) if c in REAL_COLS else raw
                except ValueError:
                    item[c] = blank(c)
            if r["section"] == "gear":  # older exports used a Yes/No "have" column
                if not (r.get("owned") or "").strip() and not (r.get("required") or "").strip():
                    item["owned"] = 0 if (r.get("have") or "Yes").strip() == "No" else 1
                if not item.get("required"):
                    item["required"] = 1
            if r["section"] == "people" and not item.get("water_per_day"):
                item["water_per_day"] = 3.0
            if item.get("expiry") and not parse_date(item["expiry"]):
                item["expiry"] = ""
            if "last_checked" in item and item["last_checked"] and not parse_date(item["last_checked"]):
                item["last_checked"] = ""
            if "type" in item and item["type"] not in TYPES:
                item["type"] = "Food"
            if "have" in item and item["have"] not in ("Yes", "No"):
                item["have"] = "Yes"
            self.db.add(r["section"], item)
        self.changed()
        messagebox.showinfo("Imported", f"Imported {len(rows)} item(s).")

    def on_close(self):
        self.db.c.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()