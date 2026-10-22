"""Tkinter graphical user interface for the Richfield Booking System."""
import tkinter as tk
from datetime import date, datetime
from tkinter import ttk, messagebox

from database import Database
from services import (AuthService, CampusService, ResourceService, BookingService,
                      ReportService, RESOURCE_TYPES)
from utils import AppError, parse_date, parse_time, parse_duration

# ---- colour palette ---------------------------------------------------------
BG = "#D6E4F5"          # faded blue application background
BG_BAR = "#C3D7EF"      # slightly deeper faded blue for bars / inactive tabs
CARD = "#EAF1FA"        # light card surface
NAVY = "#1B3A6B"        # headings
TEXT = "#22344F"
ACCENT = "#4F7CC0"
ACCENT_DARK = "#3A63A0"
GOOD, BAD, WARN = "#1E7B45", "#B3261E", "#B26A00"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")


# ---- reusable widgets ---------------------------------------------------------
class DataTable(ttk.Frame):
    """Treeview + scrollbar. Rows are (iid, values, tag)."""

    def __init__(self, parent, columns, height=10):
        super().__init__(parent)
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings",
                                 height=height, selectmode="browse")
        for key, heading, width in columns:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("good", foreground=GOOD)
        self.tree.tag_configure("bad", foreground=BAD)
        self.tree.tag_configure("warn", foreground=WARN)
        self.tree.tag_configure("muted", foreground="#7A8799")

    def set_rows(self, rows):
        keep = self.selected()
        self.tree.delete(*self.tree.get_children())
        for iid, values, tag in rows:
            self.tree.insert("", "end", iid=str(iid), values=values, tags=(tag,) if tag else ())
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def on_select(self, callback):
        self.tree.bind("<<TreeviewSelect>>", lambda e: callback())


class BarChart(tk.Canvas):
    """Minimal bar chart drawn on a Canvas (no third-party libraries needed)."""

    def __init__(self, parent, title, color=ACCENT, height=200):
        super().__init__(parent, height=height, bg="white", highlightthickness=1,
                         highlightbackground="#B5C7E0")
        self.title, self.color, self.data = title, color, []
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, data):
        self.data = data
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 120)
        self.create_text(w / 2, 14, text=self.title, font=FONT_BOLD, fill=NAVY)
        if not self.data or max(v for _, v in self.data) == 0:
            self.create_text(w / 2, h / 2, text="No data yet", fill="#7A8799")
            return
        left, right, top, bottom = 30, 12, 44, 34
        peak = max(v for _, v in self.data)
        slot = (w - left - right) / len(self.data)
        bar_w = min(70, slot * 0.6)
        self.create_line(left, h - bottom, w - right, h - bottom, fill="#9FB3CF")
        for i, (label, value) in enumerate(self.data):
            x0 = left + i * slot + (slot - bar_w) / 2
            y0 = (h - bottom) - (h - bottom - top) * (value / peak)
            self.create_rectangle(x0, y0, x0 + bar_w, h - bottom, fill=self.color, outline="")
            self.create_text(x0 + bar_w / 2, y0 - 8, text=f"{value:g}", font=("Segoe UI", 9), fill=TEXT)
            short = label if len(label) <= 16 else label[:15] + "…"
            self.create_text(x0 + bar_w / 2, h - bottom + 14, text=short, font=("Segoe UI", 8), fill=TEXT)


def booking_rows(bookings, show_lecturer=False, show_campus=False):
    tags = {"Upcoming": "good", "In progress": "warn", "Completed": "muted", "Cancelled": "bad"}
    rows = []
    for b in bookings:
        values = [b["id"], b["booking_date"], b["start_time"], b["end_time"], b["duration_min"],
                  f"{b['resource_code']} - {b['resource_name']}"]
        if show_campus:
            values.append(b["campus_name"])
        if show_lecturer:
            values.append(b["lecturer_name"])
        values.append(b["state"])
        rows.append((b["id"], values, tags.get(b["state"])))
    return rows


def booking_columns(show_lecturer=False, show_campus=False):
    cols = [("id", "ID", 45), ("date", "Date", 90), ("start", "Start", 60), ("end", "End", 60),
            ("dur", "Min", 50), ("res", "Resource", 230)]
    if show_campus:
        cols.append(("campus", "Campus", 160))
    if show_lecturer:
        cols.append(("lecturer", "Lecturer", 150))
    cols.append(("state", "Status", 90))
    return cols


