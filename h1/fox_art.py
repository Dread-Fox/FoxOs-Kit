"""DreadFox art — the "Fox Redesign" (Main + Moods artboards) drawn on a Tk canvas.

The redesign is a sitting, angular kitsune: tall dark-tipped ears, a spiky cheek ruff, white chest
and muzzle, fangs, claws and slanted glowing eyes. Each stage has its own coat palette, forehead
mark and eye glow; tails fan out behind and foxfire orbs sit on an arc above.

Everything here uses the design's own 200x200 viewBox coordinates and path data. ``build_fox``
returns a list of flat shapes; ``draw`` paints them onto a canvas at any size. Tk has no SVG,
gradients or alpha, so curves are flattened, gradients become layered rings and translucency is
pre-blended against the colour underneath.
"""
import math
import re

PANEL = "#151920"
INK = "#0E0809"
EAR_TIP = "#2B1E22"
EAR_SHADE = "#3A2629"
CLAW = "#F4EEE6"
MOUTH = "#3A0C12"

# Per stage (1-9): glow/foxfire, coat, outline/paws, tail tip, chest+muzzle, eye, cheek mark.
STAGE_ART = [
    dict(glow="#FF9F2E", fur="#D9772E", dark="#4A220E", tip="#F4E6D2", chest="#F2E4D2", eye="#FFB52E", cheek="#4A220E"),
    dict(glow="#FFB13B", fur="#D2601E", dark="#45190A", tip="#F6E4CE", chest="#F2E2CE", eye="#FFD23F", cheek="#45190A"),
    dict(glow="#FF5A36", fur="#C7361F", dark="#3E0E08", tip="#FFE2CC", chest="#F4E0D0", eye="#FFE14D", cheek="#3E0E08"),
    dict(glow="#6BEB5A", fur="#9C4A22", dark="#24140A", tip="#B6E07A", chest="#EADBC6", eye="#9CFF57", cheek="#24140A"),
    dict(glow="#A35CFF", fur="#8C2F4A", dark="#220914", tip="#D9B8FF", chest="#EBD9E0", eye="#C77DFF", cheek="#220914"),
    dict(glow="#7FC8FF", fur="#4E5F8A", dark="#10162A", tip="#DDE8FF", chest="#D9E2F2", eye="#6FD3FF", cheek="#10162A"),
    dict(glow="#CFE9FF", fur="#B9C0CC", dark="#262A33", tip="#FFFFFF", chest="#F2F4F8", eye="#7FE7FF", cheek="#262A33"),
    dict(glow="#FFC93B", fur="#C98A22", dark="#2E1C04", tip="#FFF0B8", chest="#F4E8CC", eye="#FF6A3D", cheek="#2E1C04"),
    dict(glow="#FF3B4A", fur="#F4EEE6", dark="#1E1416", tip="#D1142E", chest="#FFFFFF", eye="#FF2A2A", cheek="#D1142E"),
]

# Mood → face: head tilt (deg), ear splay (deg), eye style, mouth style, snarl lines, extra effect.
# The eight moods in the Moods artboard are exact; Sleepy, Inquisitive and Unhatched extend them.
MOOD_ART = {
    "Content":     dict(head=0,  ears=6,  eye="base",   mouth="smile", snarl=False, extra=None),
    "Delighted":   dict(head=0,  ears=6,  eye="soft",   mouth="grin",  snarl=False, extra="sparkle"),
    "Confident":   dict(head=0,  ears=6,  eye="soft",   mouth="smirk", snarl=False, extra=None),
    "Determined":  dict(head=0,  ears=6,  eye="base",   mouth="snarl", snarl=True,  extra=None),
    "Frustrated":  dict(head=0,  ears=40, eye="narrow", mouth="snarl", snarl=True,  extra="anger"),
    "Wary":        dict(head=0,  ears=40, eye="wide",   mouth="snarl", snarl=True,  extra=None),
    "Remembering": dict(head=5,  ears=6,  eye="lookup", mouth="smirk", snarl=False, extra="sparkle"),
    "Dreaming":    dict(head=-8, ears=6,  eye="closed", mouth="smile", snarl=False, extra="zzz"),
    "Sleepy":      dict(head=-4, ears=18, eye="closed", mouth="smile", snarl=False, extra="z"),
    "Inquisitive": dict(head=8,  ears=6,  eye="wide",   mouth="smile", snarl=False, extra=None),
    "Unhatched":   dict(head=-8, ears=18, eye="closed", mouth="smile", snarl=False, extra="zzz"),
}

