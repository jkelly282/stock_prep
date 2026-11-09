#!/usr/bin/env python3
"""PrepStock - emergency / preparedness inventory manager.

Pure standard library (Tkinter). Run with:  python prepstock.py
Data is saved automatically to ~/.prepstock.json
"""
import json
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

DATA_FILE = Path.home() / ".prepstock.json"
WINDOWS = {"1 week": 7, "2 weeks": 14, "1 month": 30, "3 months": 91,
           "6 months": 182, "1 year": 365}
PALETTES = {
    "light": dict(bg="#f4f5f7", fg="#1c1c1e", card="#ffffff", accent="#2f6fed",
                  field="#ffffff", warn="#fff1cc", bad="#ffd9d6", ok="#dcf3e1"),
    "dark": dict(bg="#1e1f24", fg="#e8e8ea", card="#2a2c33", accent="#5b95ff",
                 field="#33363e", warn="#5a4a1c", bad="#6a2a28", ok="#24472f"),
}
DEFAULTS = {
    "settings": {"people": 2, "kcal_per_day": 2000, "water_per_day": 3.0,
                 "window": "1 month", "dark": False},
    "consumables": [], "maintenance": [], "gear": [],
}


def parse_date(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def load():
    data = json.loads(json.dumps(DEFAULTS))
    try:
        stored = json.loads(DATA_FILE.read_text())
        data["settings"].update(stored.get("settings", {}))
        for k in ("consumables", "maintenance", "gear"):
            data[k] = stored.get(k, [])
    except (OSError, ValueError):
        pass
    return data


class ItemDialog(tk.Toplevel):
    """Generic add/edit form. fields = (key, label, kind, options)."""

    def __init__(self, app, title, fields, item=None):
        super().__init__(app)
        self.title(title)
        self.result = None
        self.fields, self.vars = fields, {}
        self.configure(bg=app.pal["bg"])
        self.transient(app)
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        for r, (key, label, kind, opts) in enumerate(fields):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            val = (item or {}).get(key, opts.get("default", "") if isinstance(opts, dict) else "")
            var = tk.StringVar(value=str(val))
            self.vars[key] = var
            if kind == "choice":
                w = ttk.Combobox(frm, textvariable=var, values=opts["choices"],
                                 state="readonly", width=28)
            else:
                w = ttk.Entry(frm, textvariable=var, width=31)
            w.grid(row=r, column=1, sticky="ew", pady=4)
            if kind == "date":
                ttk.Label(frm, text="YYYY-MM-DD", style="Hint.TLabel").grid(row=r, column=2, padx=6)
        btns = ttk.Frame(frm)
        btns.grid(row=len(fields), column=0, columnspan=3, pady=(12, 0), sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def save(self):
        out = {}
        for key, label, kind, opts in self.fields:
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


class Section(ttk.Frame):
    def __init__(self, app, key, columns, fields, row_fn, extra_buttons=()):
        super().__init__(app.nb, padding=10)
        self.app, self.key, self.fields, self.row_fn = app, key, fields, row_fn
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="+ Add", style="Accent.TButton", command=self.add).pack(side="left")
        ttk.Button(bar, text="Edit", command=self.edit).pack(side="left", padx=6)
        ttk.Button(bar, text="Delete", command=self.delete).pack(side="left")
        for text, fn in extra_buttons:
            ttk.Button(bar, text=text, command=lambda f=fn: self.act(f)).pack(side="left", padx=(12, 0))
        cols = [c[0] for c in columns]
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, head, w in columns:
            self.tree.heading(c, text=head)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.edit())

    @property
    def items(self):
        return self.app.data[self.key]

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(self.items):
            values, tag = self.row_fn(item, self.app)
            self.tree.insert("", "end", iid=str(i), values=values, tags=(tag,))

    def selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add(self):
        res = ItemDialog(self.app, "Add item", self.fields).result
        if res:
            self.items.append(res)
            self.app.changed()

    def edit(self):
        i = self.selected()
        if i is None:
            return
        res = ItemDialog(self.app, "Edit item", self.fields, self.items[i]).result
        if res:
            self.items[i] = res
            self.app.changed()

    def delete(self):
        i = self.selected()
        if i is not None and messagebox.askyesno("Delete", f"Delete '{self.items[i]['name']}'?"):
            del self.items[i]
            self.app.changed()

    def act(self, fn):
        i = self.selected()
        if i is not None:
            fn(self.items[i])
            self.app.changed()