# ---- shared report panel (campus-level reports) ---------------------------------------
class ReportPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.summary = ttk.Label(self, font=FONT_BOLD, foreground=NAVY, justify="left")
        self.summary.pack(anchor="w", pady=(0, 8))
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True)
        left = ttk.LabelFrame(mid, text="Most frequently used resources", padding=6)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.top_table = DataTable(left, [("rank", "#", 35), ("res", "Resource", 260),
                                          ("n", "Bookings", 70)], height=8)
        self.top_table.pack(fill="both", expand=True)
        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True)
        self.chart_res = BarChart(right, "Bookings per resource", height=170)
        self.chart_res.pack(fill="both", expand=True, pady=(0, 6))
        self.chart_day = BarChart(right, "Usage trend by weekday", color="#6FA0D8", height=170)
        self.chart_day.pack(fill="both", expand=True)

    def refresh(self):
        campus = self.app.campus
        rep = self.app.reports.campus_report(campus.id)
        top = rep["top_resources"][0] if rep["top_resources"] else ("-", 0)
        self.summary.config(text=(
            f"{campus.name}   |   Total bookings: {rep['total']}   |   "
            f"Average duration: {rep['average_duration']} min   |   "
            f"Cancelled: {rep['cancelled']}   |   Policy rejections: {rep['rejected']}   |   "
            f"Most used: {top[0]}"))
        self.top_table.set_rows([(i, (i + 1, name, n), None)
                                 for i, (name, n) in enumerate(rep["top_resources"])])
        self.chart_res.set_data([(name.split("  ")[0].split("-", 1)[-1], n)
                                 for name, n in rep["top_resources"]])
        self.chart_day.set_data(rep["by_weekday"])


# ---- role views ---------------------------------------------------------------------
class LecturerView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.book_tab = ttk.Frame(nb, padding=10)
        self.my_tab = ttk.Frame(nb, padding=10)
        nb.add(self.book_tab, text="  Resources & Booking  ")
        nb.add(self.my_tab, text="  My Bookings  ")
        self._build_book_tab()
        self._build_my_tab()
        self.refresh()

    # -- booking tab
    def _build_book_tab(self):
        form = ttk.LabelFrame(self.book_tab, text="New booking", padding=10)
        form.pack(fill="x")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.start_var = tk.StringVar(value="09:00")
        self.dur_var = tk.StringVar(value="60")
        for col, (label, widget) in enumerate([
            ("Date (YYYY-MM-DD)", ttk.Entry(form, textvariable=self.date_var, width=14)),
            ("Start (HH:MM)", ttk.Entry(form, textvariable=self.start_var, width=8)),
            ("Duration (minutes)", ttk.Spinbox(form, from_=15, to=480, increment=15,
                                               textvariable=self.dur_var, width=8)),
        ]):
            ttk.Label(form, text=label).grid(row=0, column=col, sticky="w", padx=(0, 14))
            widget.grid(row=1, column=col, sticky="w", padx=(0, 14))
        ttk.Button(form, text="Book selected resource", command=self.book).grid(
            row=1, column=3, padx=(10, 0))
        ttk.Label(form, text=f"Policy in effect:  {self.app.campus.policy.summary()}",
                  wraplength=900, foreground=NAVY, font=FONT_BOLD).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))
        for var in (self.date_var, self.start_var, self.dur_var):
            var.trace_add("write", lambda *a: self.refresh_resources())

        ttk.Label(self.book_tab, text="Resources on this campus (select one, then press Book). "
                                      "'Slot' shows availability for the date/time entered above.").pack(
            anchor="w", pady=(12, 4))
        self.res_table = DataTable(self.book_tab, [
            ("code", "Code", 110), ("name", "Resource", 230), ("type", "Type", 170),
            ("now", "Right now", 110), ("slot", "Selected slot", 120)], height=12)
        self.res_table.pack(fill="both", expand=True)

    def _slot(self):
        try:
            return parse_date(self.date_var.get()), parse_time(self.start_var.get()), \
                   parse_duration(self.dur_var.get())
        except AppError:
            return None

    def refresh_resources(self):
        svc, slot, now = self.app.bookings, self._slot(), datetime.now()
        rows = []
        for r in self.app.resources.list_for_campus(self.app.campus.id):
            now_status = svc.resource_status(r, now)
            slot_status = svc.slot_status(r, *slot) if slot else "-"
            tag = {"Free": "good", "Booked": "bad", "Out of service": "warn"}.get(slot_status)
            rows.append((r["id"], (r["resource_code"], r["name"], r["resource_type"],
                                   now_status, slot_status), tag))
        self.res_table.set_rows(rows)

    def book(self):
        try:
            self.app.bookings.create(self.app.user, self.app.campus, self.res_table.selected(),
                                     self.date_var.get(), self.start_var.get(), self.dur_var.get())
        except AppError as e:
            messagebox.showwarning("Booking not possible", str(e))
            return
        messagebox.showinfo("Booking confirmed", "Your booking has been saved.")
        self.refresh()

    # -- my bookings tab
    def _build_my_tab(self):
        bar = ttk.Frame(self.my_tab)
        bar.pack(fill="x", pady=(0, 8))
        self.filter_var = tk.StringVar(value="active")
        for text, value in [("Active bookings", "active"), ("Past history", "past"),
                            ("Everything", "all")]:
            ttk.Radiobutton(bar, text=text, value=value, variable=self.filter_var,
                            command=self.refresh_my).pack(side="left", padx=(0, 14))
        ttk.Button(bar, text="Cancel selected booking", command=self.cancel).pack(side="right")
        self.my_table = DataTable(self.my_tab, booking_columns(show_campus=True), height=14)
        self.my_table.pack(fill="both", expand=True)

    def refresh_my(self):
        bookings = self.app.bookings.for_lecturer(self.app.user.id)
        mode = self.filter_var.get()
        if mode == "active":
            bookings = [b for b in bookings if b["state"] in ("Upcoming", "In progress")]
        elif mode == "past":
            bookings = [b for b in bookings if b["state"] in ("Completed", "Cancelled")]
        self.my_table.set_rows(booking_rows(bookings, show_campus=True))

    def cancel(self):
        iid = self.my_table.selected()
        if not iid:
            messagebox.showinfo("Cancel booking", "Select a booking first.")
            return
        if not messagebox.askyesno("Cancel booking", "Cancel this booking?"):
            return
        try:
            self.app.bookings.cancel(self.app.user, int(iid))
        except AppError as e:
            messagebox.showwarning("Cannot cancel", str(e))
            return
        self.refresh()

    def refresh(self):
        self.refresh_resources()
        self.refresh_my()


