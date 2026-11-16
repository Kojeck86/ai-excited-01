# main.py — "Koi Over the City"
# A program that draws one exciting image, procedurally, with only the
# Python standard library. It writes a single PNG to
# $MAKEFUN_OUTPUT_DIR/entry.png (or /output/entry.png).
#
# Scene: a giant luminous koi arcs across a twilight sky above a tiny
# city skyline — a surreal scale twist, strong momentum, vivid colour.
# Everything is drawn by a small software rasterizer (scanline polygon
# fills, circles, ribbons, per-row gradients) and encoded with a hand
# written PNG writer (zlib + CRC32 chunk framing).
#
# Deterministic: one fixed-seed LCG drives every pseudo-random choice.
# No time, no OS randomness, no network, no file reads. Two runs write
# byte-identical output.

import binascii
import math
import os
import struct
import zlib

W = 1024
H = 1024

# ---------------------------------------------------------------------------
# Deterministic pseudo-random numbers (LCG, fixed seed).
# ---------------------------------------------------------------------------
_seed = 20261116


def rnd():
    global _seed
    _seed = (_seed * 1103515245 + 12345) & 0x7FFFFFFF
    return _seed / 0x7FFFFFFF


def rrange(lo, hi):
    return lo + (hi - lo) * rnd()


# ---------------------------------------------------------------------------
# Colour helpers.
# ---------------------------------------------------------------------------
def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c0, c1, t):
    return (
        int(lerp(c0[0], c1[0], t)),
        int(lerp(c0[1], c1[1], t)),
        int(lerp(c0[2], c1[2], t)),
    )


def gradient_color(stops, t):
    """stops: list of (position, (r, g, b)). Returns colour at t in [0, 1]."""
    if t <= stops[0][0]:
        return stops[0][1]
    for i in range(1, len(stops)):
        p0, c0 = stops[i - 1]
        p1, c1 = stops[i]
        if t <= p1:
            u = (t - p0) / (p1 - p0) if p1 > p0 else 0.0
            return lerp_color(c0, c1, u)
    return stops[-1][1]