# Eye shapes: upper-lid control y, lower-lid control y, pupil rx/ry, pupil shift, highlight.
EYES = {
    "base":   dict(up=89,   low=104,   rx=1.8, ry=5.5, dx=0,   dy=0,  hl=True),
    "soft":   dict(up=93.5, low=103.5, rx=1.8, ry=3,   dx=0,   dy=0,  hl=True),
    "narrow": dict(up=95.5, low=102,   rx=1.8, ry=1.8, dx=0,   dy=0,  hl=False),
    "wide":   dict(up=87,   low=105,   rx=1.0, ry=6,   dx=0,   dy=0,  hl=True),
    "lookup": dict(up=89,   low=104,   rx=1.8, ry=5,   dx=2.5, dy=-2, hl=True),
}

TAIL = "M -7,0 C -24,-21.0 -27,-42.0 -16,-57.4 L -7,-53.2 L -2,-75.6 L 6,-56.0 L 17,-61.6 L 14,-49.0 C 24,-38.5 20,-21.0 7,0 Z"
TAIL_SHINE = "M 1,-8 Q -5,-31.5 1,-49.0"
TIP_CLIP = (-40, -84.0, 40, -51.1)  # the design's clip rect: x0, y0, x1, y1 in tail space
BODY = "M 70,180 C 64,152 72,130 88,126 L 100,132 L 112,126 C 128,130 136,152 130,180 Z"
ARM_TUFTS = "M 64,160 L 58,150 L 68,152 Z M 136,160 L 142,150 L 132,152 Z"
CHEST = "M 86,134 L 93,141 L 100,136 L 107,141 L 114,134 L 114,152 L 108,163 L 104,159 L 100,173 L 96,159 L 92,163 L 86,152 Z"
PAWS = "M 78,184 L 78,174 Q 88,168 98,174 L 98,184 Z M 102,184 L 102,174 Q 112,168 122,174 L 122,184 Z"
CLAWS = "M 80,184 l 2,5 l 2,-5 M 86,184 l 2,5 l 2,-5 M 92,184 l 2,5 l 2,-5 M 104,184 l 2,5 l 2,-5 M 110,184 l 2,5 l 2,-5 M 116,184 l 2,5 l 2,-5"
EAR = "M 60,88 C 52,64 48,40 46,18 C 66,34 84,50 94,70 Z"
EAR_TIP_P = "M 46,18 C 54,25 62,32 69,38 L 51,50 C 49,40 47,29 46,18 Z"
EAR_INNER = "M 66,80 C 60,62 56,46 54,32 C 66,44 76,54 86,68 Z"
RUFF = ("M 100,60 L 124,64 L 142,78 L 150,95 L 168,100 L 154,107 L 164,116 L 146,117 L 150,126 L 132,124 "
        "L 100,142 L 68,124 L 50,126 L 54,117 L 36,116 L 46,107 L 32,100 L 50,95 L 58,78 L 76,64 Z")
MUZZLE = "M 100,102 L 116,105 L 148,104 L 138,114 L 142,120 L 126,122 L 100,142 L 74,122 L 58,120 L 62,114 L 52,104 L 84,105 Z"
SPARKLE = "M 150,58 L 152,64 L 158,66 L 152,68 L 150,74 L 148,68 L 142,66 L 148,64 Z"