class AdminView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._rows = {}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.res_tab = ttk.Frame(nb, padding=10)
        self.book_tab = ttk.Frame(nb, padding=10)
        self.report = ReportPanel(nb, app)
        nb.add(self.res_tab, text="  Manage Resources  ")
        nb.add(self.book_tab, text="  Campus Bookings  ")
        nb.add(self.report, text="  Usage & Trends  ")
        self._build_resources()
        self.book_table = DataTable(self.book_tab, booking_columns(show_lecturer=True), height=16)
        self.book_table.pack(fill="both", expand=True)
        self.refresh()

    def _build_resources(self):
        form = ttk.LabelFrame(self.res_tab, text="Resource details", padding=10)
        form.pack(fill="x")
        self.code_var, self.name_var = tk.StringVar(), tk.StringVar()
        self.type_var, self.avail_var = tk.StringVar(value=RESOURCE_TYPES[0]), tk.BooleanVar(value=True)
        self.code_entry = ttk.Entry(form, textvariable=self.code_var, width=16)
        fields = [("Unique code", self.code_entry),
                  ("Name", ttk.Entry(form, textvariable=self.name_var, width=28)),
                  ("Type", ttk.Combobox(form, textvariable=self.type_var, values=RESOURCE_TYPES,
                                        state="readonly", width=22)),
                  ("", ttk.Checkbutton(form, text="In service", variable=self.avail_var))]
        for col, (label, widget) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=0, column=col, sticky="w", padx=(0, 12))
            widget.grid(row=1, column=col, sticky="w", padx=(0, 12))
        btns = ttk.Frame(form)
        btns.grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))
        for text, cmd in [("Add resource", self.add), ("Update selected", self.update),
                          ("Remove selected", self.remove), ("Clear form", self.clear)]:
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=(0, 8))
        ttk.Label(form, text="The code is the resource's permanent unique identifier "
                             "(duplicates are rejected).", foreground=NAVY).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.res_table = DataTable(self.res_tab, [("code", "Code", 120), ("name", "Resource", 240),
                                                  ("type", "Type", 170), ("status", "Status", 120)],
                                   height=12)
        self.res_table.pack(fill="both", expand=True, pady=(10, 0))
        self.res_table.on_select(self.load_selected)

    def load_selected(self):
        iid = self.res_table.selected()
        r = self._rows.get(int(iid)) if iid else None
        if r:
            self.code_var.set(r["resource_code"])
            self.name_var.set(r["name"])
            self.type_var.set(r["resource_type"])
            self.avail_var.set(bool(r["available"]))

    def clear(self):
        self.code_var.set("")
        self.name_var.set("")
        self.type_var.set(RESOURCE_TYPES[0])
        self.avail_var.set(True)
        self.res_table.tree.selection_remove(self.res_table.tree.selection())

    def _do(self, action, success):
        try:
            action()
        except AppError as e:
            messagebox.showwarning("Resource", str(e))
            return
        messagebox.showinfo("Resource", success)
        self.refresh()

    def _selected_id(self):
        iid = self.res_table.selected()
        if not iid:
            raise AppError("Select a resource in the list first.")
        return int(iid)

    def add(self):
        self._do(lambda: self.app.resources.add(self.app.campus.id, self.code_var.get(),
                                                self.name_var.get(), self.type_var.get()),
                 "Resource added.")

    def update(self):
        self._do(lambda: self.app.resources.update(self._selected_id(), self.name_var.get(),
                                                   self.type_var.get(), self.avail_var.get()),
                 "Resource updated.")

    def remove(self):
        if self.res_table.selected() and not messagebox.askyesno("Remove", "Remove this resource?"):
            return
        self._do(lambda: self.app.resources.remove(self._selected_id()), "Resource removed.")

    def refresh(self):
        rows = self.app.resources.list_for_campus(self.app.campus.id)
        self._rows = {r["id"]: r for r in rows}
        out = []
        for r in rows:
            status = self.app.bookings.resource_status(r)
            out.append((r["id"], (r["resource_code"], r["name"], r["resource_type"], status),
                        {"Available": "good", "In use now": "warn", "Out of service": "bad"}[status]))
        self.res_table.set_rows(out)
        self.book_table.set_rows(booking_rows(self.app.bookings.for_campus(self.app.campus.id),
                                              show_lecturer=True))
        self.report.refresh()


