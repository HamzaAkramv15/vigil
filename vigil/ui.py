"""Vigil GTK3 UI — dark purple Unity identity."""
import importlib
import os
import re
import signal
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

import psutil  # noqa: E402

from . import __version__  # noqa: E402
from .collector import Collector  # noqa: E402
from .diagnose import RULES, diagnose  # noqa: E402
from .theme import Icon, apply_css  # noqa: E402
from .widgets import (COL, Donut, Graph, HBar, KV, Pills, Rows, StatCard, cls, dim, esc, fb, fr, ft,  # noqa: E402
                      hbox, make_card, page_box, scrolled)

NAV = [("overview", "Overview", "overview"), ("processes", "Processes", "processes"),
       ("resources", "Resources", "resources"), ("storage", "File Systems", "storage"),
       ("network", "Network", "network"), None,
       ("services", "Services", "services"), ("diagnose", "What's wrong?", "diagnose"),
       ("alerts", "Alerts", "alerts"), ("system", "System", "system")]
RES = [("cpu", "CPU"), ("memory", "Memory"), ("gpu", "GPU"), ("sensors", "Sensors"), ("battery", "Battery")]
RANGES = [("1 minute", 60), ("10 minutes", 600), ("1 hour", 3600), ("24 hours", 86400), ("7 days", 604800)]
AUTOSTART = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "autostart", "vigil.desktop")
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIB = 2 ** 30
FREE_COLOR = (0.30, 0.27, 0.52)


def gib(n):
    return f"{n / GIB:.1f} GiB"


def dot(color):
    l = Gtk.Label()
    l.set_markup('<span foreground="#%02x%02x%02x" size="large">●</span>' % tuple(int(c * 255) for c in color))
    return l


def set_autostart(on):
    if not on:
        try:
            os.remove(AUTOSTART)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Vigil\nComment=System monitor (background)\nIcon=vigil\n"
                f'Exec=env "PYTHONPATH={PKG_ROOT}" python3 -m vigil --background\n'
                "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n")


def app_icon_widget(name):
    theme = Gtk.IconTheme.get_default()
    for cand in (name, name.lower(), name.lower().split("-")[0], name.lower().split(".")[0]):
        if cand and theme.has_icon(cand):
            img = Gtk.Image.new_from_icon_name(cand, Gtk.IconSize.LARGE_TOOLBAR)
            img.set_pixel_size(24)
            return img
    return Icon("terminal", 24, tile="purple")


class FsTable(Gtk.Grid):
    """Mount point / total / used / available / usage bar."""

    def __init__(self):
        super().__init__(column_spacing=22, row_spacing=12)
        self.sig = None

    def update(self, mounts):
        sig = tuple((m["mount"], m["used"] >> 20, m["total"] >> 20) for m in mounts)
        if sig == self.sig:
            return
        self.sig = sig
        for c in self.get_children():
            c.destroy()
        for i, h in enumerate(["Mount Point", "Total", "Used", "Available", "Usage"]):
            self.attach(dim(h, 0.0 if i == 0 else 1.0), i, 0, 1, 1)
        for r, m in enumerate(mounts, 1):
            for i, t in enumerate([m["mount"], fb(m["total"]), fb(m["used"]), fb(m["free"])]):
                self.attach(Gtk.Label(label=t, xalign=0.0 if i == 0 else 1.0), i, r, 1, 1)
            bar = HBar(7)
            bar.set(m["pct"] / 100)
            bar.set_size_request(90, 7)
            row = Gtk.Box(spacing=8)
            row.pack_start(bar, True, True, 0)
            row.pack_start(Gtk.Label(label=f"{m['pct']:.0f}%", xalign=1), False, False, 0)
            self.attach(row, 4, r, 1, 1)
        self.show_all()