# --- colour helpers ------------------------------------------------------------
def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def blend(fg, bg, t):
    """Move colour fg toward bg by t in [0,1]."""
    t = max(0.0, min(1.0, t))
    a, b = _rgb(fg), _rgb(bg)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def over(base, color, alpha):
    """``color`` painted at ``alpha`` opacity on top of ``base``."""
    return blend(base, color, alpha)


# --- geometry: affine transforms and SVG path flattening -----------------------
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m, n):
    """Compose: apply n first, then m."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def translate(x, y):
    return (1.0, 0.0, 0.0, 1.0, x, y)


def scale(s):
    return (s, 0.0, 0.0, s, 0.0, 0.0)


def rotate(deg, cx=0.0, cy=0.0):
    r = math.radians(deg)
    co, si = math.cos(r), math.sin(r)
    return mul(translate(cx, cy), mul((co, si, -si, co, 0.0, 0.0), translate(-cx, -cy)))


def apply(m, pts):
    a, b, c, d, e, f = m
    return [(a * x + c * y + e, b * x + d * y + f) for x, y in pts]


_TOKEN = re.compile(r"[MmLlCcQqZzAaHhVv]|-?\d*\.?\d+(?:e-?\d+)?")
_PATH_CACHE = {}


def _arc(x1, y1, rx, ry, phi, large, sweep, x2, y2, steps=16):
    """SVG endpoint arc → points (SVG 1.1 implementation notes, F.6.5)."""
    if rx == 0 or ry == 0:
        return [(x2, y2)]
    p = math.radians(phi)
    cp, sp = math.cos(p), math.sin(p)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    out = []
    for i in range(1, steps + 1):
        t = t1 + dt * i / steps
        out.append((cx + rx * math.cos(t) * cp - ry * math.sin(t) * sp,
                    cy + rx * math.cos(t) * sp + ry * math.sin(t) * cp))
    return out


def parse_path(d, steps=10):
    """Flatten SVG path data into subpaths: [(points, closed)]. Cached per string."""
    if d in _PATH_CACHE:
        return _PATH_CACHE[d]
    toks = _TOKEN.findall(d)
    subs, pts, closed = [], [], False
    x = y = sx = sy = 0.0
    i, cmd = 0, None

    def num():
        nonlocal i
        i += 1
        return float(toks[i - 1])

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            if pts:
                subs.append((pts, closed))
            x, y = num() + ox, num() + oy
            sx, sy, pts, closed = x, y, [(x, y)], False
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = num() + ox, num() + oy
            pts.append((x, y))
        elif c == "H":
            x = num() + ox
            pts.append((x, y))
        elif c == "V":
            y = num() + oy
            pts.append((x, y))
        elif c == "C":
            c1 = (num() + ox, num() + oy)
            c2 = (num() + ox, num() + oy)
            e = (num() + ox, num() + oy)
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                pts.append((mt ** 3 * x + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * e[0],
                            mt ** 3 * y + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * e[1]))
            x, y = e
        elif c == "Q":
            c1 = (num() + ox, num() + oy)
            e = (num() + ox, num() + oy)
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                pts.append((mt * mt * x + 2 * mt * t * c1[0] + t * t * e[0],
                            mt * mt * y + 2 * mt * t * c1[1] + t * t * e[1]))
            x, y = e
        elif c == "A":
            rx, ry, phi, large, sweep = num(), num(), num(), int(num()), int(num())
            ex, ey = num() + ox, num() + oy
            pts.extend(_arc(x, y, rx, ry, phi, large, sweep, ex, ey))
            x, y = ex, ey
        elif c == "Z":
            closed = True
            x, y = sx, sy
        else:  # pragma: no cover - the design only uses the commands above
            raise ValueError(f"unsupported path command {cmd}")
    if pts:
        subs.append((pts, closed))
    _PATH_CACHE[d] = subs
    return subs


def ellipse(cx, cy, rx, ry, n=20):
    return [(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n)]


def clip_rect(poly, x0, y0, x1, y1):
    """Sutherland–Hodgman polygon clip against an axis-aligned rectangle."""
    def clip(pts, inside, cross):
        out = []
        for i, cur in enumerate(pts):
            prev = pts[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        return out

    def at_x(xv):
        return lambda p, q: (xv, p[1] + (q[1] - p[1]) * (xv - p[0]) / ((q[0] - p[0]) or 1e-9))

    def at_y(yv):
        return lambda p, q: (p[0] + (q[0] - p[0]) * (yv - p[1]) / ((q[1] - p[1]) or 1e-9), yv)
    pts = poly
    for inside, cross in ((lambda p: p[0] >= x0, at_x(x0)), (lambda p: p[0] <= x1, at_x(x1)),
                          (lambda p: p[1] >= y0, at_y(y0)), (lambda p: p[1] <= y1, at_y(y1))):
        if not pts:
            break
        pts = clip(pts, inside, cross)
    return pts


# --- shape list -------------------------------------------------------------------
class Shapes(list):
    """Flat shapes in viewBox space: ('poly', pts, fill, outline, width) | ('line', pts, color, width, cap)
    | ('ring', cx, cy, r, color, width, dash) | ('text', x, y, text, color, size)."""

    def path(self, d, m=IDENT, fill="", outline="", width=0.0):
        for pts, closed in parse_path(d):
            if fill and (closed or len(pts) > 2):
                self.append(("poly", apply(m, pts), fill, outline, width))
            elif outline:
                self.append(("line", apply(m, pts), outline, width, "round"))

    def stroke(self, d, m=IDENT, color=INK, width=1.0, cap="round"):
        for pts, closed in parse_path(d):
            if closed and pts[-1] != pts[0]:
                pts = pts + [pts[0]]        # a stroked closed path draws its last edge too
            self.append(("line", apply(m, pts), color, width, cap))

    def blob(self, cx, cy, rx, ry, m=IDENT, fill=INK):
        self.append(("poly", apply(m, ellipse(cx, cy, rx, ry)), fill, "", 0))


def mirror(d):
    """Mirror path data across x=100 (the design draws the right side as a mirror of the left)."""
    toks = _TOKEN.findall(d)
    out, cmd, idx = [], None, 0
    for tok in toks:
        if tok.isalpha():
            cmd, idx = tok, 0
            out.append(tok)
            continue
        v = float(tok)
        upper = cmd.upper()
        if upper == "A":
            slot = idx % 7
            if slot == 4:            # sweep flag flips in a mirror
                v = 1 - v
            elif slot == 5:
                v = (-v if cmd.islower() else 200 - v)
        elif upper == "H":
            v = -v if cmd.islower() else 200 - v
        elif upper != "V" and idx % 2 == 0:
            v = -v if cmd.islower() else 200 - v
        idx += 1
        out.append(f"{v:g}")
    return " ".join(out)


# --- the fox ------------------------------------------------------------------------
def tail_angles(n):
    """Tail fan from the design: one tail curls to the side; more fan to ±(18.5+6.5n)°, max 75°."""
    if n <= 1:
        return [48.0]
    spread = min(75.0, 18.5 + 6.5 * n)
    angs = [-spread + k * 2 * spread / (n - 1) for k in range(n)]
    # draw order: outermost pair first, centre last (back to front)
    return sorted(angs, key=lambda a: (-abs(a), a))


def orb_positions(n):
    """Foxfire orbs on the design's arc: centre (100,112), radius 90, from -75° to +75°."""
    angs = [0.0] if n <= 1 else [-75 + k * 150 / (n - 1) for k in range(n)]
    return [(100 + 90 * math.sin(math.radians(a)), 112 - 90 * math.cos(math.radians(a))) for a in angs]