# ---------------------------------------------------------------------------
# Canvas: RGB bytearray + software rasterizer.
# ---------------------------------------------------------------------------
class Canvas:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.buf = bytearray(w * h * 3)

    def blend_span(self, y, x0, x1, color, alpha=1.0):
        """Blend one horizontal run of pixels (inclusive x range)."""
        if y < 0 or y >= self.h:
            return
        if x1 < x0:
            return
        x0 = max(0, x0)
        x1 = min(self.w - 1, x1)
        if x0 > x1:
            return
        r, g, b = color
        buf = self.buf
        base = (y * self.w + x0) * 3
        n = x1 - x0 + 1
        if alpha >= 1.0:
            buf[base:base + n * 3] = bytes((r, g, b)) * n
            return
        ia = 1.0 - alpha
        ra = r * alpha
        ga = g * alpha
        ba = b * alpha
        idx = base
        for _ in range(n):
            buf[idx] = int(ra + buf[idx] * ia)
            buf[idx + 1] = int(ga + buf[idx + 1] * ia)
            buf[idx + 2] = int(ba + buf[idx + 2] * ia)
            idx += 3

    def fill_rect(self, x0, y0, x1, y1, color, alpha=1.0):
        for y in range(max(0, y0), min(self.h - 1, y1) + 1):
            self.blend_span(y, x0, x1, color, alpha)

    def fill_circle(self, cx, cy, rad, color, alpha=1.0):
        if rad <= 0:
            return
        y0 = max(0, int(math.floor(cy - rad)))
        y1 = min(self.h - 1, int(math.ceil(cy + rad)))
        r2 = rad * rad
        for y in range(y0, y1 + 1):
            dy = y - cy
            dx = math.sqrt(max(0.0, r2 - dy * dy))
            self.blend_span(y, int(math.ceil(cx - dx)),
                            int(math.floor(cx + dx)), color, alpha)

    def fill_ellipse(self, cx, cy, rx, ry, color, alpha=1.0):
        if rx <= 0 or ry <= 0:
            return
        y0 = max(0, int(math.floor(cy - ry)))
        y1 = min(self.h - 1, int(math.ceil(cy + ry)))
        for y in range(y0, y1 + 1):
            dy = (y - cy) / ry
            dx = rx * math.sqrt(max(0.0, 1.0 - dy * dy))
            self.blend_span(y, int(math.ceil(cx - dx)),
                            int(math.floor(cx + dx)), color, alpha)

    def ring(self, cx, cy, rad, thick, color, alpha=1.0):
        """Thin ring outline."""
        if rad <= 0:
            return
        ro = rad + thick / 2.0
        ri = max(0.0, rad - thick / 2.0)
        y0 = max(0, int(math.floor(cy - ro)))
        y1 = min(self.h - 1, int(math.ceil(cy + ro)))
        for y in range(y0, y1 + 1):
            dy = y - cy
            do = math.sqrt(max(0.0, ro * ro - dy * dy)) if abs(dy) <= ro else 0.0
            di = math.sqrt(max(0.0, ri * ri - dy * dy)) if abs(dy) <= ri else 0.0
            self.blend_span(y, int(math.ceil(cx - do)),
                            int(math.floor(cx - di)), color, alpha)
            self.blend_span(y, int(math.ceil(cx + di)),
                            int(math.floor(cx + do)), color, alpha)

    def fill_polygon(self, pts, color, alpha=1.0):
        """Scanline even-odd polygon fill."""
        n = len(pts)
        if n < 3:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.h - 1, int(math.ceil(max(ys))))
        for y in range(y0, y1 + 1):
            yc = y + 0.5
            xs = []
            for i in range(n):
                x1, y1p = pts[i]
                x2, y2p = pts[(i + 1) % n]
                if (y1p <= yc < y2p) or (y2p <= yc < y1p):
                    t = (yc - y1p) / (y2p - y1p)
                    xs.append(x1 + t * (x2 - x1))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                self.blend_span(y, int(math.ceil(xs[k])),
                                int(math.floor(xs[k + 1])), color, alpha)

    def polyline(self, pts, width, color, alpha=1.0):
        """Thick polyline drawn as a chain of small circles."""
        rad = width / 2.0
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            dist = math.hypot(x1 - x0, y1 - y0)
            steps = max(1, int(dist / 1.5) + 1)
            for s in range(steps + 1):
                t = s / steps
                self.fill_circle(lerp(x0, x1, t), lerp(y0, y1, t),
                                 rad, color, alpha)

    def arc(self, cx, cy, rad, a0, a1, width, color, alpha=1.0):
        """Arc drawn as a chain of small circles along the angle range."""
        steps = max(2, int(abs(a1 - a0) * rad / 3.0))
        for s in range(steps + 1):
            a = a0 + (a1 - a0) * s / steps
            self.fill_circle(cx + rad * math.cos(a), cy + rad * math.sin(a),
                             width / 2.0, color, alpha)


# ---------------------------------------------------------------------------
# Curve helpers.
# ---------------------------------------------------------------------------
def bezier(p0, p1, p2, n):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
        y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def normals(pts):
    """Unit normals (and tangents) along a polyline."""
    out = []
    n = len(pts)
    for i in range(n):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        m = math.hypot(tx, ty) or 1.0
        tx, ty = tx / m, ty / m
        out.append(((tx, ty), (-ty, tx)))
    return out


def ribbon_polygon(pts, w0, w1):
    """Tapered ribbon around a polyline: returns a closed polygon."""
    ns = normals(pts)
    left = []
    right = []
    n = len(pts)
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        w = lerp(w0, w1, t) / 2.0
        (_, _), (nx, ny) = ns[i]
        x, y = pts[i]
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))
    return left + right[::-1]


