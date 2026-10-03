"""Visual identity: dark-purple CSS and cairo-drawn icon glyphs (no icon files needed)."""
import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

CSS = b"""
.vigil { background-color: #13102a; color: #e9e7f7; }
.vigil label { color: #e9e7f7; }
.vigil label.dim { color: #8a86ab; }
.vigil label.card-title { font-weight: 600; font-size: 12pt; }
.vigil label.big { font-size: 20pt; font-weight: 700; }
.vigil label.mono { font-family: monospace; }
.vigil separator { background-color: #262148; min-height: 1px; min-width: 1px; }

.vigil .sidebar { background-color: #17142f; border-right: 1px solid #262148; }
.vigil .sidefoot { background-color: #1a1636; border: 1px solid #2a2552; border-radius: 10px; padding: 10px 12px; }
.vigil .navhead { color: #6f6a94; font-size: 9pt; font-weight: 600; }

.vigil .card { background-color: #1a1636; border: 1px solid #2a2552; border-radius: 14px; padding: 14px 16px; }
.vigil .statcard { background-color: #1a1636; border: 1px solid #2a2552; border-radius: 14px; padding: 14px 16px; }
.vigil .statcard:hover { border-color: #4a3f9a; }

.vigil button { background-image: none; background-color: #231e48; color: #e9e7f7; border: 1px solid #332c63;
                border-radius: 9px; padding: 5px 12px; box-shadow: none; text-shadow: none; }
.vigil button:hover { background-color: #2d2760; }
.vigil button:checked, .vigil button:active { background-color: #6a4be0; border-color: #7d5cf2; color: #ffffff; }
.vigil button.nav { background-color: transparent; border-color: transparent; padding: 9px 12px; border-radius: 11px; }
.vigil button.nav:hover { background-color: rgba(139,108,247,0.14); }
.vigil button.nav.active { background-image: linear-gradient(to right, #6a4be0, #7d5cf2); border-color: transparent; color: #ffffff; }
.vigil button.pill { background-color: transparent; border-color: #332c63; border-radius: 18px; padding: 4px 18px; }
.vigil button.pill:hover { background-color: rgba(139,108,247,0.14); }
.vigil button.pill:checked { background-image: linear-gradient(to right, #6a4be0, #7d5cf2); border-color: transparent; }
.vigil button.danger { background-color: #4a1f3a; border-color: #7a2f55; }
.vigil button.accent { background-image: linear-gradient(to right, #6a4be0, #7d5cf2); border-color: transparent; color: #fff; }

.vigil entry, .vigil spinbutton { background-image: none; background-color: #120f26; color: #e9e7f7; border: 1px solid #2a2552;
                                  border-radius: 9px; padding: 5px 10px; box-shadow: none; min-height: 18px; }
.vigil spinbutton button { border: none; border-radius: 0; background-color: transparent; padding: 0 6px; }
.vigil check, .vigil radio { background-image: none; background-color: #120f26; border: 1px solid #4a4290; border-radius: 5px;
                             min-width: 16px; min-height: 16px; color: #ffffff; box-shadow: none; }
.vigil check:checked, .vigil radio:checked { background-color: #7d5cf2; border-color: #7d5cf2; }

.vigil scrolledwindow, .vigil viewport { background-color: transparent; border: none; }
.vigil scrollbar { background-color: transparent; border: none; }
.vigil scrollbar slider { background-color: #3b3470; border-radius: 6px; min-width: 7px; min-height: 7px; border: none; }
.vigil scrollbar slider:hover { background-color: #5a4fa8; }

.vigil treeview { background-color: transparent; color: #e9e7f7; }
.vigil treeview.view:selected { background-color: #4b38a8; color: #ffffff; }
.vigil treeview.view:hover { background-color: rgba(139,108,247,0.10); }
.vigil treeview header button { background-image: none; background-color: transparent; color: #8a86ab; border: none;
                                border-bottom: 1px solid #2a2552; border-radius: 0; box-shadow: none; padding: 6px 8px; }
.vigil treeview header button label { color: #8a86ab; font-weight: normal; }
.vigil progressbar trough { background-color: #120f26; border: none; border-radius: 9px; min-height: 18px; }
.vigil progressbar progress { background-image: linear-gradient(to right, #6a4be0, #a78bfa); border: none; border-radius: 9px; min-height: 18px; }
.vigil progressbar text { color: #ffffff; font-size: 9pt; }
.vigil frame > border { border: none; }
"""