def _forehead(sh, stage, m, fur):
    if stage == 2:
        sh.blob(100, 76, 2.6, 2.6, m, "#FFE6CC")
    elif stage == 3:
        sh.stroke("M 93,72 L 100,79 L 107,72", m, "#FFE2C8", 2.6)
    elif stage == 4:
        sh.path("M 100,70 C 106,74 106,80 100,84 C 94,80 94,74 100,70 Z", m, "#4E9A5E")
        sh.stroke("M 100,72 L 100,82", m, over("#4E9A5E", "#FFFFFF", .6), 1)
    elif stage == 5:
        sh.stroke("M 100,69 L 105,77 L 100,85 L 95,77 Z", m, "#9B6BF0", 2.2)
        sh.blob(100, 77, 1.8, 1.8, m, "#9B6BF0")
    elif stage == 6:
        # The design's crescent path is degenerate (both arcs trace the same half circle, so the
        # browser draws nothing); this draws the crescent moon it was meant to be.
        sh.blob(100, 77, 7, 7, m, "#FFFFFF")
        sh.blob(103.2, 75.4, 6, 6, m, fur)
    elif stage == 7:
        for x, y in ((94, 78), (100, 73), (106, 78)):
            sh.blob(x, y, 2.1, 2.1, m, "#7C8BA6")
    elif stage == 8:
        star = [(100.0, 70.0), (101.8, 74.6), (106.7, 74.8), (102.9, 77.9), (104.1, 82.7), (100.0, 80.0),
                (95.9, 82.7), (97.1, 77.9), (93.3, 74.8), (98.2, 74.6)]
        sh.append(("poly", apply(m, star), "#FFF3C4", "", 0))
    elif stage == 9:
        sh.path("M 100,66 C 106,73 106,80 100,86 C 94,80 94,73 100,66 Z", m, "#E23A4E")


