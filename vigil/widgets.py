"""Reusable widgets in the Vigil visual style."""
import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .theme import Icon  # noqa: E402

esc = GLib.markup_escape_text

COL = {"purple": (0.655, 0.545, 0.980), "violet": (0.55, 0.36, 0.96), "blue": (0.36, 0.62, 1.0),
       "pink": (1.0, 0.36, 0.62), "green": (0.30, 0.85, 0.55), "orange": (1.0, 0.64, 0.30),
       "red": (1.0, 0.40, 0.40), "teal": (0.25, 0.80, 0.78)}
DIMC = (0.54, 0.525, 0.67)
TRACK = (0.075, 0.06, 0.15)
BORDER = (0.165, 0.145, 0.32)


def hexc(c):
    return "#%02x%02x%02x" % tuple(int(x * 255) for x in c[:3])


# ---------------------------------------------------------------- formatting
def fb(n):
    n = float(n or 0)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or u == "TiB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def fr(n):
    return fb(n) + "/s"


def ft(sec):
    if sec is None:
        return "–"
    return f"{int(sec // 3600)}h {int(sec % 3600 // 60):02d}m"


FMT = {"pct": lambda v: f"{v:.0f}%", "temp": lambda v: f"{v:.0f}°C", "rate": fr, "watt": lambda v: f"{v:.1f} W"}
AXIS = {"pct": lambda v: f"{v:.0f}%", "temp": lambda v: f"{v:.0f}°", "rate": fb, "watt": lambda v: f"{v:.0f} W"}
XLAB = {60: ("1 min ago", "30 secs ago"), 600: ("10 min ago", "5 min ago"), 3600: ("1 hour ago", "30 min ago"),
        86400: ("24 hours ago", "12 hours ago"), 604800: ("7 days ago", "3.5 days ago")}


# ------------------------------------------------------------------- helpers
def dim(text="", xalign=0.0):
    l = Gtk.Label(label=text, xalign=xalign)
    l.get_style_context().add_class("dim")
    return l


def cls(widget, *names):
    for n in names:
        widget.get_style_context().add_class(n)
    return widget


def scrolled(child, h=Gtk.PolicyType.AUTOMATIC):
    sw = Gtk.ScrolledWindow()
    sw.set_policy(h, Gtk.PolicyType.AUTOMATIC)
    sw.add(child)
    return sw


def page_box(spacing=14):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for side in ("start", "end", "top", "bottom"):
        getattr(b, "set_margin_" + side)(20)
    return b


def hbox(*children, spacing=14, homogeneous=False, expand=True):
    b = Gtk.Box(spacing=spacing, homogeneous=homogeneous)
    for c in children:
        b.pack_start(c, expand, True, 0)
    return b