# ---------------------------------------------------------------------------
# PNG writer (hand-rolled chunk framing, zlib compression).
# ---------------------------------------------------------------------------
def _chunk(typ, data):
    out = struct.pack(">I", len(data)) + typ + data
    out += struct.pack(">I", binascii.crc32(typ + data) & 0xFFFFFFFF)
    return out


def write_png(path, w, h, buf):
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)  # filter type 0 (None) for every scanline
        raw += buf[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", ihdr)
           + _chunk(b"IDAT", comp)
           + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


# ---------------------------------------------------------------------------
# Scene.
# ---------------------------------------------------------------------------
SKY_STOPS = [
    (0.00, (12, 14, 48)),     # deep indigo, top
    (0.30, (38, 32, 92)),     # violet
    (0.55, (96, 48, 112)),    # dusk purple
    (0.75, (150, 70, 105)),   # warm rose
    (0.90, (196, 88, 82)),    # ember rose, near horizon
    (0.97, (238, 128, 62)),   # horizon ember, narrow band only
    (1.00, (244, 138, 68)),
]

BODY_STOPS = [
    (0.00, (196, 56, 16)),    # tail: deep ember red
    (0.45, (238, 116, 26)),   # mid: bright orange
    (0.80, (252, 158, 40)),   # toward head: gold
    (1.00, (255, 188, 70)),   # head: bright gold
]

HORIZON_Y = 720


def body_radius(t):
    """Koi body half-width along the spine, t=0 tail -> t=1 head."""
    return 18.0 + 92.0 * math.sin(math.pi / 2.0 * (t ** 0.8))


def render():
    img = Canvas(W, H)

    # --- 1. Sky: vertical multi-stop gradient, one row fill at a time. ---
    for y in range(HORIZON_Y + 1):
        img.blend_span(y, 0, W - 1, gradient_color(SKY_STOPS, y / HORIZON_Y), 1.0)
    # Below the horizon the sky glow fades into the city's dark base.
    for y in range(HORIZON_Y + 1, H):
        img.blend_span(y, 0, W - 1, (16, 12, 34), 1.0)

    # --- 2. Stars (upper sky only), a few with a soft halo. ---
    for _ in range(120):
        x = rrange(0, W)
        y = rrange(0, 520)
        r = rrange(0.8, 2.2)
        a = rrange(0.35, 1.0)
        if r > 1.9:
            img.fill_circle(x, y, 6.0, (220, 220, 255), 0.12 * a)
        img.fill_circle(x, y, r, (235, 235, 255), a)

    # --- 3. Pale low moon, upper right, with a soft glow. ---
    mx, my = 832.0, 168.0
    img.fill_circle(mx, my, 128.0, (250, 240, 210), 0.05)
    img.fill_circle(mx, my, 82.0, (250, 240, 210), 0.09)
    img.fill_circle(mx, my, 52.0, (246, 240, 214), 1.0)
    img.fill_circle(mx - 16, my - 10, 9.0, (222, 214, 186), 0.55)
    img.fill_circle(mx + 14, my + 16, 6.0, (222, 214, 186), 0.45)
    img.fill_circle(mx + 20, my - 20, 4.5, (222, 214, 186), 0.4)

    # --- 4. A few soft dark clouds for depth (kept faint and high so
    # they never read as flat blobs against the warm horizon). ---
    for (cx, cy, rx, ry, a) in [
        (200, 140, 140, 20, 0.07), (700, 80, 170, 16, 0.06),
        (430, 120, 120, 16, 0.05), (880, 470, 150, 18, 0.06),
    ]:
        img.fill_ellipse(cx, cy, rx, ry, (24, 20, 60), a)

    # --- 5. Concentric ripple arcs in the upper sky, as if the koi swims
    # air. Only the top half of each ring is drawn, so the arcs never
    # cross the city or read as full target circles. ---
    rcx, rcy = 520.0, 330.0
    for rad, a in [(250, 0.09), (305, 0.11), (365, 0.08), (400, 0.06)]:
        img.arc(rcx, rcy, rad, math.pi + 0.15, 2 * math.pi - 0.15, 3.0,
                (190, 220, 255), a)

    # --- 6. Warm halo behind the koi, layered so it fades out softly
    # (a single flat-alpha disc would show a hard circular edge). ---
    for (cx, cy, rad, a) in [(500, 300, 330, 0.035), (500, 300, 240, 0.045),
                             (500, 300, 150, 0.055), (640, 350, 190, 0.04),
                             (640, 350, 120, 0.05), (330, 260, 150, 0.04),
                             (330, 260, 95, 0.05)]:
        img.fill_circle(cx, cy, rad, (255, 150, 60), a)

    # --- 7. Koi spine: one big diagonal arc, tail upper-left, head lower-right.
    spine = bezier((140.0, 300.0), (500.0, 90.0), (860.0, 450.0), 48)
    ns = normals(spine)
    n = len(spine)

    def spine_point(t):
        i = min(n - 1, int(t * (n - 1)))
        return spine[i]

    def spine_normal(t):
        i = min(n - 1, int(t * (n - 1)))
        return ns[i][1]

    # Tail ribbons: three flowing translucent ribbons fanning down-left
    # from the tail tip, drawn behind the body.
    tail = spine[0]
    rib1 = bezier(tail, (tail[0] - 110, tail[1] + 55),
                  (tail[0] - 165, tail[1] + 185), 24)
    rib2 = bezier(tail, (tail[0] - 60, tail[1] + 95),
                  (tail[0] - 55, tail[1] + 245), 24)
    rib3 = bezier(tail, (tail[0] - 110, tail[1] + 5),
                  (tail[0] - 125, tail[1] + 85), 24)
    img.fill_polygon(ribbon_polygon(rib3, 30, 4), (226, 104, 28), 0.55)
    img.fill_polygon(ribbon_polygon(rib1, 46, 5), (238, 118, 30), 0.62)
    img.fill_polygon(ribbon_polygon(rib2, 32, 4), (252, 158, 44), 0.55)
    img.fill_polygon(ribbon_polygon(rib1, 24, 3), (255, 196, 96), 0.35)

    # Dorsal fin: low ridge along the top of the mid-body. The spine
    # normal must point up (negative y) for the top side.
    dorsal = []
    for i in range(n):
        t = i / (n - 1)
        if 0.38 <= t <= 0.78:
            u = (t - 0.38) / 0.40
            lift = 30.0 * math.sin(math.pi * u)
            (tx, ty), (nx, ny) = ns[i]
            if ny > 0:
                nx, ny = -nx, -ny
            x, y = spine[i]
            dorsal.append((x + nx * (body_radius(t) + lift),
                           y + ny * (body_radius(t) + lift)))
    base = []
    for i in range(n):
        t = i / (n - 1)
        if 0.38 <= t <= 0.78:
            (tx, ty), (nx, ny) = ns[i]
            if ny > 0:
                nx, ny = -nx, -ny
            x, y = spine[i]
            base.append((x + nx * body_radius(t) * 0.75,
                         y + ny * body_radius(t) * 0.75))
    if dorsal and base:
        img.fill_polygon(dorsal + base[::-1], (226, 104, 28), 0.55)

    # --- 8. Body: dark expanded silhouette first (separation from sky),
    # then segment quads with a tail-to-head gold gradient. ---
    left = []
    right = []
    left6 = []
    right6 = []
    for i in range(n):
        t = i / (n - 1)
        r = body_radius(t)
        (tx, ty), (nx, ny) = ns[i]
        x, y = spine[i]
        left.append((x + nx * r, y + ny * r))
        right.append((x - nx * r, y - ny * r))
        left6.append((x + nx * (r + 6), y + ny * (r + 6)))
        right6.append((x - nx * (r + 6), y - ny * (r + 6)))
    img.fill_polygon(left6 + right6[::-1], (18, 8, 38), 0.55)
    for i in range(n - 1):
        t = (i + 0.5) / (n - 1)
        col = gradient_color(BODY_STOPS, t)
        img.fill_polygon([left[i], left[i + 1], right[i + 1], right[i]],
                         col, 1.0)
    # Rounded head cap.
    head = spine[-1]
    img.fill_circle(head[0], head[1], body_radius(1.0),
                    gradient_color(BODY_STOPS, 1.0), 1.0)

    # Belly band (pale, lower side) and dorsal shade (darker, upper side):
    # a side flag picks which normal direction the band sits on.
    def band(offset_lo, offset_hi, color, alpha, down=True):
        top = []
        bot = []
        for i in range(n):
            t = i / (n - 1)
            r = body_radius(t)
            (tx, ty), (nx, ny) = ns[i]
            if (ny < 0) == down:
                nx, ny = -nx, -ny
            x, y = spine[i]
            top.append((x + nx * r * offset_lo, y + ny * r * offset_lo))
            bot.append((x + nx * r * offset_hi, y + ny * r * offset_hi))
        img.fill_polygon(top + bot[::-1], color, alpha)

    band(0.12, 0.92, (255, 214, 150), 0.42, down=True)    # belly
    band(0.55, 0.95, (168, 58, 12), 0.28, down=False)    # dorsal shade

    # Scales: staggered rows of small darker arcs along the body.
    for ti in range(6, 47):
        t = ti / 48.0
        r = body_radius(t)
        (tx, ty), (nx, ny) = ns[ti]
        x, y = spine[ti]
        stagger = 0.18 if (ti % 2 == 0) else 0.0
        for lat in (-0.55, -0.28 + stagger * 0.3, 0.0 + stagger * 0.2,
                    0.28, 0.55):
            sx = x + nx * r * lat
            sy = y + ny * r * lat
            img.fill_circle(sx, sy, max(2.5, r * 0.10), (176, 66, 14), 0.18)

    # Pectoral fin: translucent ribbon sweeping back-down from the head.
    fin_base_t = 0.86
    fb = spine_point(fin_base_t)
    fn = spine_normal(fin_base_t)
    if fn[1] < 0:
        fn = (-fn[0], -fn[1])
    fb = (fb[0] + fn[0] * body_radius(fin_base_t) * 0.55,
          fb[1] + fn[1] * body_radius(fin_base_t) * 0.55)
    fin = bezier(fb, (fb[0] - 85, fb[1] + 55), (fb[0] - 130, fb[1] + 135), 20)
    img.fill_polygon(ribbon_polygon(fin, 52, 7), (255, 150, 60), 0.50)
    img.fill_polygon(ribbon_polygon(fin, 28, 4), (255, 198, 110), 0.35)

    # --- 9. Eye, whiskers, bubbles, light particles. ---
    (htx, hty), (hnx, hny) = ns[-1]
    # upper normal = the one pointing up (negative y)
    if hny > 0:
        hnx, hny = -hnx, -hny
    eye = (head[0] - htx * 26 + hnx * 36,
           head[1] - hty * 26 + hny * 36)
    img.ring(eye[0], eye[1], 17.5, 2.0, (40, 16, 6), 0.7)
    img.fill_circle(eye[0], eye[1], 16.5, (250, 244, 228), 1.0)
    img.fill_circle(eye[0] + 2, eye[1] + 1, 9.0, (28, 12, 4), 1.0)
    img.fill_circle(eye[0] - 3.5, eye[1] - 4.5, 4.0, (255, 255, 255), 1.0)

    nose = (head[0] + htx * body_radius(1.0) * 0.92,
            head[1] + hty * body_radius(1.0) * 0.92)
    whisk1 = bezier(nose, (nose[0] + 62, nose[1] + 48),
                    (nose[0] + 108, nose[1] + 128), 16)
    whisk2 = bezier(nose, (nose[0] + 34, nose[1] + 76),
                    (nose[0] + 48, nose[1] + 152), 16)
    img.polyline(whisk1, 3.0, (255, 202, 112), 0.85)
    img.polyline(whisk2, 3.0, (255, 202, 112), 0.85)

    # Bubbles trailing the tail and rising near the head.
    bubbles = [
        (118, 392, 12), (96, 452, 9), (70, 516, 13), (128, 470, 7),
        (52, 586, 10), (152, 540, 6), (906, 372, 11), (928, 306, 8),
        (884, 296, 6), (950, 240, 9), (60, 430, 5),
    ]
    for (bx, by, br) in bubbles:
        img.ring(bx, by, br, 2.0, (205, 232, 255), 0.75)
        img.fill_circle(bx - br * 0.3, by - br * 0.35, max(1.5, br * 0.22),
                        (255, 255, 255), 0.85)

    # Light particles drifting below and around the koi.
    for _ in range(46):
        t = rrange(0.05, 0.98)
        i = min(n - 1, int(t * (n - 1)))
        x, y = spine[i]
        (tx, ty), (nx, ny) = ns[i]
        if ny < 0:
            nx, ny = -nx, -ny
        off = body_radius(t) + rrange(18, 70)
        px = x + nx * off + rrange(-24, 24)
        py = y + ny * off + rrange(-16, 16)
        img.fill_circle(px, py, rrange(1.5, 3.8), (255, 196, 110),
                        rrange(0.45, 0.9))
    for _ in range(18):
        t = rrange(0.05, 0.95)
        i = min(n - 1, int(t * (n - 1)))
        x, y = spine[i]
        (tx, ty), (nx, ny) = ns[i]
        if ny > 0:
            nx, ny = -nx, -ny
        off = body_radius(t) + rrange(20, 60)
        img.fill_circle(x + nx * off, y + ny * off, rrange(1.5, 3.2),
                        (255, 210, 140), rrange(0.4, 0.8))

    # --- 10. Horizon ember glow, then the tiny city skyline. ---
    for y in range(682, 738):
        u = (y - 682) / 56.0
        a = 0.16 * math.sin(math.pi * u)
        img.blend_span(y, 0, W - 1, (255, 132, 52), a)

    bx = 0
    while bx < W:
        bw = int(rrange(36, 92))
        bh = int(rrange(70, 260))
        if rnd() < 0.14:
            bh = int(rrange(240, 300))
        top = H - bh
        img.fill_rect(bx, top, min(W - 1, bx + bw - 1), H - 1,
                      (13, 13, 32), 1.0)
        # Antenna with a red beacon on some towers.
        if rnd() < 0.28:
            ax = bx + bw // 2
            ah = int(rrange(18, 52))
            img.fill_rect(ax - 1, top - ah, ax + 1, top, (13, 13, 32), 1.0)
            img.fill_circle(ax, top - ah - 2, 2.5, (255, 70, 60), 1.0)
        # Lit windows.
        wy = top + 12
        while wy < H - 12:
            wx = bx + 7
            while wx < bx + bw - 8:
                if rnd() < 0.34:
                    if rnd() < 0.82:
                        col = (255, 190, 100)
                    else:
                        col = (150, 220, 255)
                    img.fill_rect(wx, wy, wx + 4, wy + 7, col, 0.92)
                wx += 13
            wy += 17
        bx += bw + int(rrange(0, 10))

    return img


def main():
    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(outdir, exist_ok=True)
    img = render()
    path = os.path.join(outdir, "entry.png")
    size = write_png(path, img.w, img.h, img.buf)
    print("wrote %s (%d bytes)" % (path, size))


if __name__ == "__main__":
    main()