def _eye(sh, side, style, art, m, fur, blink):
    """One eye; side=-1 is the viewer's left. The right eye is the design's mirror image."""
    mx = (lambda d: d) if side < 0 else mirror
    ex = (lambda x: x) if side < 0 else (lambda x: 200 - x)
    sh.path(mx("M 72,100 L 64,113 L 76,104 Z"), m, art["cheek"])
    if style == "closed" or blink:
        sh.stroke(mx("M 89,100 L 69,92"), m, INK, 2.8)
    else:
        e = EYES[style]
        sh.path(mx(f"M 89,100 Q 80,{e['up']} 69,92 Q 78,{e['low']} 89,100 Z"), m, art["eye"])
        # pupils shift the same way in both eyes (a glance), so the shift is not mirrored
        sh.blob(ex(80) + e["dx"], 96 + e["dy"], e["rx"], e["ry"], m, INK)
        if e["hl"]:
            sh.blob(ex(82.6) + e["dx"], 93.8 + e["dy"], 1.3, 1.3, m, over(art["eye"], "#FFFFFF", .9))
        sh.stroke(mx(f"M 89,100 Q 80,{e['up']} 69,92"), m, INK, 2.8)
    sh.stroke(mx("M 69,92 l -4,-2"), m, INK, 2.4)
    sh.path(mx("M 67,82 L 89,90 L 87,93 L 68,86 Z"), m, over(fur, INK, .85))