# ---------- row formatters: return (values, colour-tag) ----------
def expiry_status(item, app):
    exp = parse_date(item.get("expiry", ""))
    if not exp:
        return "", "", None
    days = (exp - date.today()).days
    window = WINDOWS[app.data["settings"]["window"]]
    tag = "bad" if days < 0 else "warn" if days <= window else ""
    return exp.isoformat(), tag, days


def consumable_row(it, app):
    exp, tag, days = expiry_status(it, app)
    left = "" if days is None else ("EXPIRED" if days < 0 else f"{days} d")
    size = []
    if it["type"] == "Food" and it.get("kcal"):
        size.append(f"{it['kcal']} kcal")
    if it["type"] == "Water" and it.get("litres"):
        size.append(f"{it['litres']} L")
    return (it["name"], it["type"], it["qty"], ", ".join(size), exp or "-", left,
            it.get("location", ""), it.get("notes", "")), tag


def maintenance_row(it, app):
    last = parse_date(it.get("last_checked", ""))
    every = it.get("every_days", 0)
    if last and every:
        due = last + timedelta(days=every)
        left = (due - date.today()).days
        status = "OVERDUE" if left < 0 else f"due in {left} d"
        tag = "bad" if left < 0 else "warn" if left <= 14 else "ok"
        due_s = due.isoformat()
    else:
        status, tag, due_s = ("never checked", "bad", "-") if not last else ("no schedule", "", "-")
    return (it["name"], it.get("last_checked") or "-", every or "-", due_s, status,
            it.get("location", ""), it.get("notes", "")), tag