TILES = {
    "purple": ((0.55, 0.42, 1.00), (0.36, 0.24, 0.85)),
    "blue": ((0.31, 0.60, 1.00), (0.18, 0.38, 0.85)),
    "pink": ((1.00, 0.36, 0.62), (0.82, 0.22, 0.48)),
    "green": ((0.26, 0.80, 0.53), (0.17, 0.62, 0.41)),
    "orange": ((1.00, 0.64, 0.31), (0.88, 0.46, 0.17)),
    "teal": ((0.25, 0.80, 0.78), (0.16, 0.60, 0.58)),
    "red": ((1.00, 0.42, 0.42), (0.84, 0.26, 0.30)),
}


def apply_css():
    Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)


# ------------------------------------------------------------------- glyphs
def _poly(cr, pts, close=False):
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    if close:
        cr.close_path()


def _rr(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _dot(cr, x, y, r=0.04):
    cr.new_sub_path()
    cr.arc(x, y, r, 0, 2 * math.pi)
    cr.fill()


def g_overview(cr):
    _poly(cr, [(.06, .55), (.30, .55), (.42, .20), (.60, .85), (.72, .45), (.80, .55), (.94, .55)])
    cr.stroke()


def g_processes(cr):
    for y in (.25, .5, .75):
        _dot(cr, .14, y)
        _poly(cr, [(.30, y), (.90, y)])
    cr.stroke()


def g_resources(cr):
    cr.arc(.5, .5, .4, 0, 2 * math.pi)
    cr.stroke()
    _poly(cr, [(.5, .10), (.5, .5), (.85, .70)])
    cr.stroke()


def g_storage(cr):
    _rr(cr, .12, .22, .76, .56, .10)
    cr.stroke()
    _poly(cr, [(.12, .60), (.88, .60)])
    cr.stroke()
    _dot(cr, .74, .69, .03)


def g_network(cr):
    cr.arc(.5, .5, .4, 0, 2 * math.pi)
    cr.stroke()
    cr.save()
    cr.translate(.5, .5)
    cr.scale(.18, .4)
    cr.new_sub_path()
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    cr.stroke()
    _poly(cr, [(.1, .5), (.9, .5)])
    cr.stroke()


def g_cpu(cr):
    _rr(cr, .26, .26, .48, .48, .06)
    cr.stroke()
    for t in (.40, .60):
        _poly(cr, [(t, .10), (t, .26)])
        _poly(cr, [(t, .74), (t, .90)])
        _poly(cr, [(.10, t), (.26, t)])
        _poly(cr, [(.74, t), (.90, t)])
    cr.stroke()
    _rr(cr, .40, .40, .20, .20, .03)
    cr.fill()


def g_memory(cr):
    _rr(cr, .08, .26, .84, .42, .06)
    cr.stroke()
    for x in (.28, .50, .72):
        _poly(cr, [(x, .38), (x, .56)])
    for x in (.22, .38, .54, .70):
        _poly(cr, [(x, .68), (x, .84)])
    cr.stroke()


def g_swap(cr):
    _poly(cr, [(.14, .34), (.86, .34)])
    _poly(cr, [(.68, .17), (.86, .34), (.68, .51)])
    _poly(cr, [(.86, .68), (.14, .68)])
    _poly(cr, [(.32, .51), (.14, .68), (.32, .85)])
    cr.stroke()


def g_gpu(cr):
    _rr(cr, .06, .26, .88, .46, .06)
    cr.stroke()
    cr.arc(.36, .49, .12, 0, 2 * math.pi)
    cr.stroke()
    _poly(cr, [(.62, .40), (.80, .40)])
    _poly(cr, [(.62, .58), (.80, .58)])
    _poly(cr, [(.20, .72), (.20, .84)])
    cr.stroke()


def g_battery(cr):
    _rr(cr, .08, .28, .74, .44, .07)
    cr.stroke()
    _rr(cr, .84, .43, .08, .14, .02)
    cr.fill()
    _rr(cr, .17, .37, .30, .26, .03)
    cr.fill()


def g_sensors(cr):
    _poly(cr, [(.42, .62), (.42, .20)])
    cr.arc(.5, .20, .08, math.pi, 2 * math.pi)
    _poly(cr, [(.58, .20), (.58, .62)])
    cr.stroke()
    cr.arc(.5, .74, .15, 0, 2 * math.pi)
    cr.stroke()
    _poly(cr, [(.5, .74), (.5, .36)])
    cr.stroke()


def g_services(cr):
    cr.arc(.5, .5, .17, 0, 2 * math.pi)
    cr.stroke()
    for i in range(8):
        a = i * math.pi / 4
        _poly(cr, [(.5 + .27 * math.cos(a), .5 + .27 * math.sin(a)), (.5 + .40 * math.cos(a), .5 + .40 * math.sin(a))])
    cr.stroke()


def g_diagnose(cr):
    cr.move_to(.5, .08)
    cr.line_to(.86, .22)
    cr.line_to(.86, .50)
    cr.curve_to(.86, .75, .62, .88, .5, .93)
    cr.curve_to(.38, .88, .14, .75, .14, .50)
    cr.line_to(.14, .22)
    cr.close_path()
    cr.stroke()
    _poly(cr, [(.33, .50), (.46, .63), (.68, .38)])
    cr.stroke()


def g_alerts(cr):
    cr.move_to(.22, .72)
    cr.line_to(.78, .72)
    cr.stroke()
    cr.move_to(.28, .72)
    cr.curve_to(.28, .52, .30, .18, .5, .18)
    cr.curve_to(.70, .18, .72, .52, .72, .72)
    cr.stroke()
    cr.arc(.5, .82, .07, 0, math.pi)
    cr.stroke()


def g_system(cr):
    cr.arc(.5, .5, .4, 0, 2 * math.pi)
    cr.stroke()
    _poly(cr, [(.5, .46), (.5, .72)])
    cr.stroke()
    _dot(cr, .5, .30, .045)


def g_terminal(cr):
    _rr(cr, .08, .18, .84, .64, .08)
    cr.stroke()
    _poly(cr, [(.24, .38), (.40, .50), (.24, .62)])
    _poly(cr, [(.50, .64), (.72, .64)])
    cr.stroke()


GLYPHS = {"overview": g_overview, "processes": g_processes, "resources": g_resources, "storage": g_storage,
          "network": g_network, "cpu": g_cpu, "memory": g_memory, "swap": g_swap, "gpu": g_gpu,
          "battery": g_battery, "sensors": g_sensors, "services": g_services, "diagnose": g_diagnose,
          "alerts": g_alerts, "system": g_system, "terminal": g_terminal}


class Icon(Gtk.DrawingArea):
    """A glyph, optionally on a gradient rounded-square tile (the Unity-style app icon look)."""

    def __init__(self, glyph, size=20, tile=None, color=(0.80, 0.78, 0.95, 1.0)):
        super().__init__()
        self.glyph, self.size, self.tile, self.color = glyph, size, tile, color
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_color(self, color):
        self.color = color
        self.queue_draw()

    def _draw(self, _w, cr):
        s = self.size
        if self.tile:
            c1, c2 = TILES[self.tile]
            g = cairo.LinearGradient(0, 0, 0, s)
            g.add_color_stop_rgb(0, *c1)
            g.add_color_stop_rgb(1, *c2)
            _rr(cr, 0, 0, s, s, s * .28)
            cr.set_source(g)
            cr.fill()
            inset = s * .20
            cr.set_source_rgb(1, 1, 1)
        else:
            inset = 0
            cr.set_source_rgba(*self.color)
        box = s - 2 * inset
        cr.save()
        cr.translate(inset, inset)
        cr.scale(box, box)
        cr.set_line_width(.085)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        GLYPHS[self.glyph](cr)
        cr.restore()
        return False