def _mouth(sh, style, snarl, m):
    sh.path("M 94,111 L 106,111 L 100,118 Z", m, INK, INK, 1.5)
    sh.stroke("M 100,118 L 100,121", m, INK, 1.6, "butt")
    white = "#FFFFFF"
    if style == "smile":
        sh.stroke("M 90,123 L 100,125 L 110,123", m, INK, 2)
        sh.path("M 90.8,123.6 L 95.2,123.6 L 93,128.6 Z", m, white, INK, .6)
        sh.path("M 104.8,123.6 L 109.2,123.6 L 107,128.6 Z", m, white, INK, .6)
    elif style == "smirk":
        sh.stroke("M 91,123 L 100,125 L 111,120", m, INK, 2)
        sh.path("M 103.8,122.5 L 108.2,122.5 L 106,128.5 Z", m, white, INK, .6)
    elif style == "snarl":
        sh.path("M 89,121 L 111,121 L 108,130 L 100,133 L 92,130 Z", m, MOUTH, INK, 1.6)
        sh.path("M 89.8,121 L 94.2,121 L 92,128 Z", m, white, INK, .6)
        sh.path("M 105.8,121 L 110.2,121 L 108,128 Z", m, white, INK, .6)
        sh.path("M 95,121 L 97,124 L 99,121 L 101,121 L 103,124 L 105,121 Z", m, white)
        sh.path("M 95,132 L 97,128 L 99,132.5 Z M 105,132 L 103,128 L 101,132.5 Z", m, white)
    elif style == "grin":
        sh.path("M 86,122 L 114,122 L 111,129 L 100,132 L 89,129 Z", m, MOUTH, INK, 1.6)
        sh.path("M 86.8,122 L 91.2,122 L 89,129 Z", m, white, INK, .6)
        sh.path("M 108.8,122 L 113.2,122 L 111,129 Z", m, white, INK, .6)
        sh.path("M 92,122 L 94,125 L 96,122 L 104,122 L 106,125 L 108,122 Z", m, white)
        sh.path("M 95,131 L 97,127 L 99,131.5 Z M 105,131 L 103,127 L 101,131.5 Z", m, white)
    if snarl:
        sh.stroke("M 92,108 L 96,106 M 108,108 L 104,106 M 94,104 L 97,103 M 106,104 L 103,103", m, INK, 1.4)


def build_fox(stage, mood="Content", *, t=0.0, sway=1.0, fire=1.0, flicker=False, blink=False):
    """All shapes for one frame. ``stage`` is 1-9; ``t`` drives the idle animation.

    ``fire`` (0.25-1) dims the foxfire orbs and glow, ``flicker`` makes them waver,
    ``blink`` briefly closes open eyes.
    """
    stage = max(1, min(9, int(stage)))
    art = STAGE_ART[stage - 1]
    face = MOOD_ART.get(mood, MOOD_ART["Content"])
    fur, dark, glow = art["fur"], art["dark"], art["glow"]
    sh = Shapes()

    # aura: the radial glow as stacked discs fading to the panel
    for k in range(16, 0, -1):
        r = 96 * k / 16
        sh.blob(100, 112, r, r, IDENT, blend(PANEL, glow, 0.32 * (1 - r / 96) * (0.6 + 0.4 * fire)))
    if stage in (5, 8):
        sh.append(("ring", 100, 108, 84, over(PANEL, glow, .35), 1, (1, 6)))
    if stage == 9:
        sh.append(("ring", 100, 108, 86, over(PANEL, "#E8B04A", .55), 1.5, None))
        sh.append(("ring", 100, 108, 80, over(PANEL, "#E8B04A", .25), 1, (2, 5)))

    # tails, back to front, with a gentle sway
    for i, a in enumerate(tail_angles(stage)):
        a += math.sin(t * 2.2 + i * 0.6) * 3.0 * sway
        m = mul(translate(100, 152), mul(rotate(a), scale(1 - abs(a) / 750)))
        outline = parse_path(TAIL)[0][0]
        sh.append(("poly", apply(m, outline), fur, over(fur, dark, .35), 1.2))
        tip = clip_rect(outline, *TIP_CLIP)
        if tip:
            sh.append(("poly", apply(m, tip), art["tip"], "", 0))
        sh.stroke(TAIL_SHINE, m, over(fur, "#FFFFFF", .22), 3)

    # foxfire orbs: soft glow, coloured body, white-hot core; they bob and dim with ``fire``
    for i, (x, y) in enumerate(orb_positions(stage)):
        y += math.sin(t * 1.5 + i * 1.1) * 1.6
        lv = fire * (0.65 + 0.35 * abs(math.sin(t * 9 + i * 1.7))) if flicker else fire
        for k in range(8, 0, -1):
            f = k / 8                               # radius fraction of the 8-unit orb
            col = (blend(PANEL, glow, lv * (1 - f) / 0.65) if f > 0.35
                   else blend(blend(PANEL, glow, lv), "#FFFFFF", 1 - f / 0.35))
            sh.blob(x, y, 8 * f, 8 * f, IDENT, col)
        sh.blob(x, y, 2.4, 2.4, IDENT, blend(PANEL, "#FFFFFF", 0.4 + 0.6 * lv))

    # body
    sh.path(BODY, IDENT, fur)
    sh.path(ARM_TUFTS, IDENT, fur)
    sh.path(CHEST, IDENT, art["chest"])
    sh.path(PAWS, IDENT, dark)
    sh.path(CLAWS, IDENT, CLAW, CLAW, .8)

    # head (tilts with mood)
    hm = rotate(face["head"], 100, 100)
    inner = over(over(fur, EAR_SHADE, .55), art["chest"], .35)
    for side, pivot in ((-1, (74, 76)), (1, (126, 76))):
        em = mul(hm, rotate(side * face["ears"], *pivot))
        mx = (lambda d: d) if side < 0 else mirror
        sh.path(mx(EAR), em, fur)
        sh.path(mx(EAR_TIP_P), em, EAR_TIP)
        sh.path(mx(EAR_INNER), em, inner)
    sh.path(RUFF, hm, fur, over(fur, dark, .5), 1.2)
    sh.path(MUZZLE, hm, art["chest"])
    _forehead(sh, stage, hm, fur)
    for side in (-1, 1):
        _eye(sh, side, face["eye"], art, hm, fur, blink)
    _mouth(sh, face["mouth"], face["snarl"], hm)

    # mood extras
    extra = face["extra"]
    if extra == "sparkle":
        tw = 0.85 + 0.15 * math.sin(t * 4)
        sm = mul(translate(150, 66), mul(scale(tw), translate(-150, -66)))
        sh.path(SPARKLE, sm, "#FFE08A")
        sh.blob(162, 52, 2 * tw, 2 * tw, IDENT, "#FFE08A")
    elif extra == "anger":
        jit = math.sin(t * 12) * 0.6
        sh.stroke(f"M {146 + jit},66 l 6,-6 M {150 + jit},74 l 9,-2 M {140 + jit},62 l 1,-8", IDENT, "#FF6B6B", 2.4)
    elif extra in ("zzz", "z"):
        drift = (t * 0.35) % 1.0
        letters = ((146, 70, 16, 1.0), (158, 54, 12, .75), (167, 42, 9, .5))
        for x, y, size, op in letters[:3 if extra == "zzz" else 1]:
            sh.append(("text", x + drift * 4, y - drift * 6, "z", blend(PANEL, "#C9D6F2", op * (1 - 0.5 * drift)), size))
    return sh