def make_card(title=None, glyph=None, tile="purple"):
    """Returns (card_box, readout_label). Content goes in card_box via pack_start."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    cls(box, "card")
    right = None
    if title:
        head = Gtk.Box(spacing=10)
        if glyph:
            head.pack_start(Icon(glyph, 30, tile=tile), False, False, 0)
        head.pack_start(cls(Gtk.Label(label=title, xalign=0), "card-title"), False, False, 0)
        right = Gtk.Label(xalign=1)
        right.set_use_markup(True)
        head.pack_end(right, False, False, 0)
        box.pack_start(head, False, False, 0)
    return box, right


class KV(Gtk.Grid):
    def __init__(self, keys):
        super().__init__(column_spacing=22, row_spacing=6)
        self.vals = {}
        for i, k in enumerate(keys):
            self.attach(dim(k), 0, i, 1, 1)
            v = Gtk.Label(label="–", xalign=0, selectable=True)
            self.attach(v, 1, i, 1, 1)
            self.vals[k] = v

    def set(self, k, text):
        self.vals[k].set_text(text)


class Rows:
    """Keep a ListStore in sync with keyed rows without rebuilding it (preserves scroll/selection)."""

    def __init__(self, store):
        self.store, self.it = store, {}

    def update(self, keyed):
        seen = set()
        for k, r in keyed:
            seen.add(k)
            it = self.it.get(k)
            if it is None:
                self.it[k] = self.store.append(list(r))
            else:
                self.store.set(it, list(range(len(r))), list(r))
        for k in [k for k in self.it if k not in seen]:
            self.store.remove(self.it.pop(k))


class Pills(Gtk.Box):
    """Rounded tab switcher bound to a Gtk.Stack."""

    def __init__(self, stack, items):
        super().__init__(spacing=8)
        self.stack, self.btns, self._lock = stack, {}, False
        for name, label in items:
            b = cls(Gtk.ToggleButton(label=label), "pill")
            b.connect("toggled", self._toggled, name)
            self.pack_start(b, False, False, 0)
            self.btns[name] = b
        self.select(items[0][0])

    def _toggled(self, btn, name):
        if self._lock:
            return
        if not btn.get_active():
            self.select(name)  # keep exactly one pressed
            return
        self.select(name)

    def select(self, name):
        self._lock = True
        for n, b in self.btns.items():
            b.set_active(n == name)
        self._lock = False
        self.stack.set_visible_child_name(name)


# ---------------------------------------------------------------------- graph
class Graph(Gtk.DrawingArea):
    def __init__(self, win, series, fmt="pct", ymax=None, floor=1.0, height=160, axes=True):
        super().__init__()
        self.win, self.series, self.fmt, self.ymax, self.floor, self.axes = win, series, fmt, ymax, floor, axes
        self.label = None  # header label that mirrors current values
        self.set_size_request(120, height)
        self.set_hexpand(True)
        self.connect("draw", self.on_draw)
        win.graphs.append(self)

    def _last(self, key):
        d = self.win.col.hist[key].get(60)
        return next((v for v in reversed(d) if v is not None), None)

    def readout(self):
        parts = []
        for k, lab, c in self.series:
            v = self._last(k)
            txt = (lab + " " if lab else "") + (FMT[self.fmt](v) if v is not None else "–")
            parts.append(f'<span foreground="{hexc(c)}">{esc(txt)}</span>')
        return "   ".join(parts)

    def on_draw(self, _w, cr):
        W, H = self.get_allocated_width(), self.get_allocated_height()
        L = (66 if self.fmt == "rate" else 50) if self.axes else 2
        B = 22 if self.axes else 2
        T, R = 6, 6
        pw, ph = W - L - R, H - T - B
        rng, hist = self.win.range, self.win.col.hist
        data = [hist[k].get(rng) for k, _, _ in self.series]
        slots = max(hist[self.series[0][0]].slots(rng), 2)
        vals = [v for d in data for v in d if v is not None]
        ymax = self.ymax or max(max(vals, default=0) * 1.25, self.floor)

        # plot box + grid
        cr.set_source_rgb(*TRACK)
        cr.rectangle(L, T, pw, ph)
        cr.fill()
        cr.set_line_width(1)
        cr.set_source_rgba(0.55, 0.5, 0.9, 0.13)
        steps = (0, .25, .5, .75, 1) if self.fmt == "pct" else (0, .5, 1)
        for f in steps:
            y = round(T + ph * (1 - f)) + .5
            cr.move_to(L, y)
            cr.line_to(L + pw, y)
        cr.move_to(round(L + pw / 2) + .5, T)
        cr.line_to(round(L + pw / 2) + .5, T + ph)
        cr.stroke()
        cr.set_source_rgba(0.55, 0.5, 0.9, 0.28)
        cr.rectangle(L + .5, T + .5, pw - 1, ph - 1)
        cr.stroke()

        # series
        cr.save()
        cr.rectangle(L, T, pw, ph)
        cr.clip()
        step = max(1, int(slots / max(pw, 1)))
        for si, (d, (_k, _lab, color)) in enumerate(zip(data, self.series)):
            n = len(d)
            segs, seg = [[]], None
            seg = segs[0]
            for i in list(range(n - 1, -1, -step))[::-1]:
                v = d[i]
                if v is None:
                    if seg:
                        seg = []
                        segs.append(seg)
                    continue
                seg.append((L + pw - (n - 1 - i) * pw / (slots - 1), T + ph * (1 - min(v / ymax, 1))))
            for seg in segs:
                if len(seg) < 2:
                    continue
                grad = cairo.LinearGradient(0, T, 0, T + ph)
                grad.add_color_stop_rgba(0, *color, 0.42 if si == 0 else 0.20)
                grad.add_color_stop_rgba(1, *color, 0.02)
                cr.move_to(seg[0][0], T + ph)
                for x, y in seg:
                    cr.line_to(x, y)
                cr.line_to(seg[-1][0], T + ph)
                cr.close_path()
                cr.set_source(grad)
                cr.fill()
                cr.move_to(*seg[0])
                for x, y in seg[1:]:
                    cr.line_to(x, y)
                cr.set_source_rgba(*color, 1)
                cr.set_line_width(1.8)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.stroke()
        cr.restore()

        if self.axes:
            cr.select_font_face("Sans")
            cr.set_font_size(10.5)
            cr.set_source_rgb(*DIMC)
            for f in steps:
                txt = AXIS[self.fmt](ymax * f)
                e = cr.text_extents(txt)
                cr.move_to(L - 8 - e.x_advance, T + ph * (1 - f) + 4)
                cr.show_text(txt)
            left, mid = XLAB.get(rng, ("", ""))
            cr.move_to(L, H - 6)
            cr.show_text(left)
            cr.move_to(L + pw / 2 - cr.text_extents(mid).x_advance / 2, H - 6)
            cr.show_text(mid)
            cr.move_to(L + pw - cr.text_extents("Now").x_advance, H - 6)
            cr.show_text("Now")
        return False


class Spark(Gtk.DrawingArea):
    """Tiny trend line for stat cards (always the last minute)."""

    def __init__(self, win, key, color, ymax=None, floor=1.0):
        super().__init__()
        self.win, self.key, self.color, self.ymax, self.floor = win, key, color, ymax, floor
        self.set_size_request(48, 44)
        self.set_valign(Gtk.Align.END)
        self.connect("draw", self.on_draw)
        win.graphs.append(self)

    def on_draw(self, _w, cr):
        W, H = self.get_allocated_width(), self.get_allocated_height()
        d = self.win.col.hist[self.key].get(60)
        vals = [v for v in d if v is not None]
        if len(vals) < 2:
            return False
        ymax = self.ymax or max(max(vals) * 1.2, self.floor)
        pts = [(W - (len(d) - 1 - i) * W / 59, 3 + (H - 6) * (1 - min(v / ymax, 1))) for i, v in enumerate(d) if v is not None]
        grad = cairo.LinearGradient(0, 0, 0, H)
        grad.add_color_stop_rgba(0, *self.color, 0.35)
        grad.add_color_stop_rgba(1, *self.color, 0.0)
        cr.move_to(pts[0][0], H)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(pts[-1][0], H)
        cr.close_path()
        cr.set_source(grad)
        cr.fill()
        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        cr.set_source_rgba(*self.color, 1)
        cr.set_line_width(1.6)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()
        return False


class Donut(Gtk.DrawingArea):
    def __init__(self, size=124):
        super().__init__()
        self.size = size
        self.parts, self.big, self.small = [], "", ""
        self.set_size_request(size, size)
        self.connect("draw", self.on_draw)

    def set(self, parts, big, small):
        self.parts, self.big, self.small = parts, big, small
        self.queue_draw()

    def on_draw(self, _w, cr):
        s = self.size
        cx = cy = s / 2
        r, th = s / 2 - 6, 15
        cr.set_line_width(th)
        cr.set_source_rgb(0.17, 0.15, 0.31)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        a = -math.pi / 2
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        for frac, color in self.parts:
            if frac <= 0.002:
                continue
            b = a + 2 * math.pi * min(frac, 1)
            cr.set_source_rgb(*color)
            cr.arc(cx, cy, r, a, b)
            cr.stroke()
            a = b
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(19)
        cr.set_source_rgb(0.93, 0.91, 0.99)
        e = cr.text_extents(self.big)
        cr.move_to(cx - e.x_advance / 2, cy + 2)
        cr.show_text(self.big)
        cr.select_font_face("Sans")
        cr.set_font_size(10)
        cr.set_source_rgb(*DIMC)
        e = cr.text_extents(self.small)
        cr.move_to(cx - e.x_advance / 2, cy + 18)
        cr.show_text(self.small)
        return False


class HBar(Gtk.DrawingArea):
    def __init__(self, height=9, color=COL["violet"]):
        super().__init__()
        self.frac, self.h, self.color = 0.0, height, color
        self.set_size_request(40, height)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self.on_draw)

    def set(self, frac):
        self.frac = max(0.0, min(frac, 1.0))
        self.queue_draw()

    def on_draw(self, _w, cr):
        W, H = self.get_allocated_width(), self.h
        r = H / 2
        cr.set_source_rgb(0.17, 0.15, 0.31)
        self._pill(cr, 0, W, H, r)
        cr.fill()
        w = max(self.frac * W, H if self.frac > 0 else 0)
        if w:
            g = cairo.LinearGradient(0, 0, W, 0)
            g.add_color_stop_rgb(0, 0.42, 0.29, 0.88)
            g.add_color_stop_rgb(1, *self.color)
            cr.set_source(g)
            self._pill(cr, 0, w, H, r)
            cr.fill()
        return False

    @staticmethod
    def _pill(cr, x, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, r, r, -math.pi / 2, math.pi / 2)
        cr.arc(x + r, r, r, math.pi / 2, 1.5 * math.pi)
        cr.close_path()


class StatCard(Gtk.Box):
    """Top-row card: icon tile, title, big value, subtitle, sparkline."""

    def __init__(self, win, title, glyph, tile, color, key, ymax=None, floor=1.0):
        super().__init__(spacing=12)
        cls(self, "statcard")
        self.set_hexpand(True)
        self.pack_start(Icon(glyph, 44, tile=tile), False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        col.set_valign(Gtk.Align.CENTER)
        self.title = dim(title)
        self.v = cls(Gtk.Label(xalign=0), "big")
        self.s = dim("")
        for lab, mx, wc in ((self.title, 18, 4), (self.v, 10, 3), (self.s, 18, 4)):
            lab.set_ellipsize(3)       # let cards shrink instead of forcing the window wide
            lab.set_width_chars(wc)
            lab.set_max_width_chars(mx)
        for w in (self.title, self.v, self.s):
            col.pack_start(w, False, False, 0)
        self.pack_start(col, True, True, 0)
        if key:
            self.pack_end(Spark(win, key, color, ymax, floor), False, False, 0)

    def set(self, value, sub=""):
        self.v.set_text(value)
        self.s.set_text(sub)