class OperatorView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.book_tab = ttk.Frame(nb, padding=10)
        self.res_tab = ttk.Frame(nb, padding=10)
        self.report = ReportPanel(nb, app)
        self.cmp_tab = ttk.Frame(nb, padding=10)
        nb.add(self.book_tab, text="  All Bookings  ")
        nb.add(self.res_tab, text="  All Resources  ")
        nb.add(self.report, text="  Campus Report  ")
        nb.add(self.cmp_tab, text="  Cross-Campus Comparison  ")

        bar = ttk.Frame(self.book_tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Show campus:").pack(side="left")
        self.filter_var = tk.StringVar(value="All campuses")
        names = ["All campuses"] + [c.name for c in app.campuses.values()]
        box = ttk.Combobox(bar, textvariable=self.filter_var, values=names, state="readonly", width=28)
        box.pack(side="left", padx=8)
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh_bookings())
        self.book_table = DataTable(self.book_tab, booking_columns(True, True), height=16)
        self.book_table.pack(fill="both", expand=True)

        self.res_table = DataTable(self.res_tab, [("campus", "Campus", 180), ("code", "Code", 130),
                                                  ("name", "Resource", 230), ("type", "Type", 170),
                                                  ("status", "Status", 110)], height=18)
        self.res_table.pack(fill="both", expand=True)

        self.cmp_table = DataTable(self.cmp_tab, [
            ("campus", "Campus", 160), ("max", "Max min", 65), ("hours", "Hours", 95),
            ("adv", "Days ahead", 80), ("res", "Resources", 75), ("bk", "Bookings", 70),
            ("avg", "Avg min", 65), ("per", "Per resource", 90), ("canc", "Cancelled", 70),
            ("rej", "Rejected", 70)], height=4)
        self.cmp_table.pack(fill="x")
        self.insights = tk.Text(self.cmp_tab, height=8, wrap="word", bg="white", relief="flat",
                                font=FONT, fg=TEXT, padx=8, pady=6)
        self.insights.pack(side="bottom", fill="x")
        charts = ttk.Frame(self.cmp_tab)
        charts.pack(fill="both", expand=True, pady=8)
        self.chart_total = BarChart(charts, "Total bookings per campus", height=150)
        self.chart_avg = BarChart(charts, "Average booking duration (min)", color="#6FA0D8", height=150)
        self.chart_rej = BarChart(charts, "Policy rejections", color="#C98A4B", height=150)
        for ch in (self.chart_total, self.chart_avg, self.chart_rej):
            ch.pack(side="left", fill="both", expand=True, padx=3)
        self.refresh()

    def refresh_bookings(self):
        bookings = self.app.bookings.for_all()
        if self.filter_var.get() != "All campuses":
            bookings = [b for b in bookings if b["campus_name"] == self.filter_var.get()]
        self.book_table.set_rows(booking_rows(bookings, True, True))

    def refresh_comparison(self):
        data = self.app.reports.comparison()
        rows, lines = [], []
        for d in data:
            p, r, c = d["policy"], d["report"], d["campus"]
            rows.append((c.id, (c.name, p.max_duration, f"{p.open_minute // 60:02d}:{p.open_minute % 60:02d}-"
                                f"{p.close_minute // 60:02d}:{p.close_minute % 60:02d}", p.max_advance_days,
                                r["resources"], r["total"], r["average_duration"], d["per_resource"],
                                r["cancelled"], r["rejected"]), None))
        self.cmp_table.set_rows(rows)
        short = lambda d: d["campus"].city
        self.chart_total.set_data([(short(d), d["report"]["total"]) for d in data])
        self.chart_avg.set_data([(short(d), d["report"]["average_duration"]) for d in data])
        self.chart_rej.set_data([(short(d), d["report"]["rejected"]) for d in data])
        if data and any(d["report"]["total"] for d in data):
            busiest = max(data, key=lambda d: d["report"]["total"])
            longest = max(data, key=lambda d: d["report"]["average_duration"])
            strict = max(data, key=lambda d: d["report"]["rejected"])
            lines.append(f"- Busiest campus: {busiest['campus'].name} ({busiest['report']['total']} bookings).")
            lines.append(f"- Longest average sessions: {longest['campus'].name} "
                         f"({longest['report']['average_duration']} min, policy cap {longest['policy'].max_duration} min).")
            if strict["report"]["rejected"]:
                lines.append(f"- Most policy rejections: {strict['campus'].name} "
                             f"({strict['report']['rejected']}) - its rules block the most attempted bookings.")
            for d in data:
                note = d["policy"].priority_note or "no special priority rules"
                lines.append(f"- {d['campus'].city}: {d['policy'].max_duration}-min cap, "
                             f"{d['policy'].max_advance_days}-day window, {note}.")
        else:
            lines.append("No booking data available yet.")
        self.insights.config(state="normal")
        self.insights.delete("1.0", "end")
        self.insights.insert("1.0", "Insights - how campus rules shape usage\n" + "\n".join(lines))
        self.insights.tag_add("h", "1.0", "1.end")
        self.insights.tag_config("h", font=FONT_BOLD, foreground=NAVY)
        self.insights.config(state="disabled")

    def refresh(self):
        self.refresh_bookings()
        rows = []
        for r in self.app.resources.list_all():
            status = self.app.bookings.resource_status(r)
            rows.append((r["id"], (r["campus_name"], r["resource_code"], r["name"],
                                   r["resource_type"], status),
                         {"Available": "good", "In use now": "warn", "Out of service": "bad"}[status]))
        self.res_table.set_rows(rows)
        self.report.refresh()
        self.refresh_comparison()