def gear_row(it, app):
    have = it.get("have") == "Yes"
    return (it["name"], "✔ Have" if have else "✘ Missing", it.get("location", ""),
            it.get("notes", "")), "ok" if have else "bad"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PrepStock - Emergency Supplies Inventory")
        self.geometry("1050x700")
        self.minsize(850, 560)
        self.data = load()
        self.s = self.data["settings"]
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.pal = PALETTES["dark" if self.s["dark"] else "light"]
        self.build()
        self.apply_theme()
        self.refresh_all()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------
    def build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        ttk.Label(top, text="PrepStock", style="Title.TLabel").pack(side="left")
        self.theme_btn = ttk.Button(top, command=self.toggle_theme)
        self.theme_btn.pack(side="right")
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=12)

        self.dash = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.dash, text="  Overview  ")
        self.build_dashboard()

        T = ["Food", "Water", "Medicine"]
        self.sections = [
            Section(self, "consumables",
                    [("name", "Item", 190), ("type", "Type", 70), ("qty", "Qty", 45),
                     ("size", "Size (each)", 90), ("exp", "Expiry", 90), ("left", "Left", 70),
                     ("loc", "Location", 120), ("notes", "Notes", 200)],
                    [("name", "Name", "text", {"required": True}),
                     ("type", "Type", "choice", {"choices": T, "default": "Food"}),
                     ("qty", "Quantity", "int", {"default": 1}),
                     ("kcal", "Calories per unit (food)", "int", {}),
                     ("litres", "Litres per unit (water)", "float", {}),
                     ("expiry", "Expiry date (optional)", "date", {}),
                     ("location", "Location", "text", {}),
                     ("notes", "Notes", "text", {})],
                    consumable_row),
            Section(self, "maintenance",
                    [("name", "Item", 200), ("last", "Last checked", 100), ("every", "Every (days)", 90),
                     ("due", "Next due", 90), ("status", "Status", 100),
                     ("loc", "Location", 120), ("notes", "Notes", 200)],
                    [("name", "Name", "text", {"required": True}),
                     ("last_checked", "Last checked", "date", {"default": date.today().isoformat()}),
                     ("every_days", "Check every (days)", "int", {"default": 90}),
                     ("location", "Location", "text", {}),
                     ("notes", "Notes", "text", {})],
                    maintenance_row,
                    [("Mark checked today",
                      lambda it: it.update(last_checked=date.today().isoformat()))]),
            Section(self, "gear",
                    [("name", "Item", 240), ("have", "Status", 100),
                     ("loc", "Location", 150), ("notes", "Notes", 300)],
                    [("name", "Name", "text", {"required": True}),
                     ("have", "Have it?", "choice", {"choices": ["Yes", "No"], "default": "Yes"}),
                     ("location", "Location", "text", {}),
                     ("notes", "Notes", "text", {})],
                    gear_row,
                    [("Toggle have/missing",
                      lambda it: it.update(have="No" if it.get("have") == "Yes" else "Yes"))]),
        ]
        for sec, label in zip(self.sections, ["Food, Water && Medicine", "Routine Checks",
                                               "Other Gear Checklist"]):
            self.nb.add(sec, text=f"  {label.replace('&&', '&')}  ")

    def build_dashboard(self):
        d = self.dash
        bar = ttk.Frame(d)
        bar.pack(fill="x")
        self.v_people = tk.StringVar(value=str(self.s["people"]))
        self.v_kcal = tk.StringVar(value=str(self.s["kcal_per_day"]))
        self.v_water = tk.StringVar(value=str(self.s["water_per_day"]))
        self.v_window = tk.StringVar(value=self.s["window"])
        for text, var, w in [("People", self.v_people, 4), ("kcal/person/day", self.v_kcal, 6),
                             ("Water L/person/day", self.v_water, 5)]:
            ttk.Label(bar, text=text).pack(side="left", padx=(0, 4))
            e = ttk.Entry(bar, textvariable=var, width=w)
            e.pack(side="left", padx=(0, 14))
            e.bind("<KeyRelease>", lambda ev: self.settings_changed())
        ttk.Label(bar, text="Flag expiring within").pack(side="left", padx=(0, 4))
        cb = ttk.Combobox(bar, textvariable=self.v_window, values=list(WINDOWS), state="readonly", width=10)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda ev: self.settings_changed())

        cards = ttk.Frame(d)
        cards.pack(fill="x", pady=14)
        self.cards = {}
        for i, key in enumerate(["food", "water", "expiring", "checks"]):
            c = ttk.Frame(cards, style="Card.TFrame", padding=14)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            cards.columnconfigure(i, weight=1, uniform="c")
            big = ttk.Label(c, style="Big.TLabel")
            big.pack(anchor="w")
            small = ttk.Label(c, style="Card.TLabel", wraplength=210, justify="left")
            small.pack(anchor="w")
            self.cards[key] = (big, small)

        ttk.Label(d, text="Needs attention", style="Head.TLabel").pack(anchor="w", pady=(4, 4))
        cols = ("item", "kind", "info", "loc")
        self.attn = ttk.Treeview(d, columns=cols, show="headings", selectmode="none")
        for c, h, w in [("item", "Item", 260), ("kind", "Type", 110), ("info", "Detail", 260), ("loc", "Location", 160)]:
            self.attn.heading(c, text=h)
            self.attn.column(c, width=w)
        self.attn.pack(fill="both", expand=True)

    # ---------- theme ----------
    def apply_theme(self):
        p, st = self.pal, self.style
        self.configure(bg=p["bg"])
        st.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                     bordercolor=p["card"], lightcolor=p["bg"], darkcolor=p["bg"], font=("Segoe UI", 10))
        st.configure("TFrame", background=p["bg"])
        st.configure("TLabel", background=p["bg"], foreground=p["fg"])
        st.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        st.configure("Head.TLabel", font=("Segoe UI", 12, "bold"))
        st.configure("Hint.TLabel", foreground="#888888")
        st.configure("Card.TFrame", background=p["card"])
        st.configure("Card.TLabel", background=p["card"], foreground=p["fg"])
        st.configure("Big.TLabel", background=p["card"], foreground=p["accent"], font=("Segoe UI", 20, "bold"))
        st.configure("TButton", background=p["card"], foreground=p["fg"], padding=6, borderwidth=1)
        st.map("TButton", background=[("active", p["field"])])
        st.configure("Accent.TButton", background=p["accent"], foreground="#ffffff")
        st.map("Accent.TButton", background=[("active", p["accent"])])
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"])
        st.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"], background=p["card"],
                     arrowcolor=p["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["fg"])])
        st.configure("TNotebook", background=p["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", background=p["card"], foreground=p["fg"], padding=(8, 6))
        st.map("TNotebook.Tab", background=[("selected", p["accent"])], foreground=[("selected", "#ffffff")])
        st.configure("Treeview", background=p["card"], fieldbackground=p["card"], foreground=p["fg"],
                     rowheight=26, borderwidth=0)
        st.configure("Treeview.Heading", background=p["field"], foreground=p["fg"], font=("Segoe UI", 10, "bold"))
        st.map("Treeview", background=[("selected", p["accent"])], foreground=[("selected", "#ffffff")])
        st.configure("Vertical.TScrollbar", background=p["card"], troughcolor=p["bg"], arrowcolor=p["fg"])
        trees = [s.tree for s in self.sections] + [self.attn]
        for t in trees:
            for tag in ("bad", "warn", "ok"):
                t.tag_configure(tag, background=p[tag], foreground=p["fg"])
        self.option_add("*TCombobox*Listbox.background", p["field"])
        self.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.theme_btn.configure(text="☀ Light mode" if self.s["dark"] else "🌙 Dark mode")

    def toggle_theme(self):
        self.s["dark"] = not self.s["dark"]
        self.pal = PALETTES["dark" if self.s["dark"] else "light"]
        self.apply_theme()
        self.save()

    # ---------- logic ----------
    def settings_changed(self):
        try:
            self.s["people"] = max(1, int(self.v_people.get()))
            self.s["kcal_per_day"] = max(1, int(self.v_kcal.get()))
            self.s["water_per_day"] = max(0.1, float(self.v_water.get()))
        except ValueError:
            return
        self.s["window"] = self.v_window.get()
        self.refresh_all()
        self.save()

    def changed(self):
        self.refresh_all()
        self.save()

    def refresh_all(self):
        for sec in self.sections:
            sec.refresh()
        self.update_dashboard()

    def update_dashboard(self):
        s, today = self.s, date.today()
        window = WINDOWS[s["window"]]
        kcal = litres = 0.0
        attention = []
        for it in self.data["consumables"]:
            exp = parse_date(it.get("expiry", ""))
            days = (exp - today).days if exp else None
            expired = days is not None and days < 0
            if not expired:
                kcal += it["qty"] * it.get("kcal", 0) if it["type"] == "Food" else 0
                litres += it["qty"] * it.get("litres", 0) if it["type"] == "Water" else 0
            if days is not None and days <= window:
                info = f"EXPIRED {-days} d ago" if expired else f"expires in {days} d ({exp})"
                attention.append((days, (it["name"], it["type"], info, it.get("location", "")),
                                  "bad" if expired else "warn"))
        n_exp = len(attention)
        overdue = 0
        for it in self.data["maintenance"]:
            last, every = parse_date(it.get("last_checked", "")), it.get("every_days", 0)
            if not last:
                overdue += 1
                attention.append((-9999, (it["name"], "Check", "never checked", it.get("location", "")), "bad"))
            elif every and (last + timedelta(days=every) - today).days < 0:
                late = (today - last - timedelta(days=every)).days
                overdue += 1
                attention.append((-late - 1000, (it["name"], "Check", f"check overdue by {late} d",
                                                 it.get("location", "")), "bad"))
        missing = [it for it in self.data["gear"] if it.get("have") != "Yes"]
        for it in missing:
            attention.append((9999, (it["name"], "Gear", "marked as missing", it.get("location", "")), "warn"))

        food_days = kcal / (s["people"] * s["kcal_per_day"])
        water_days = litres / (s["people"] * s["water_per_day"])
        self.cards["food"][0].config(text=f"{food_days:.1f} days")
        self.cards["food"][1].config(
            text=f"of food for {s['people']} {'person' if s['people'] == 1 else 'people'}\n"
                 f"{kcal:,.0f} kcal in date, at {s['kcal_per_day']} kcal/day each")
        self.cards["water"][0].config(text=f"{litres:,.1f} L")
        self.cards["water"][1].config(text=f"water = {water_days:.1f} days\nat {s['water_per_day']} L/person/day")
        self.cards["expiring"][0].config(text=str(n_exp))
        self.cards["expiring"][1].config(text=f"item(s) expired or expiring within {s['window']}")
        total_checks = len(self.data["maintenance"])
        self.cards["checks"][0].config(text=f"{overdue}/{total_checks}")
        self.cards["checks"][1].config(text=f"routine checks overdue\n{len(missing)} gear item(s) missing")

        self.attn.delete(*self.attn.get_children())
        for _, vals, tag in sorted(attention, key=lambda a: a[0]):
            self.attn.insert("", "end", values=vals, tags=(tag,))

    def save(self):
        try:
            DATA_FILE.write_text(json.dumps(self.data, indent=2))
        except OSError as e:
            messagebox.showerror("Save failed", str(e))

    def on_close(self):
        self.save()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()