def draw(canvas, shapes, ox, oy, s, tag="fox"):
    """Paint shapes onto a Tk canvas; viewBox point (x, y) lands at (ox + s*x, oy + s*y)."""
    for shp in shapes:
        kind = shp[0]
        if kind == "poly":
            _, pts, fill, outline, width = shp
            flat = [v for x, y in pts for v in (ox + s * x, oy + s * y)]
            if len(flat) >= 6:
                canvas.create_polygon(flat, fill=fill, outline=outline or "", width=max(0, width * s) if outline else 0,
                                      joinstyle="round", tags=tag)
        elif kind == "line":
            _, pts, color, width, cap = shp
            flat = [v for x, y in pts for v in (ox + s * x, oy + s * y)]
            if len(flat) >= 4:
                canvas.create_line(flat, fill=color, width=max(1, width * s), capstyle=cap, joinstyle="round", tags=tag)
        elif kind == "ring":
            _, cx, cy, r, color, width, dash = shp
            kw = {"dash": tuple(max(1, round(d * s)) for d in dash)} if dash else {}
            canvas.create_oval(ox + s * (cx - r), oy + s * (cy - r), ox + s * (cx + r), oy + s * (cy + r),
                               outline=color, width=max(1, width * s), tags=tag, **kw)
        elif kind == "text":
            _, x, y, text, color, size = shp
            canvas.create_text(ox + s * x, oy + s * y, text=text, fill=color, anchor="sw",
                               font=("DejaVu Sans", max(6, round(size * s * 0.75)), "bold"), tags=tag)