VIEWS = {"lecturer": LecturerView, "campus_admin": AdminView, "system_operator": OperatorView}


# ---- main application -------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self, db_path=None):
        super().__init__()
        self.title("Richfield Booking System")
        self.geometry("1120x740")
        self.minsize(1000, 680)
        self.configure(bg=BG)
        self.db = Database(db_path) if db_path else Database()
        self.auth = AuthService(self.db)
        self.campus_service = CampusService(self.db)
        self.resources = ResourceService(self.db)
        self.bookings = BookingService(self.db)
        self.campuses = self.campus_service.all()
        self.reports = ReportService(self.db, self.campus_service)
        self.user = self.campus = self.view = None
        self._style()

        tk.Label(self, text="Richfield Booking System", font=("Segoe UI", 28, "bold"),
                 bg=BG, fg=NAVY).pack(pady=(16, 0))
        tk.Label(self, text="Smart Campus Resource Booking", font=("Segoe UI", 11),
                 bg=BG, fg=ACCENT_DARK).pack(pady=(0, 8))
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.show_login()
        self.after(30_000, self._tick)

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, font=FONT)
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("Bar.TFrame", background=BG_BAR)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("Card.TLabel", background=CARD, foreground=TEXT)
        s.configure("CardTitle.TLabel", background=CARD, foreground=NAVY, font=("Segoe UI", 15, "bold"))
        s.configure("Bar.TLabel", background=BG_BAR, foreground=NAVY, font=FONT_BOLD)
        s.configure("TLabelframe", background=BG, bordercolor="#9FB3CF")
        s.configure("TLabelframe.Label", background=BG, foreground=NAVY, font=FONT_BOLD)
        s.configure("TButton", background=ACCENT, foreground="white", padding=(12, 6), borderwidth=0)
        s.map("TButton", background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)])
        s.configure("TCheckbutton", background=BG)
        s.configure("TRadiobutton", background=BG)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG_BAR, padding=(14, 7), foreground=NAVY)
        s.map("TNotebook.Tab", background=[("selected", "white")])
        s.configure("Treeview", background="white", fieldbackground="white", rowheight=26,
                    bordercolor="#B5C7E0")
        s.configure("Treeview.Heading", background=ACCENT, foreground="white", font=FONT_BOLD,
                    padding=5)
        s.map("Treeview.Heading", background=[("active", ACCENT_DARK)])
        s.map("Treeview", background=[("selected", "#BBD0EE")], foreground=[("selected", NAVY)])

    def _clear(self):
        self.view = None
        for child in self.body.winfo_children():
            child.destroy()

    def _tick(self):
        """Refresh live availability every 30 seconds."""
        if self.view is not None:
            try:
                self.view.refresh()
            except Exception:
                pass
        self.after(30_000, self._tick)

    def _close(self):
        self.db.close()
        self.destroy()

    # -- login / registration
    def show_login(self):
        self.user = self.campus = None
        self._clear()
        card = ttk.Frame(self.body, style="Card.TFrame", padding=30)
        card.place(relx=0.5, rely=0.42, anchor="center")
        ttk.Label(card, text="Sign in", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2,
                                                                      pady=(0, 14))
        self.u_var, self.p_var = tk.StringVar(), tk.StringVar()
        ttk.Label(card, text="Username", style="Card.TLabel").grid(row=1, column=0, sticky="w")
        user_entry = ttk.Entry(card, textvariable=self.u_var, width=30)
        user_entry.grid(row=2, column=0, columnspan=2, pady=(0, 10))
        ttk.Label(card, text="Password", style="Card.TLabel").grid(row=3, column=0, sticky="w")
        pass_entry = ttk.Entry(card, textvariable=self.p_var, show="•", width=30)
        pass_entry.grid(row=4, column=0, columnspan=2, pady=(0, 16))
        ttk.Button(card, text="Log in", command=self.do_login).grid(row=5, column=0, sticky="ew",
                                                                  padx=(0, 6))
        ttk.Button(card, text="Register as Lecturer", command=self.show_register).grid(
            row=5, column=1, sticky="ew")
        ttk.Label(card, style="Card.TLabel", foreground="#5B6B84", justify="left", text=(
            "Demo accounts:\n  lecturer / Lecturer@123\n  admin_jhb, admin_dbn, admin_cpt / Admin@123\n"
            "  operator / Operator@123")).grid(row=6, column=0, columnspan=2, sticky="w", pady=(16, 0))
        user_entry.focus()
        for w in (user_entry, pass_entry):
            w.bind("<Return>", lambda e: self.do_login())

    def do_login(self):
        try:
            self.user = self.auth.login(self.u_var.get(), self.p_var.get())
        except AppError as e:
            messagebox.showerror("Login failed", str(e))
            return
        self.show_campus_select()

    def show_register(self):
        win = tk.Toplevel(self)
        win.title("Register - Lecturer")
        win.configure(bg=CARD)
        win.transient(self)
        win.grab_set()
        frame = ttk.Frame(win, style="Card.TFrame", padding=24)
        frame.pack()
        ttk.Label(frame, text="Create lecturer account", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, pady=(0, 12))
        vars_ = {k: tk.StringVar() for k in ("name", "user", "pw", "pw2", "campus")}
        names = {c.name: c.id for c in self.campuses.values()}
        for i, (label, key, show) in enumerate([("Full name", "name", ""), ("Username", "user", ""),
                                                ("Password", "pw", "•"), ("Confirm password", "pw2", "•")]):
            ttk.Label(frame, text=label, style="Card.TLabel").grid(row=i + 1, column=0, sticky="w", pady=4)
            ttk.Entry(frame, textvariable=vars_[key], show=show, width=28).grid(row=i + 1, column=1, pady=4)
        ttk.Label(frame, text="Home campus", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Combobox(frame, textvariable=vars_["campus"], values=list(names), state="readonly",
                     width=26).grid(row=5, column=1, pady=4)

        def submit():
            try:
                self.auth.register_lecturer(vars_["name"].get(), vars_["user"].get(), vars_["pw"].get(),
                                            vars_["pw2"].get(), names.get(vars_["campus"].get()))
            except AppError as e:
                messagebox.showwarning("Registration", str(e), parent=win)
                return
            messagebox.showinfo("Registration", "Account created. You can now log in.", parent=win)
            self.u_var.set(vars_["user"].get().strip())
            self.p_var.set("")
            win.destroy()

        ttk.Button(frame, text="Create account", command=submit).grid(row=6, column=0, columnspan=2,
                                                                     pady=(14, 0), sticky="ew")

    # -- campus selection
    def show_campus_select(self):
        self._clear()
        card = ttk.Frame(self.body, style="Card.TFrame", padding=26)
        card.place(relx=0.5, rely=0.4, anchor="center")
        ttk.Label(card, text=f"Welcome, {self.user.full_name}", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text=f"Role: {self.user.role_label}.  Select the campus you want to work with.",
                  style="Card.TLabel").pack(anchor="w", pady=(2, 12))
        allowed = [c for c in self.campuses.values()
                   if self.user.role != "campus_admin" or c.id == self.user.campus_id]
        table = DataTable(card, [("campus", "Campus", 190), ("city", "City", 110),
                                 ("policy", "Booking policy in effect", 560)], height=len(allowed))
        table.pack()
        table.set_rows([(c.id, (c.name, c.city, c.policy.summary()), None) for c in allowed])
        if allowed:
            table.tree.selection_set(str(allowed[0].id))

        def proceed():
            iid = table.selected()
            if not iid:
                messagebox.showinfo("Campus", "Please select a campus.")
                return
            self.campus = self.campuses[int(iid)]
            self.show_main()

        table.tree.bind("<Double-1>", lambda e: proceed())
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="Continue", command=proceed).pack(side="right")
        ttk.Button(row, text="Log out", command=self.show_login).pack(side="right", padx=8)

    # -- main workspace
    def show_main(self):
        self._clear()
        bar = ttk.Frame(self.body, style="Bar.TFrame", padding=(14, 8))
        bar.pack(fill="x", pady=(0, 8))
        info = ttk.Frame(bar, style="Bar.TFrame")
        info.pack(side="left", fill="x", expand=True)
        ttk.Label(info, text=f"Active campus:  {self.campus.name}", style="Bar.TLabel",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(info, text=f"Booking policy in effect:  {self.campus.policy.summary()}",
                  style="Bar.TLabel", font=FONT, wraplength=640, justify="left").pack(anchor="w")
        right = ttk.Frame(bar, style="Bar.TFrame")
        right.pack(side="right")
        ttk.Label(right, text=f"{self.user.full_name}  ({self.user.role_label})",
                  style="Bar.TLabel").pack(anchor="e", pady=(0, 4))
        btns = ttk.Frame(right, style="Bar.TFrame")
        btns.pack(anchor="e")
        if self.user.role != "campus_admin":
            ttk.Button(btns, text="Switch campus", command=self.show_campus_select).pack(side="left", padx=4)
        ttk.Button(btns, text="Log out", command=self.show_login).pack(side="left")
        self.view = VIEWS[self.user.role](self.body, self)
        self.view.pack(fill="both", expand=True)