class DiskBars(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.sig = None

    def update(self, mounts):
        sig = tuple((m["mount"], m["used"] >> 20) for m in mounts)
        if sig == self.sig:
            return
        self.sig = sig
        for c in self.get_children():
            c.destroy()
        for m in mounts:
            top = Gtk.Box()
            top.pack_start(Gtk.Label(label=m["mount"], xalign=0), True, True, 0)
            top.pack_end(dim(f"{m['used'] / GIB:.0f} / {m['total'] / GIB:.0f} GiB ({m['pct']:.0f}%)", 1.0), False, False, 0)
            bar = HBar(9)
            bar.set(m["pct"] / 100)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            col.pack_start(top, False, False, 0)
            col.pack_start(bar, False, False, 0)
            self.pack_start(col, False, False, 0)
        self.show_all()


class Indicator:
    """Optional panel indicator (Ayatana/AppIndicator): 'CPU% · temp' next to the Unity panel icons."""

    def __init__(self, app):
        self.ind = self.mod = None
        for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(ns, "0.1")
                self.mod = importlib.import_module("gi.repository." + ns)
                break
            except (ValueError, ImportError):
                continue
        if not self.mod:
            return
        m = self.mod
        self.ind = m.Indicator.new("vigil", "vigil", m.IndicatorCategory.SYSTEM_SERVICES)
        self.ind.set_status(m.IndicatorStatus.ACTIVE)
        menu = Gtk.Menu()
        for label, fn in (("Open System Monitor", lambda *_: app.show_window()),
                          ("Processes", lambda *_: app.show_window("processes")),
                          ("What's wrong?", lambda *_: app.show_window("diagnose")),
                          (None, None), ("Quit", lambda *_: app.quit())):
            mi = Gtk.SeparatorMenuItem() if label is None else Gtk.MenuItem(label=label)
            if fn:
                mi.connect("activate", fn)
            menu.append(mi)
        menu.show_all()
        self.ind.set_menu(menu)

    def update(self, s):
        if self.ind:
            t = f" · {s['cpu_temp']:.0f}°C" if s.get("cpu_temp") is not None else ""
            self.ind.set_label(f"{s['cpu']:.0f}%{t}", "100% · 100°C")


class VigilWindow(Gtk.ApplicationWindow):
    def __init__(self, app, collector):
        super().__init__(application=app, title="System Monitor")
        self.app, self.col, self.range = app, collector, 60
        self.graphs, self.updaters, self.helper_btns, self.helper_lbls = [], {}, [], []
        self._iconified = False
        apply_css()
        cls(self, "vigil")
        self.set_default_size(1240, 800)
        self.set_icon_name("vigil")

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for name in ("overview", "processes", "resources", "storage", "network", "services", "diagnose", "alerts", "system"):
            w = getattr(self, "_page_" + name)()
            self.stack.add_named(w if name in ("processes", "resources") else scrolled(w), name)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        cls(side, "sidebar")
        side.set_size_request(224, -1)
        spacer = Gtk.Box()
        spacer.set_size_request(-1, 18)
        side.pack_start(spacer, False, False, 0)
        self.nav = {}
        for item in NAV:
            if item is None:
                head = cls(Gtk.Label(label="MORE", xalign=0), "navhead")
                head.set_margin_start(26)
                head.set_margin_top(18)
                head.set_margin_bottom(4)
                side.pack_start(head, False, False, 0)
                continue
            name, label, glyph = item
            btn = cls(Gtk.Button(), "nav")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            row = Gtk.Box(spacing=12)
            icon = Icon(glyph, 20)
            row.pack_start(icon, False, False, 0)
            row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            btn.add(row)
            btn.set_margin_start(12)
            btn.set_margin_end(12)
            btn.connect("clicked", lambda _b, n=name: self.show_page(n))
            side.pack_start(btn, False, False, 0)
            self.nav[name] = (btn, icon)
        side.pack_start(Gtk.Box(), True, True, 0)
        foot = cls(Gtk.Box(spacing=10), "sidefoot")
        foot.pack_start(Icon("overview", 34, tile="purple"), False, False, 0)
        fv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        fv.pack_start(Gtk.Label(label="Live system data", xalign=0), False, False, 0)
        self.foot_sub = dim("Updated every second")
        fv.pack_start(self.foot_sub, False, False, 0)
        foot.pack_start(fv, True, True, 0)
        foot.set_margin_start(14)
        foot.set_margin_end(14)
        foot.set_margin_bottom(16)
        side.pack_start(foot, False, False, 0)

        combo = Gtk.ComboBoxText()
        for label, _ in RANGES:
            combo.append_text(label)
        combo.set_active(0)
        combo.connect("changed", lambda c: setattr(self, "range", RANGES[c.get_active()][1]) or self.tick())
        bar = Gtk.Box(spacing=8)
        bar.set_margin_end(20)
        bar.set_margin_top(12)
        bar.pack_end(combo, False, False, 0)
        bar.pack_end(dim("History"), False, False, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.pack_start(bar, False, False, 0)
        right.pack_start(self.stack, True, True, 0)

        root = Gtk.Box()
        root.pack_start(side, False, False, 0)
        root.pack_start(right, True, True, 0)
        self.add(root)
        root.show_all()  # children shown; the window itself is shown by present()

        self.connect("delete-event", self.on_delete)
        self.connect("window-state-event", lambda _w, ev: setattr(
            self, "_iconified", bool(ev.new_window_state & Gdk.WindowState.ICONIFIED)))
        self.stack.connect("notify::visible-child-name", lambda *_: (self._sync_nav(), self.tick()))
        self._sync_nav()
        GLib.timeout_add(1000, self.tick)

    # ------------------------------------------------------------ navigation
    def show_page(self, name):
        if name in dict(RES):
            self.stack.set_visible_child_name("resources")
            self.pills.select(name)
        elif self.stack.get_child_by_name(name):
            self.stack.set_visible_child_name(name)

    def _sync_nav(self):
        cur = self.stack.get_visible_child_name()
        for name, (btn, icon) in self.nav.items():
            ctx = btn.get_style_context()
            if name == cur:
                ctx.add_class("active")
                icon.set_color((1, 1, 1, 1))
            else:
                ctx.remove_class("active")
                icon.set_color((0.80, 0.78, 0.95, 1))

    def leaf(self):
        n = self.stack.get_visible_child_name()
        return self.res_stack.get_visible_child_name() if n == "resources" else n

    def on_delete(self, *_):
        if self.col.cfg.data.get("background"):
            self.hide()
            self.col.visible = False
            return True
        return False

    def tick(self):
        self.col.visible = self.get_visible() and not self._iconified
        snap = self.col.snap
        if not snap:
            return True
        self.app.indicator_update(snap)
        if self.col.visible:
            name = self.leaf()
            self.col.proc_every = 2 if name == "processes" else 5
            self._sync_helper(snap)
            fn = self.updaters.get(name)
            if fn:
                try:
                    fn(snap)
                except Exception as e:  # never kill the timer
                    print("vigil: update error on", name, repr(e), file=sys.stderr)
            for g in self.graphs:
                if getattr(g, "label", None) is not None:
                    g.label.set_markup(g.readout())
                g.queue_draw()
        return True

    def graph(self, *a, **k):
        return Graph(self, *a, **k)

    # ----------------------------------------------------- privileged helper
    def toggle_helper(self, *_):
        h = self.col.helper
        h.stop() if h.running() else h.start()

    def helper_controls(self):
        """A status label + enable/disable button, kept in sync by tick()."""
        lbl = dim("")
        lbl.set_line_wrap(True)
        btn = cls(Gtk.Button(label="Enable privileged data"), "accent")
        btn.connect("clicked", self.toggle_helper)
        self.helper_btns.append(btn)
        self.helper_lbls.append(lbl)
        return lbl, btn

    def _sync_helper(self, s):
        h = s["helper"]
        on = h["active"]
        text = "Disable privileged data" if on else "Waiting for authorization…" if h["running"] else "Enable privileged data"
        status = ("Active: per-process network and disk for all users, iGPU load, CPU power." if on else
                  f"Not active ({h['error']})." if h["error"] else
                  "Needs a one-time password prompt (polkit). It only reads; nothing is changed.")
        for b in self.helper_btns:
            b.set_label(text)
        for l in self.helper_lbls:
            l.set_text(status)
        self.foot_sub.set_text("Updated every second" + (" · privileged" if on else ""))

    # --------------------------------------------------------------- overview
    def _page_overview(self):
        b = page_box(14)
        g1 = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        self.stat = {}
        for i, (k, t, gl, tile, c, key) in enumerate([
                ("cpu", "CPU", "cpu", "purple", COL["purple"], "cpu"), ("ram", "Memory", "memory", "blue", COL["blue"], "ram"),
                ("swap", "Swap", "swap", "pink", COL["pink"], "swap"), ("disk", "Disk", "storage", "green", COL["green"], "disk_pct")]):
            self.stat[k] = StatCard(self, t, gl, tile, c, key, 100)
            g1.attach(self.stat[k], i, 0, 1, 1)
        b.pack_start(g1, False, False, 0)

        g2 = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        self.health = StatCard(self, "Health", "diagnose", "teal", COL["teal"], None)
        eb = Gtk.EventBox()
        eb.add(self.health)
        eb.connect("button-press-event", lambda *_: self.show_page("diagnose"))
        extras = [eb]
        self.stat["temp"] = StatCard(self, "Temp", "sensors", "red", COL["red"], "cpu_temp", None, 60)
        extras.append(self.stat["temp"])
        for gp in self.col.gpus:
            self.stat[gp["id"]] = StatCard(self, f"{gp['kind']} · {gp['name'][:22]}", "gpu",
                                           "orange" if gp["kind"] == "iGPU" else "teal", COL["orange"], gp["id"] + "_util", 100)
            extras.append(self.stat[gp["id"]])
        if os.path.isdir("/sys/class/power_supply/BAT0") or os.path.isdir("/sys/class/power_supply/BAT1"):
            self.stat["batt"] = StatCard(self, "Battery", "battery", "green", COL["green"], "batt_pct", 100)
            extras.append(self.stat["batt"])
        for i in range(max(4, len(extras) + (-len(extras)) % 4)):  # pad so empty columns keep their width
            w = extras[i] if i < len(extras) else Gtk.Box()
            g2.attach(w, i % 4, i // 4, 1, 1)
        b.pack_start(g2, False, False, 0)

        cc, cr_ = make_card("CPU Usage", "cpu", "purple")
        gr = self.graph([("cpu", "", COL["purple"])], "pct", 100, height=200)
        gr.label = cr_
        cc.pack_start(gr, True, True, 0)
        pc, pr = make_card("Processes", "processes", "purple")
        pr.set_markup('<span foreground="#8a86ab">Top CPU</span>')
        pc.set_size_request(380, -1)
        self.top_grid = Gtk.Grid(column_spacing=14, row_spacing=11)
        for i, (t, xa) in enumerate([("", 0), ("Name", 0), ("CPU %", 1), ("Memory", 1)]):
            self.top_grid.attach(dim(t, xa), i, 0, 1, 1)
        self.top_rows = []
        for r in range(1, 6):
            slot, n, c, m = Gtk.Box(), Gtk.Label(xalign=0), Gtk.Label(xalign=1), Gtk.Label(xalign=1)
            slot.set_size_request(26, 26)
            n.set_hexpand(True)
            n.set_ellipsize(3)
            for i, w in enumerate((slot, n, c, m)):
                self.top_grid.attach(w, i, r, 1, 1)
            self.top_rows.append({"slot": slot, "name": n, "cpu": c, "mem": m, "icon": None})
        pc.pack_start(self.top_grid, True, True, 0)
        row2 = hbox(cc, pc)
        row2.set_child_packing(pc, False, True, 0, Gtk.PackType.START)
        b.pack_start(row2, False, False, 0)

        self.donuts = {}
        mc, _ = make_card("Memory Usage", "memory", "blue")
        swc, _ = make_card("Swap Usage", "swap", "pink")
        for card, key, items in ((mc, "mem", [("Used", COL["violet"]), ("Cached", COL["purple"]), ("Free", FREE_COLOR)]),
                                 (swc, "swap", [("Used", COL["pink"]), ("Free", FREE_COLOR)])):
            d = Donut(130)
            leg = Gtk.Grid(column_spacing=10, row_spacing=10)
            vals = {}
            for i, (lab, c) in enumerate(items):
                leg.attach(dot(c), 0, i, 1, 1)
                leg.attach(dim(lab), 1, i, 1, 1)
                v = Gtk.Label(xalign=1)
                leg.attach(v, 2, i, 1, 1)
                vals[lab] = v
            row = Gtk.Box(spacing=18)
            row.pack_start(d, False, False, 0)
            lv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            lv.set_valign(Gtk.Align.CENTER)
            lv.pack_start(leg, False, False, 0)
            row.pack_start(lv, True, True, 0)
            card.pack_start(row, True, True, 0)
            self.donuts[key] = (d, vals)
        nc, nr = make_card("Network", "network", "purple")
        ng = self.graph([("net_down", "↓", COL["purple"]), ("net_up", "↑", COL["pink"])], "rate", floor=10240, height=130)
        ng.label = nr
        nc.pack_start(ng, True, True, 0)
        row3 = hbox(mc, swc, nc)
        row3.set_child_packing(mc, False, True, 0, Gtk.PackType.START)
        row3.set_child_packing(swc, False, True, 0, Gtk.PackType.START)
        b.pack_start(row3, False, False, 0)

        dc, _ = make_card("Disk Usage", "storage", "green")
        self.disk_bars = DiskBars()
        self.disk_tot = {"Used": Gtk.Label(xalign=1), "Free": Gtk.Label(xalign=1)}
        tot = Gtk.Grid(column_spacing=10, row_spacing=12)
        for i, (lab, c) in enumerate([("Used", COL["violet"]), ("Free", FREE_COLOR)]):
            tot.attach(dot(c), 0, i, 1, 1)
            tot.attach(dim(lab), 1, i, 1, 1)
            tot.attach(self.disk_tot[lab], 2, i, 1, 1)
        tot.set_valign(Gtk.Align.CENTER)
        drow = Gtk.Box(spacing=26)
        drow.pack_start(self.disk_bars, True, True, 0)
        drow.pack_start(tot, False, False, 0)
        dc.pack_start(drow, True, True, 0)
        fc, _ = make_card("File Systems", "storage", "purple")
        self.fs_over = FsTable()
        fc.pack_start(self.fs_over, True, True, 0)
        b.pack_start(hbox(dc, fc), False, False, 0)
        self.updaters["overview"] = self._up_overview
        return b

    def _up_overview(self, s):
        st, r, sw = self.stat, s["ram"], s["swap"]
        freq = f"{s['cpu_freq'] / 1000:.1f} GHz • " if s["cpu_freq"] else ""
        st["cpu"].set(f"{s['cpu']:.0f}%", f"{freq}{s['cpu_count']} cores")
        st["ram"].set(f"{r['pct']:.0f}%", f"{r['used'] / GIB:.1f} / {r['total'] / GIB:.0f} GiB")
        st["swap"].set(f"{sw['pct']:.0f}%" if sw["total"] else "–",
                       f"{sw['used'] / GIB:.1f} / {sw['total'] / GIB:.0f} GiB" if sw["total"] else "no swap")
        root = next((m for m in s["mounts"] if m["mount"] == "/"), s["mounts"][0] if s["mounts"] else None)
        if root:
            st["disk"].set(f"{root['pct']:.0f}%", f"{root['used'] / GIB:.0f} / {root['total'] / GIB:.0f} GiB")
        issues = [t for lvl, t in diagnose(s) if lvl == "warn"]
        self.health.set("All good" if not issues else f"{len(issues)} issue{'s' if len(issues) > 1 else ''}",
                        "Nothing unusual" if not issues else issues[0][:34])
        pw = f" · {s['cpu_power']:.1f} W" if s["cpu_power"] else ""
        st["temp"].set(f"{s['cpu_temp']:.0f}°C" if s["cpu_temp"] is not None else "–", "CPU" + pw)
        for g in s["gpus"]:
            c = st[g["id"]]
            if g["state"] == "suspended":
                c.set("Asleep", "powered down")
                continue
            bits = [f"{g['temp']:.0f}°C"] if g["temp"] is not None else []
            if g["vram_total"]:
                bits.append(f"{g['vram_used'] / GIB:.1f}/{g['vram_total'] / GIB:.0f} GiB")
            elif g["clock"]:
                bits.append(f"{g['clock']:.0f} MHz")
            val = (f"{g['util']:.0f}%" if g["util"] is not None else
                   f"{g['clock']:.0f} MHz" if g["clock"] else g["state"].capitalize())
            c.set(val, " · ".join(bits))
        b = s["battery"]
        if "batt" in st and b:
            sign = "-" if b["status"] == "Discharging" else "+" if b["status"] == "Charging" else ""
            w = f"{sign}{b['power_w']:.1f} W" if b["power_w"] is not None else b["status"]
            st["batt"].set(f"{b['pct']:.0f}%", f"{w} · {ft(b['eta'])}" if b["eta"] else w)

        agg = {}
        for p in s["procs"]:
            a = agg.setdefault(p[1], [0.0, 0])
            a[0] += p[3]
            a[1] += p[4]
        top = sorted(agg.items(), key=lambda kv: -kv[1][0])[:5]
        for row, (name, (cpu, mem)) in zip(self.top_rows, top):
            if row["icon"] != name:
                for c in row["slot"].get_children():
                    c.destroy()
                w = app_icon_widget(name)
                row["slot"].pack_start(w, True, True, 0)
                w.show()
                row["icon"] = name
            row["name"].set_text(name)
            row["cpu"].set_text(f"{cpu:.1f}%")
            row["mem"].set_text(fb(mem))

        d, v = self.donuts["mem"]
        cached = min(r["cached"], r["available"])
        d.set([(r["used"] / r["total"], COL["violet"]), (cached / r["total"], COL["purple"])], f"{r['pct']:.0f}%",
              f"{r['used'] / GIB:.1f} / {r['total'] / GIB:.0f} GiB")
        v["Used"].set_text(gib(r["used"]))
        v["Cached"].set_text(gib(cached))
        v["Free"].set_text(gib(r["available"] - cached))
        d, v = self.donuts["swap"]
        if sw["total"]:
            d.set([(sw["used"] / sw["total"], COL["pink"])], f"{sw['pct']:.0f}%",
                  f"{sw['used'] / GIB:.1f} / {sw['total'] / GIB:.0f} GiB")
            v["Used"].set_text(gib(sw["used"]))
            v["Free"].set_text(gib(sw["total"] - sw["used"]))
        else:
            d.set([], "n/a", "no swap")
        self.disk_bars.update(s["mounts"])
        self.fs_over.update(s["mounts"])
        self.disk_tot["Used"].set_text(f"{sum(m['used'] for m in s['mounts']) / GIB:.0f} GiB")
        self.disk_tot["Free"].set_text(f"{sum(m['free'] for m in s['mounts']) / GIB:.0f} GiB")

    # ------------------------------------------------------------- processes
    _PCOLS = [("Name", 1, "text"), ("PID", 0, "int"), ("User", 2, "text"), ("CPU %", 3, "pct"),
              ("Memory", 4, "bytes"), ("GPU %", 5, "pct"), ("Disk", 6, "rate"), ("Net", 11, "rate"),
              ("Threads", 7, "int"), ("State", 8, "text"), ("PPID", 9, "int"), ("Started", 10, "time")]

    def _page_processes(self):
        b = page_box(12)
        bar = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8,
                          max_children_per_line=12, homogeneous=False)
        self.psearch = Gtk.SearchEntry(placeholder_text="Filter by name, user or PID")
        self.psearch.set_size_request(240, -1)
        bar.add(self.psearch)
        for label, fn, kind in (("End", lambda *_: self.signal_proc(signal.SIGTERM), None),
                                ("Kill", lambda *_: self.signal_proc(signal.SIGKILL), "danger"),
                                ("Stop", lambda *_: self.signal_proc(signal.SIGSTOP), None),
                                ("Continue", lambda *_: self.signal_proc(signal.SIGCONT), None),
                                ("Open location", self.open_location, None), ("Terminal", self.open_terminal, None)):
            btn = Gtk.Button(label=label)
            if kind:
                cls(btn, kind)
            btn.connect("clicked", fn)
            bar.add(btn)
        _hl, hb = self.helper_controls()
        bar.add(hb)
        b.pack_start(bar, False, False, 0)

        self.pstore = Gtk.ListStore(int, str, str, float, GObject.TYPE_INT64, float, float, int, str, int, float, float)
        self.prows = Rows(self.pstore)
        self.pfilter = self.pstore.filter_new()
        self.pfilter.set_visible_func(self._pvisible)
        self.psort = Gtk.TreeModelSort(model=self.pfilter)
        self.psort.set_sort_column_id(3, Gtk.SortType.DESCENDING)
        self.pview = Gtk.TreeView(model=self.psort)
        self.pview.set_enable_search(False)
        for title, idx, kind in self._PCOLS:
            r = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, r)
            if kind == "text":
                col.add_attribute(r, "text", idx)
            else:
                r.set_property("xalign", 1.0)
                col.set_cell_data_func(r, self._fmt_cell, (idx, kind))
                col.set_alignment(1.0)
            col.set_sort_column_id(idx)
            col.set_resizable(True)
            self.pview.append_column(col)
        self.psearch.connect("search-changed", lambda *_: self.pfilter.refilter())
        self.pview.get_selection().connect("changed", lambda *_: self._show_details())
        self.pview.connect("button-press-event", self._pmenu)
        tc, _ = make_card()
        tc.pack_start(scrolled(self.pview), True, True, 0)
        b.pack_start(tc, True, True, 0)

        dc, _ = make_card()
        self.pdetail = Gtk.Label(xalign=0, yalign=0, selectable=True, wrap=True)
        self.pdetail.set_markup("<i>Select a process for details</i>")
        self.pdetail.set_size_request(-1, 92)
        dc.pack_start(self.pdetail, False, False, 0)
        b.pack_start(dc, False, False, 0)
        self.updaters["processes"] = self._up_procs
        return b

    @staticmethod
    def _fmt_cell(_col, cell, model, it, data):
        idx, kind = data
        v = model[it][idx]
        if kind == "int":
            t = str(v)
        elif kind == "pct":
            t = f"{v:.1f}" if v else "0"
        elif kind == "bytes":
            t = fb(v)
        elif kind == "rate":
            t = fr(v) if v >= 1 else "–"
        else:
            t = time.strftime("%H:%M" if time.time() - v < 86400 else "%b %d %H:%M", time.localtime(v))
        cell.set_property("text", t)

    def _pvisible(self, model, it, _data):
        q = self.psearch.get_text().strip().lower()
        if not q:
            return True
        row = model[it]
        return q in row[1].lower() or q in row[2].lower() or q == str(row[0])

    def _up_procs(self, s):
        self.prows.update((r[0], r) for r in s["procs"])
        self._show_details()

    def _sel_pid(self):
        model, it = self.pview.get_selection().get_selected()
        return model[it][0] if it else None

    def _pmenu(self, view, ev):
        if ev.button != 3:
            return False
        hit = view.get_path_at_pos(int(ev.x), int(ev.y))
        if hit:
            view.get_selection().select_path(hit[0])
        menu = Gtk.Menu()
        for label, fn in (("End process", lambda *_: self.signal_proc(signal.SIGTERM)),
                          ("Kill process", lambda *_: self.signal_proc(signal.SIGKILL)),
                          ("Stop", lambda *_: self.signal_proc(signal.SIGSTOP)),
                          ("Continue", lambda *_: self.signal_proc(signal.SIGCONT)),
                          ("Open location", self.open_location)):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", fn)
            menu.append(mi)
        menu.show_all()
        menu.popup_at_pointer(ev)
        return True

    def _show_details(self):
        pid = self._sel_pid()
        if pid is None:
            return
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                chain, q = [], p
                for _ in range(6):
                    q = q.parent()
                    if not q:
                        break
                    chain.append(q.name())
                try:
                    exe = p.exe() or "–"
                except psutil.AccessDenied:
                    exe = "(no access)"
                try:
                    cwd = p.cwd()
                except (psutil.AccessDenied, OSError):
                    cwd = "(no access)"
                try:
                    fds = p.num_fds()
                except (psutil.AccessDenied, OSError):
                    fds = "?"
                cmd = " ".join(p.cmdline())[:300] or p.name()
                k = "<span foreground='#8a86ab'>%s</span>"
                self.pdetail.set_markup(
                    f"<b>{esc(p.name())}</b>  (PID {pid})   nice {p.nice()}   {fds} open files   {p.num_threads()} threads\n"
                    f"{k % 'Parents'}  {esc(' ← '.join(chain)) or '–'}\n{k % 'Exe'}  {esc(exe)}\n"
                    f"{k % 'Cwd'}  {esc(cwd)}\n{k % 'Cmd'}  {esc(cmd)}")
        except psutil.NoSuchProcess:
            self.pdetail.set_markup("<i>Process has exited</i>")
        except Exception as e:
            self.pdetail.set_text(str(e))

    def signal_proc(self, sig):
        pid = self._sel_pid()
        if pid is None:
            return
        if sig == signal.SIGKILL and not self._confirm(f"Force-kill PID {pid}? Unsaved data in it will be lost."):
            return
        try:
            os.kill(pid, sig)
        except PermissionError:
            subprocess.Popen(["pkexec", "kill", f"-{int(sig)}", str(pid)])
        except ProcessLookupError:
            pass

    def _confirm(self, text):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.OK_CANCEL, text=text)
        ok = d.run() == Gtk.ResponseType.OK
        d.destroy()
        return ok

    def open_location(self, *_):
        try:
            subprocess.Popen(["xdg-open", os.path.dirname(psutil.Process(self._sel_pid()).exe())])
        except Exception:
            pass

    def open_terminal(self, *_):
        pid = self._sel_pid()
        if pid is None:
            return
        script = (f"ps -fp {pid}; echo; ls -l /proc/{pid}/exe /proc/{pid}/cwd; echo; "
                  f"top -H -b -n1 -p {pid} | head -15; echo; exec bash")
        try:
            subprocess.Popen(["x-terminal-emulator", "-e", "sh", "-c", script])
        except OSError:
            pass

    # -------------------------------------------------------------- resources
    def _page_resources(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.res_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for name, _ in RES:
            self.res_stack.add_named(scrolled(getattr(self, "_page_" + name)()), name)
        self.pills = Pills(self.res_stack, RES)
        self.pills.set_margin_start(20)
        self.pills.set_margin_top(4)
        self.res_stack.connect("notify::visible-child-name", lambda *_: self.tick())
        box.pack_start(self.pills, False, False, 0)
        box.pack_start(self.res_stack, True, True, 0)
        return box

    def _page_cpu(self):
        b = page_box()
        gc, gr = make_card("CPU Usage", "cpu", "purple")
        g = self.graph([("cpu", "", COL["purple"])], "pct", 100, height=220)
        g.label = gr
        gc.pack_start(g, True, True, 0)
        b.pack_start(gc, False, False, 0)
        dc, _ = make_card("Details", "overview", "blue")
        self.cpu_kv = KV(["Usage", "Verdict", "Frequency", "Temperature", "Load average", "Package power", "Busiest process"])
        dc.pack_start(self.cpu_kv, False, False, 0)
        cc, _ = make_card("Cores", "cpu", "teal")
        grid = Gtk.Grid(column_spacing=12, row_spacing=8, column_homogeneous=True)
        self.core_bars = []
        for i in range(psutil.cpu_count()):
            pb = Gtk.ProgressBar(show_text=True)
            self.core_bars.append(pb)
            grid.attach(pb, i % 4, i // 4, 1, 1)
        cc.pack_start(grid, False, False, 0)
        row = hbox(dc, cc)
        row.set_child_packing(dc, False, True, 0, Gtk.PackType.START)
        b.pack_start(row, False, False, 0)
        self.updaters["cpu"] = self._up_cpu
        return b

    def _up_cpu(self, s):
        per, kv = s["cpu_per"], self.cpu_kv
        for i, (pb, v) in enumerate(zip(self.core_bars, per)):
            pb.set_fraction(v / 100)
            pb.set_text(f"CPU {i}: {v:.0f}%")
        kv.set("Usage", f"{s['cpu']:.0f}%")
        hot = max(per) > 90 and s["cpu"] < 35
        kv.set("Verdict", f"One core pinned (CPU {per.index(max(per))})" if hot else
               "Busy across many cores" if s["cpu"] > 70 else "Light or evenly spread")
        kv.set("Frequency", f"{s['cpu_freq']:.0f} MHz" if s["cpu_freq"] else "–")
        kv.set("Temperature", f"{s['cpu_temp']:.0f}°C" if s["cpu_temp"] is not None else "–")
        kv.set("Load average", " / ".join(f"{x:.2f}" for x in s["load"]))
        kv.set("Package power", f"{s['cpu_power']:.1f} W" if s["cpu_power"] is not None else "enable privileged data")
        top = max(s["procs"], key=lambda r: r[3], default=None)
        kv.set("Busiest process", f"{top[1]} ({top[3]:.0f}%)" if top else "–")

    def _page_memory(self):
        b = page_box()
        gc, gr = make_card("Memory", "memory", "blue")
        g = self.graph([("ram", "RAM", COL["blue"]), ("swap", "Swap", COL["pink"])], "pct", 100, height=220)
        g.label = gr
        gc.pack_start(g, True, True, 0)
        b.pack_start(gc, False, False, 0)
        dc, _ = make_card("Details", "overview", "blue")
        self.mem_kv = KV(["Total", "Used (excl. cache)", "Available", "Cached", "Buffers", "Swap", "Pressure (10 s)"])
        dc.pack_start(self.mem_kv, False, False, 0)
        dc.pack_start(dim("“90% used” on Linux is usually mostly cache.\nWatch Available and Pressure instead."), False, False, 0)
        tc, _ = make_card("Top memory users", "processes", "purple")
        self.mem_top = Gtk.Label(xalign=0, selectable=True)
        tc.pack_start(self.mem_top, False, False, 0)
        b.pack_start(hbox(dc, tc), False, False, 0)
        self.updaters["memory"] = self._up_mem
        return b

    def _up_mem(self, s):
        r, sw, kv = s["ram"], s["swap"], self.mem_kv
        kv.set("Total", fb(r["total"]))
        kv.set("Used (excl. cache)", f"{fb(r['used'])}  ({r['pct']:.0f}%)")
        kv.set("Available", fb(r["available"]))
        kv.set("Cached", fb(r["cached"]))
        kv.set("Buffers", fb(r["buffers"]))
        kv.set("Swap", f"{fb(sw['used'])} / {fb(sw['total'])}" if sw["total"] else "none")
        p = s["psi"].get("memory")
        kv.set("Pressure (10 s)", f"{p:.1f}% stalled" if p is not None else "n/a (kernel PSI off)")
        agg = {}
        for pr in s["procs"]:
            agg[pr[1]] = agg.get(pr[1], 0) + pr[4]
        top = sorted(agg.items(), key=lambda x: -x[1])[:8]
        self.mem_top.set_markup("<tt>" + "\n".join(f"{esc(n[:26]):<26} {fb(m):>10}" for n, m in top) + "</tt>")

    def _page_gpu(self):
        b = page_box()
        self.gpu_kv = {}
        if not self.col.gpus:
            b.pack_start(dim("No GPU detected."), False, False, 0)
        for g in self.col.gpus:
            card, rd = make_card(f"{g['kind']} — {g['name']}", "gpu", "orange" if g["kind"] == "iGPU" else "teal")
            gr = self.graph([(g["id"] + "_util", "", COL["green"])], "pct", 100, height=150)
            gr.label = rd
            kv = KV(["Status", "Utilization", "VRAM", "Temperature", "Clock", "Power", "Fan", "Video encode / decode"])
            self.gpu_kv[g["id"]] = kv
            tg = self.graph([(g["id"] + "_temp", "", COL["red"])], "temp", floor=60, height=150)
            card.pack_start(kv, False, False, 0)
            card.pack_start(hbox(gr, tg), False, False, 0)
            b.pack_start(card, False, False, 0)
        if any(g["kind"] == "dGPU" for g in self.col.gpus):
            b.pack_start(dim("A sleeping dGPU is left alone: Vigil never wakes it just to read stats."), False, False, 0)
        if any(g["vendor"] == "0x8086" for g in self.col.gpus):
            _hl, hb = self.helper_controls()
            b.pack_start(dim("Intel iGPU load needs root (and intel-gpu-tools): use privileged data."), False, False, 0)
            b.pack_start(hb, False, False, 0)
        self.updaters["gpu"] = self._up_gpu
        return b

    def _up_gpu(self, s):
        na = lambda v, f: f(v) if v is not None else "–"
        for g in s["gpus"]:
            kv = self.gpu_kv[g["id"]]
            kv.set("Status", "Powered down (runtime suspend)" if g["state"] == "suspended" else g["state"])
            kv.set("Utilization", na(g["util"], lambda v: f"{v:.0f}%"))
            kv.set("VRAM", f"{fb(g['vram_used'])} / {fb(g['vram_total'])}" if g["vram_total"] else "–")
            kv.set("Temperature", na(g["temp"], lambda v: f"{v:.0f}°C"))
            kv.set("Clock", na(g["clock"], lambda v: f"{v:.0f} MHz"))
            kv.set("Power", na(g["power"], lambda v: f"{v:.1f} W"))
            kv.set("Fan", na(g["fan"], lambda v: f"{v:.0f}" + ("%" if g["kind"] == "dGPU" and v <= 100 else " RPM")))
            kv.set("Video encode / decode", f"{g['enc']:.0f}% / {g['dec']:.0f}%" if g["enc"] is not None else "–")

    def _page_sensors(self):
        b = page_box(12)
        c, _ = make_card("Sensors", "sensors", "red")
        self.show_all_sensors = Gtk.CheckButton(label="Show all sensors (duplicates and per-core too)")
        c.pack_start(self.show_all_sensors, False, False, 0)
        self.sstore = Gtk.ListStore(str, str, str)
        self.srows = Rows(self.sstore)
        v = Gtk.TreeView(model=self.sstore)
        for i, t in enumerate(["Chip", "Sensor", "Value"]):
            col = Gtk.TreeViewColumn(t, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            col.set_min_width(180)
            v.append_column(col)
        c.pack_start(v, True, True, 0)
        b.pack_start(c, True, True, 0)
        self.updaters["sensors"] = self._up_sensors
        return b

    def _up_sensors(self, s):
        show_all, rows = self.show_all_sensors.get_active(), []
        main = ("Composite", "Package", "Tctl", "Tdie", "edge")
        for chip, ents in s["temps"].items():
            seen = set()
            has_main = any(l.startswith(main) for l, _ in ents)
            for i, (label, v) in enumerate(ents):
                if not show_all and (v <= 0 or v > 125 or (label, round(v)) in seen or
                                     (has_main and not label.startswith(main)) or re.match(r"^Core \d+", label)):
                    continue
                seen.add((label, round(v)))
                rows.append(((chip, i), (chip, label, f"{v:.0f} °C")))
        for chip, ents in s["fans"].items():
            for i, (label, v) in enumerate(ents):
                rows.append((("fan", chip, i), (chip, label, f"{v:.0f} RPM")))
        for g in s["gpus"]:
            if g["power"] is not None:
                rows.append((("gp", g["id"]), (g["kind"], g["name"][:30], f"{g['power']:.1f} W")))
        if s["cpu_power"] is not None:
            rows.append((("cp",), ("cpu", "Package power", f"{s['cpu_power']:.1f} W")))
        b = s["battery"]
        if b and b["power_w"] is not None:
            rows.append((("bp",), ("battery", "Power draw", f"{b['power_w']:.1f} W")))
        if b and b["voltage"]:
            rows.append((("bv",), ("battery", "Voltage", f"{b['voltage']:.2f} V")))
        self.srows.update(rows)

    def _page_battery(self):
        b = page_box()
        self.batt_none = dim("No battery detected.")
        b.pack_start(self.batt_none, False, False, 0)
        self.batt_cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        c, _ = make_card("Battery", "battery", "green")
        self.batt_kv = KV(["Charge", "Status", "Power draw", "Time remaining", "Health", "Full-charge capacity",
                           "Design capacity", "Charge cycles", "Voltage", "AC adapter"])
        c.pack_start(self.batt_kv, False, False, 0)
        self.batt_cards.pack_start(c, False, False, 0)
        gc, gr = make_card("Charge", "battery", "green")
        g1 = self.graph([("batt_pct", "", COL["green"])], "pct", 100, height=150)
        g1.label = gr
        pc, pr = make_card("Power draw", "overview", "purple")
        g2 = self.graph([("power", "", COL["purple"])], "watt", floor=10, height=150)
        g2.label = pr
        gc.pack_start(g1, True, True, 0)
        pc.pack_start(g2, True, True, 0)
        self.batt_cards.pack_start(hbox(gc, pc), False, False, 0)
        lc, _ = make_card("Charge limit", "battery", "orange")
        row = Gtk.Box(spacing=10)
        self.limit_spin = Gtk.SpinButton.new_with_range(40, 100, 1)
        self.limit_spin.set_value(80)
        ap = cls(Gtk.Button(label="Apply (asks for password)"), "accent")
        ap.connect("clicked", self.apply_limit)
        row.pack_start(dim("Stop charging at"), False, False, 0)
        row.pack_start(self.limit_spin, False, False, 0)
        row.pack_start(dim("%"), False, False, 0)
        row.pack_start(ap, False, False, 8)
        self.limit_note = dim("")
        lc.pack_start(row, False, False, 0)
        lc.pack_start(self.limit_note, False, False, 0)
        self.limit_card = lc
        self.batt_cards.pack_start(lc, False, False, 0)
        b.pack_start(self.batt_cards, False, False, 0)
        self._limit_loaded = False
        self.updaters["battery"] = self._up_batt
        return b

    def apply_limit(self, *_):
        b = self.col.snap.get("battery")
        if not b:
            return
        path = f"/sys/class/power_supply/{b['name']}/charge_control_end_threshold"
        n = int(self.limit_spin.get_value())
        threading.Thread(target=lambda: subprocess.run(["pkexec", "/usr/bin/tee", path], input=str(n), text=True,
                                                       stdout=subprocess.DEVNULL), daemon=True).start()

    def _up_batt(self, s):
        b, kv = s["battery"], self.batt_kv
        self.batt_none.set_visible(b is None)
        self.batt_cards.set_visible(b is not None)
        if not b:
            return
        wh = lambda v: f"{v:.1f} Wh" if v is not None else "–"
        kv.set("Charge", f"{b['pct']:.0f}%" if b["pct"] is not None else "–")
        kv.set("Status", b["status"])
        kv.set("Power draw", f"{b['power_w']:.1f} W" if b["power_w"] is not None else "–")
        kv.set("Time remaining", ft(b["eta"]) + (" to full" if b["status"] == "Charging" and b["eta"] else ""))
        kv.set("Health", f"{b['health']:.0f}%" if b["health"] is not None else "–")
        kv.set("Full-charge capacity", wh(b["full"]))
        kv.set("Design capacity", wh(b["design"]))
        kv.set("Charge cycles", str(b["cycles"]) if b["cycles"] is not None else "–")
        kv.set("Voltage", f"{b['voltage']:.2f} V" if b["voltage"] else "–")
        kv.set("AC adapter", "connected" if b["ac"] else "unplugged")
        self.limit_card.set_visible(b["limit"] is not None)
        if b["limit"] is not None:
            if not self._limit_loaded:
                self.limit_spin.set_value(b["limit"])
                self._limit_loaded = True
            self.limit_note.set_text(f"Current limit: {b['limit']}%. Many laptops reset this at reboot.")

    # ---------------------------------------------------------------- storage
    def _page_storage(self):
        b = page_box()
        fc, _ = make_card("File Systems", "storage", "purple")
        self.fs_page = FsTable()
        fc.pack_start(self.fs_page, False, False, 0)
        b.pack_start(fc, False, False, 0)
        gc, gr = make_card("Throughput", "storage", "green")
        g = self.graph([("disk_r", "R", COL["teal"]), ("disk_w", "W", COL["pink"])], "rate", floor=10240, height=180)
        g.label = gr
        gc.pack_start(g, True, True, 0)
        b.pack_start(gc, False, False, 0)
        dc, _ = make_card("Activity", "overview", "blue")
        self.disk_kv = KV(["Read / Write", "IOPS (R / W)", "NVMe temperature", "I/O pressure (10 s)"])
        dc.pack_start(self.disk_kv, False, False, 0)
        tc, _ = make_card("Who is hammering the disk?", "processes", "orange")
        self.disk_top = Gtk.Label(xalign=0, selectable=True)
        tc.pack_start(self.disk_top, False, False, 0)
        _hl, hb = self.helper_controls()
        tc.pack_start(dim("Other users' processes need privileged data."), False, False, 0)
        tc.pack_start(hb, False, False, 0)
        b.pack_start(hbox(dc, tc), False, False, 0)
        self.updaters["storage"] = self._up_storage
        return b

    def _up_storage(self, s):
        self.fs_page.update(s["mounts"])
        d, kv = s["disk"], self.disk_kv
        kv.set("Read / Write", f"{fr(d['read'])} / {fr(d['write'])}")
        kv.set("IOPS (R / W)", f"{d['riops']:.0f} / {d['wiops']:.0f}")
        kv.set("NVMe temperature", f"{s['nvme_temp']:.0f}°C" if s["nvme_temp"] is not None else "–")
        p = s["psi"].get("io")
        kv.set("I/O pressure (10 s)", f"{p:.1f}% stalled" if p is not None else "n/a")
        agg = {}
        for r in s["procs"]:
            agg[r[1]] = agg.get(r[1], 0) + r[6]
        top = [(n, v) for n, v in sorted(agg.items(), key=lambda x: -x[1])[:6] if v >= 1024]
        self.disk_top.set_markup("<tt>" + ("\n".join(f"{esc(n[:26]):<26} {fr(v):>12}" for n, v in top) or "quiet") + "</tt>")

    # ---------------------------------------------------------------- network
    def _page_network(self):
        b = page_box()
        gc, gr = make_card("Traffic", "network", "purple")
        g = self.graph([("net_down", "↓", COL["purple"]), ("net_up", "↑", COL["pink"])], "rate", floor=10240, height=200)
        g.label = gr
        gc.pack_start(g, True, True, 0)
        b.pack_start(gc, False, False, 0)
        ic, _ = make_card("Interfaces", "network", "blue")
        self.nstore = Gtk.ListStore(str, str, str, str, str, str, str)
        self.nrows = Rows(self.nstore)
        v = Gtk.TreeView(model=self.nstore)
        for i, t in enumerate(["Interface", "Status", "IP address", "Down", "Up", "Wi-Fi signal", "Connection"]):
            col = Gtk.TreeViewColumn(t, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            v.append_column(col)
        ic.pack_start(v, False, False, 0)
        b.pack_start(ic, False, False, 0)
        pc, _ = make_card("Per-process network usage", "processes", "orange")
        self.net_top = Gtk.Label(xalign=0, selectable=True)
        hl, hb = self.helper_controls()
        pc.pack_start(self.net_top, False, False, 0)
        pc.pack_start(hl, False, False, 0)
        pc.pack_start(hb, False, False, 0)
        b.pack_start(pc, False, False, 0)
        self.updaters["network"] = self._up_net
        return b

    def _up_net(self, s):
        n = s["net"]
        self.nrows.update((i["name"], (i["name"], "up" if i["up"] else "down", i["ip"], fr(i["down"]), fr(i["up_rate"]),
                                      f"{i['signal']:.0f}/70" if i["signal"] is not None else "", n["conns"].get(i["name"], "")))
                          for i in n["ifs"])
        if s["helper"]["active"]:
            lines = [f"{esc(nm[:26]):<26} ↓ {fr(d):>12}   ↑ {fr(u):>12}" for nm, d, u in s["net_top"]]
            self.net_top.set_markup("<tt>" + ("\n".join(lines) or "quiet") + "</tt>")
        else:
            self.net_top.set_text("")

    # --------------------------------------------------------------- services
    def _page_services(self):
        b = page_box(12)
        c, _ = make_card("Services", "services", "orange")
        bar = Gtk.Box(spacing=8)
        btn = Gtk.Button(label="Refresh")
        btn.connect("clicked", lambda *_: self.refresh_services())
        bar.pack_start(btn, False, False, 0)
        self.failed_only = Gtk.CheckButton(label="Failed only")
        self.failed_only.connect("toggled", lambda *_: self.svc_filter.refilter())
        bar.pack_start(self.failed_only, False, False, 8)
        for act in ("start", "stop", "restart", "enable", "disable"):
            bt = Gtk.Button(label=act.capitalize())
            bt.connect("clicked", lambda _w, a=act: self.svc_action(a))
            bar.pack_start(bt, False, False, 0)
        c.pack_start(bar, False, False, 0)
        self.svc_boot = dim("")
        c.pack_start(self.svc_boot, False, False, 0)
        self.svc_store = Gtk.ListStore(str, str, str, str, str)
        self.svc_filter = self.svc_store.filter_new()
        self.svc_filter.set_visible_func(lambda m, it, _d: not self.failed_only.get_active() or m[it][2] == "failed")
        self.svc_view = Gtk.TreeView(model=self.svc_filter)
        for i, t in enumerate(["Unit", "Load", "Active", "Sub", "Description"]):
            r = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(t, r, text=i)
            col.set_cell_data_func(r, self._svc_color, i)
            col.set_resizable(True)
            self.svc_view.append_column(col)
        sw = scrolled(self.svc_view)
        sw.set_size_request(-1, 460)
        c.pack_start(sw, True, True, 0)
        b.pack_start(c, True, True, 0)
        self._svc_loaded = False
        self.updaters["services"] = lambda _s: self._svc_first()
        return b

    @staticmethod
    def _svc_color(_c, cell, model, it, i):
        cell.set_property("text", model[it][i])
        cell.set_property("foreground", "#ff6b81" if model[it][2] == "failed" else "#e9e7f7")

    def _svc_first(self):
        if not self._svc_loaded:
            self._svc_loaded = True
            self.refresh_services()

    def refresh_services(self):
        def work():
            rows, boot = [], ""
            try:
                out = subprocess.run(["systemctl", "list-units", "--type=service", "--all", "--plain", "--no-legend", "--no-pager"],
                                     capture_output=True, text=True, timeout=10).stdout
                for l in out.splitlines():
                    f = l.split(None, 4)
                    if len(f) >= 4 and f[1] == "loaded":
                        rows.append((f[0], f[1], f[2], f[3], f[4] if len(f) > 4 else ""))
                boot = subprocess.run(["systemd-analyze"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
            except Exception:
                pass
            GLib.idle_add(self._svc_done, rows, boot)
        threading.Thread(target=work, daemon=True).start()

    def _svc_done(self, rows, boot):
        self.svc_store.clear()
        for r in sorted(rows, key=lambda r: (r[2] != "failed", r[0])):
            self.svc_store.append(list(r))
        self.svc_boot.set_text(boot)
        return False

    def svc_action(self, action):
        model, it = self.svc_view.get_selection().get_selected()
        if not it:
            return
        unit = model[it][0]
        if action in ("stop", "disable") and not self._confirm(f"{action.capitalize()} {unit}?"):
            return

        def work():
            subprocess.run(["pkexec", "systemctl", action, unit])
            GLib.timeout_add(800, lambda: self.refresh_services() or False)
        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------------- diagnose
    def _page_diagnose(self):
        b = page_box()
        c, _ = make_card("What's wrong?", "diagnose", "teal")
        c.pack_start(dim("Anomalies surfaced from live data. Vigil flags things; you decide what to do."), False, False, 0)
        self.diag_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.diag_box.set_margin_top(8)
        c.pack_start(self.diag_box, False, False, 0)
        b.pack_start(c, False, False, 0)
        self.updaters["diagnose"] = self._up_diag
        return b

    def _up_diag(self, s):
        for c in self.diag_box.get_children():
            c.destroy()
        icon = {"warn": ("⚠", "#ffb454"), "ok": ("✓", "#4ade80"), "info": ("ℹ", "#7aa7ff")}
        for lvl, text in diagnose(s):
            ic, color = icon[lvl]
            l = Gtk.Label(xalign=0)
            l.set_markup(f'<span foreground="{color}" weight="bold">{ic}</span>   {esc(text)}')
            self.diag_box.pack_start(l, False, False, 0)
        self.diag_box.show_all()

    # ----------------------------------------------------------------- alerts
    def _page_alerts(self):
        b = page_box()
        c, _ = make_card("Alerts", "alerts", "orange")
        c.pack_start(dim("A desktop notification fires when a limit is exceeded for the stated time (10 min cooldown)."), False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        cfg = self.col.cfg
        for i, (rid, label, cmp_, _d, secs, unit) in enumerate(RULES):
            on, val = cfg.alert(rid)
            chk = Gtk.CheckButton(label=label)
            chk.set_active(on)
            chk.connect("toggled", lambda w, r=rid: cfg.set_alert(r, on=w.get_active()))
            spin = Gtk.SpinButton.new_with_range(0, 100 if unit == "%" else 120, 1)
            spin.set_value(val)
            spin.connect("value-changed", lambda w, r=rid: cfg.set_alert(r, value=w.get_value()))
            grid.attach(chk, 0, i, 1, 1)
            grid.attach(dim("below" if cmp_ == "<" else "above"), 1, i, 1, 1)
            grid.attach(spin, 2, i, 1, 1)
            grid.attach(dim(unit + (f"  for {secs}s" if secs else "")), 3, i, 1, 1)
        c.pack_start(grid, False, False, 0)
        b.pack_start(c, False, False, 0)
        lc, _ = make_card("Recent alerts", "alerts", "pink")
        self.alert_log = Gtk.Label(xalign=0, selectable=True)
        lc.pack_start(self.alert_log, False, False, 0)
        b.pack_start(lc, False, False, 0)
        self.updaters["alerts"] = self._up_alerts
        return b

    def _up_alerts(self, _s):
        log = self.col.alerts.log[-15:]
        self.alert_log.set_text("\n".join(f"{time.strftime('%H:%M:%S', time.localtime(t))}  {x}"
                                          for t, x in reversed(log)) or "none yet")

    # ----------------------------------------------------------------- system
    def _page_system(self):
        b = page_box()
        c, _ = make_card("System", "system", "blue")
        self.sys_kv = KV(list(self.col.sysinfo) + ["Uptime", "Vigil"])
        for k, v in self.col.sysinfo.items():
            self.sys_kv.set(k, v)
        self.sys_kv.set("Vigil", __version__)
        c.pack_start(self.sys_kv, False, False, 0)
        b.pack_start(c, False, False, 0)

        sc, _ = make_card("Settings", "services", "purple")
        cfg = self.col.cfg

        def check(label, active, cb):
            w = Gtk.CheckButton(label=label)
            w.set_active(active)
            w.connect("toggled", lambda x: cb(x.get_active()))
            sc.pack_start(w, False, False, 0)

        def put(key):
            return lambda v: (cfg.data.__setitem__(key, v), cfg.save())

        check("Keep running in the background when the window is closed (alerts keep working)",
              bool(cfg.data.get("background")), put("background"))
        check("Start at login (hidden)", os.path.exists(AUTOSTART), set_autostart)
        check("Show CPU / temperature in the top panel", cfg.data.get("indicator", True),
              lambda v: (put("indicator")(v), self.app.indicator_enable(v)))
        sc.pack_start(Gtk.Separator(), False, False, 4)
        sc.pack_start(Gtk.Label(label="Privileged data", xalign=0), False, False, 0)
        hl, hb = self.helper_controls()
        sc.pack_start(hl, False, False, 0)
        sc.pack_start(hb, False, False, 0)
        sc.pack_start(dim("Unlocks per-process network and disk for all users, Intel iGPU load (needs intel-gpu-tools)\n"
                          "and CPU package power. The helper only reads, and stops when Vigil closes."), False, False, 0)
        b.pack_start(sc, False, False, 0)
        self.updaters["system"] = lambda s: self.sys_kv.set("Uptime", ft(time.time() - psutil.boot_time()))
        return b


# ----------------------------------------------------------------------- app
class VigilApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.vigil.Vigil", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        pages = [i[0] for i in NAV if i] + [n for n, _ in RES]
        self.add_main_option("page", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Open a page: " + ", ".join(pages), "PAGE")
        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start hidden (alerts and panel indicator only)", None)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Print version", None)
        self.win = self.col = self.indicator = None

    def do_command_line(self, cl):
        opts = cl.get_options_dict().end().unpack()
        if self.win is None:
            self.col = Collector()
            self.col.start()
            time.sleep(1.2)  # let the first sample land so pages open populated
            self.win = VigilWindow(self, self.col)
            if self.col.cfg.data.get("indicator", True):
                self.indicator = Indicator(self)
            if opts.get("background") or self.col.cfg.data.get("background"):
                self.hold()
        if opts.get("page"):
            self.win.show_page(opts["page"])
        if not opts.get("background") or self.win.get_visible():
            self.win.present()
        return 0

    def show_window(self, page=None):
        if page:
            self.win.show_page(page)
        self.win.present()

    def indicator_update(self, snap):
        if self.indicator:
            self.indicator.update(snap)

    def indicator_enable(self, on):
        if on and not self.indicator:
            self.indicator = Indicator(self)
        if self.indicator and self.indicator.ind:
            m = self.indicator.mod
            self.indicator.ind.set_status(m.IndicatorStatus.ACTIVE if on else m.IndicatorStatus.PASSIVE)

    def do_shutdown(self):
        if self.col:
            self.col.stop()
        Gtk.Application.do_shutdown(self)


def main():
    if any(a in ("--version", "-v") for a in sys.argv[1:]):
        print("vigil", __version__)
        return 0
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    GLib.set_prgname("vigil")
    GLib.set_application_name("Vigil")
    return VigilApp().run(sys.argv)
