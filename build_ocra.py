"""OCR-A の中心線（skeleton-round.txt）から、線幅と角の丸みを変えられるフォントを作る。

使い方: python3 build_ocra.py skeleton-round.txt 出力ディレクトリ

軸:
  wght  線幅。400 で ANSI X3.17 の線幅 T (101/1000em)
  ROND  角の丸み。100 で元の OCR-A（丸ペンと丸い角）、0 で角だけの OCR-A

線1本を1つの輪郭（左の縁 → 終端のキャップ → 右の縁 → 始端のキャップ）にする。
頂点ごと・キャップごとの点の数をマスターによらず固定にして、そのまま補間できるようにする。
輪郭どうしの重なりは線が交わる所だけにとどめる
（重なった輪郭が多いと、macOS の CoreText がバリアブルフォントを塗り損ねて細線を描く）。
"""
import copy
import math
import sys
from pathlib import Path

from fontTools import varLib
from fontTools.agl import toUnicode
from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, InstanceDescriptor, SourceDescriptor
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib.tables import ttProgram
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphCoordinates

FAMILY = "OCRA Stroke"
UPM = 1000
ASCENT, DESCENT = 800, -200
STANDARD_WIDTH = 101  # ANSI の線幅 T
WEIGHTS = {100: 40, 400: STANDARD_WIDTH, 900: 180}  # wght → 線幅
STYLES = {100: "Thin", 300: "Light", 400: "Regular", 700: "Bold", 900: "Black"}
ARC_STEPS = 6  # 角の丸め（ベジェ1本）を何本の線分に割るか（粗いと内側の縁がガタつく）
CHAMFER_GLYPHS = {"B"}  # 角版で丸めの角を 45° ずつの2つの角に分けて面取りにする字（B が「日」にならないように）
CHAMFER_TURN = 45
MAX_CORNER_TURN = 100  # 曲線の区間がこれ以上（度）回るなら、角版で角を分ける
K_JOIN = 13  # 頂点の外側の角を表す点の数（奇数。真ん中の点が角の先端に来る）
K_CAP = 17  # キャップを表す点の数（両側の縁の点を除く。奇数で真ん中が端の正面）
RING = 12  # 点（ドット）の輪郭のオンカーブ点の数
MITER_LIMIT = 3.0  # 留め継ぎの先端がこれ以上伸びるなら面取りする
COPYRIGHT = (
    "METAFONT-84 Definition for OCR-A: (C) Copyright 1988, 1989 Richard B. Wales. "
    "MetaType1 translation by Matthew Skala (public domain). "
    "Stroke-based variable version derived from the Tsukurimashou OCR package."
)


# ---- 幾何 ----------------------------------------------------------------

def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def mul(a, k):
    return (a[0] * k, a[1] * k)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n else (0.0, 0.0)


def normal(d):
    """進行方向 d の左側の法線。"""
    return (-d[1], d[0])


def bezier(p0, p1, p2, p3, t):
    s = 1 - t
    return tuple(s**3 * p0[i] + 3 * s * s * t * p1[i] + 3 * s * t * t * p2[i] + t**3 * p3[i] for i in range(2))


def is_straight(seg):
    p0, p1, p2, p3 = seg
    chord = sub(p3, p0)
    length = math.hypot(*chord)
    if length == 0:
        return True
    return all(abs(cross(chord, sub(q, p0))) / length < 0.01 for q in (p1, p2))


def line_intersection(p, d, q, e):
    den = cross(d, e)
    if abs(den) < 1e-9:
        return None
    t = cross(sub(q, p), e) / den
    return add(p, mul(d, t))


def area(points):
    return sum(cross(points[i], points[(i + 1) % len(points)]) for i in range(len(points))) / 2


def distance_to_segment(p, a, b):
    ab = sub(b, a)
    length2 = dot(ab, ab)
    t = 0 if length2 == 0 else max(0, min(1, dot(sub(p, a), ab) / length2))
    return math.hypot(*sub(p, add(a, mul(ab, t))))


def clip_to_pen_box(poly):
    """丸ペンの円が収まる正方形（|x|, |y| ≤ 1）で多角形を切る（点の順序を保つ）。

    斜めの線の角キャップや鋭角の留め継ぎが、元の OCR-A の字の範囲
    （ベースライン・キャップハイトなど）からはみ出さないようにする。
    """
    for axis in (0, 1):
        for sign in (1, -1):
            out = []
            for i in range(len(poly)):
                a, b = poly[i - 1], poly[i]
                ina, inb = sign * a[axis] <= 1 + 1e-9, sign * b[axis] <= 1 + 1e-9
                if ina != inb:
                    t = (sign - a[axis]) / (b[axis] - a[axis])
                    out.append(add(a, mul(sub(b, a), t)))
                if inb:
                    out.append(b)
            poly = out
    return poly


def chain_in_box(chain):
    """原点を囲む凸な形の外周の一部 chain を、ペンの正方形で切った鎖にする。"""
    poly = clip_to_pen_box([(0.0, 0.0)] + chain)
    i = poly.index((0.0, 0.0))
    chain = poly[i + 1:] + poly[:i]
    # 切った所に出る重複点や、一直線上の余分な点を除く
    # （残ると角の先端が複数あるように扱われ、中間の丸みで段ができる）
    out = [chain[0]]
    for q in chain[1:]:
        if math.hypot(*sub(q, out[-1])) > 1e-6:
            out.append(q)
    k = 1
    while k < len(out) - 1:
        if abs(cross(sub(out[k], out[k - 1]), sub(out[k + 1], out[k]))) < 1e-9:
            del out[k]
        else:
            k += 1
    return out


def resample(chain, m):
    """鎖を、両端と途中の頂点を保ったまま m 点にする（長い辺の中点を足していく）。"""
    pts = [chain[0]] + [p for i, p in enumerate(chain[1:], 1) if math.hypot(*sub(p, chain[i - 1])) > 1e-9]
    if len(pts) > m:
        raise ValueError("鎖の頂点が多すぎる")
    while len(pts) < m:
        if len(pts) == 1:
            pts.append(pts[0])
            continue
        i = max(range(len(pts) - 1), key=lambda k: math.hypot(*sub(pts[k + 1], pts[k])))
        pts.insert(i + 1, mul(add(pts[i], pts[i + 1]), .5))
    return pts


def collapse_to_corner(chain, fractions):
    """丸め版の円弧上の各点（円弧の中での割合 0〜1）を、角版の角の頂点に集める。

    chain は角版の外側の縁（両端は前後の縁の上の点）。その途中の頂点
    （ふつうは留め継ぎの先端1点、切り落としがあれば2点）へ、割合に応じて割り振る。
    丸め版とこの形を補間すると、途中はどこでも「半径が丸めの割合に比例した円弧」になる。
    """
    corners = chain[1:-1] or [mul(add(chain[0], chain[-1]), .5)]
    m = len(corners)
    out = []
    for f in fractions:
        x = f * m
        k = round(x)
        if 0 < k < m and abs(x - k) < 1e-9:
            out.append(mul(add(corners[k - 1], corners[k]), .5))  # ちょうど境目なら中点
        else:
            out.append(corners[min(int(x), m - 1)])
    return out


def arc_chain(v0, turn, m):
    """単位ベクトル v0 から turn（ラジアン）だけ回る円弧上の m 点（両端を含む）。"""
    a0 = math.atan2(v0[1], v0[0])
    return [(math.cos(a0 + turn * k / (m - 1)), math.sin(a0 + turn * k / (m - 1))) for k in range(m)]


# ---- 中心線の読み込み ----------------------------------------------------

def read_skeleton(path):
    glyphs = []
    lines = iter(Path(path).read_text().splitlines())
    for line in lines:
        f = line.split()
        if f[0] == "G":
            g = {"name": f[1], "code": int(f[2]), "advance": float(f[3]),
                 "strokes": [], "fills": [], "corners": [], "dots": []}
            glyphs.append(g)
        elif f[0] in ("S", "F"):
            cyc, n = f[1] == "1", int(f[2])
            segs = []
            for _ in range(n):
                v = [float(x) for x in next(lines).split()]
                segs.append(tuple((v[i], v[i + 1]) for i in range(0, 8, 2)))
            (g["strokes"] if f[0] == "S" else g["fills"]).append((segs, cyc))
        elif f[0] == "C":
            g["corners"].append((float(f[1]), float(f[2])))
        elif f[0] == "D":
            g["dots"].append((float(f[1]), float(f[2])))
    return glyphs


# ---- 中心線を折れ線にし、角版での位置を求める ---------------------------

def split_bezier(seg):
    p0, p1, p2, p3 = seg
    m01, m12, m23 = mul(add(p0, p1), .5), mul(add(p1, p2), .5), mul(add(p2, p3), .5)
    a, b = mul(add(m01, m12), .5), mul(add(m12, m23), .5)
    m = mul(add(a, b), .5)
    return (p0, m01, a, m), (m, b, m23, p3)


def tangent_in(seg):
    p0, p1, p2, p3 = seg
    return unit(sub(p1, p0) if p1 != p0 else sub(p2, p0))


def tangent_out(seg):
    p0, p1, p2, p3 = seg
    return unit(sub(p3, p2) if p3 != p2 else sub(p3, p1))


def run_turn(run):
    """曲線の区間で接線が回る角度（度、符号付き）。"""
    total, prev = 0.0, math.atan2(*tangent_in(run[0])[::-1])
    for seg in run:
        a = math.atan2(*tangent_out(seg)[::-1])
        total += (a - prev + math.pi) % (2 * math.pi) - math.pi
        prev = a
    return math.degrees(total)


def corners_for_run(run, line_in=None, line_out=None, max_turn=MAX_CORNER_TURN):
    """曲線の区間 run を角版での角の列に置き換える。

    区間を折れ線にした各点について (角版での位置, 区間の情報) を返す。
    max_turn を超えて回る区間は、回転を等分する向きの線で区切って複数の角にする
    （O の上辺なら水平な線。max_turn を小さくすると面取りになる）。
    区間の情報は (始点, 始点の接線, 終点, 終点の接線, 半径の目安)。
    line_in, line_out は区間の前後の直線の向き。あればそれを延ばして角を求める
    （曲線の接線は直線の向きとわずかにずれ、角版で直線が傾いてしまう）。
    """
    start, end = run[0][0], run[-1][3]
    d_in = line_in if line_in is not None else tangent_in(run[0])
    d_out = line_out if line_out is not None else tangent_out(run[-1])
    a0 = math.atan2(d_in[1], d_in[0])
    total = (math.atan2(d_out[1], d_out[0]) - a0 + math.pi) % (2 * math.pi) - math.pi
    total = math.radians(run_turn(run)) if abs(run_turn(run)) > 179 else total
    pieces = max(1, math.ceil(abs(math.degrees(total)) / max_turn - 1e-9))
    # 折れ線の各点と、その点での接線の向き（始点からの回転の割合）
    pts = [start]
    fracs = [0.0]
    for seg in run:
        for t in range(1, ARC_STEPS + 1):
            q = bezier(*seg, t / ARC_STEPS)
            tan = sub(bezier(*seg, min(1, t / ARC_STEPS + 1e-4)), bezier(*seg, max(0, t / ARC_STEPS - 1e-4)))
            a = math.atan2(tan[1], tan[0])
            diff = (a - a0 + math.pi) % (2 * math.pi) - math.pi
            if abs(total) > math.pi * 0.99 and diff * total < 0:
                diff += math.copysign(2 * math.pi, total)
            pts.append(q)
            fracs.append(min(1.0, max(0.0, diff / total)) if total else 1.0)
    fracs[-1] = 1.0
    # 区切りの線: 接線がちょうど k/pieces だけ回った点を通り、その向きの線
    lines = [(start, d_in)]
    for k in range(1, pieces):
        f = k / pieces
        j = next(j for j in range(1, len(pts)) if fracs[j] >= f)
        t = (f - fracs[j - 1]) / (fracs[j] - fracs[j - 1]) if fracs[j] > fracs[j - 1] else 0
        point = add(pts[j - 1], mul(sub(pts[j], pts[j - 1]), t))
        a = a0 + total * f
        lines.append((point, (math.cos(a), math.sin(a))))
    lines.append((end, d_out))
    corners = []
    for k in range(pieces):
        (p, d), (q, e) = lines[k], lines[k + 1]
        corners.append(line_intersection(p, d, q, e) if abs(cross(d, e)) > math.sin(math.radians(5)) else None)
    # 区切りごとの情報（半径の目安は弧の長さ ÷ 回転角）
    piece_of = [min(pieces - 1, int(f * pieces)) for f in fracs]
    infos = []
    for k in range(pieces):
        idx = [j for j in range(1, len(pts)) if piece_of[j] == k]
        length = sum(math.hypot(*sub(pts[j], pts[j - 1])) for j in idx)
        turn = abs(total) / pieces
        infos.append((lines[k][0], lines[k][1], lines[k + 1][0], lines[k + 1][1],
                      length / turn if turn > 1e-6 else math.inf))
    result = []
    for j in range(1, len(pts)):
        k = piece_of[j]
        result.append((corners[k] if corners[k] else pts[j], infos[k]))
    return result


def polyline(segs, cyc, chamfer=False):
    """中心線を折れ線にする。

    (丸め版の位置の列, 角版の位置の列, 各頂点の曲線区間の情報) を返す。
    曲線（join による角の丸め）が続く区間は、前後の直線を延ばした交点を
    角版での位置とする。直線上の頂点の区間の情報は None。
    """
    segs = [s for s in segs if math.hypot(*sub(s[3], s[0])) > 0.5]
    straight = [is_straight(s) for s in segs]
    if cyc and not all(straight) and any(straight):
        # 閉じた線は直線から始まるように回す
        k = next(k for k in range(len(segs)) if straight[k] and not straight[k - 1])
        segs, straight = segs[k:] + segs[:k], straight[k:] + straight[:k]
    pts_round, pts_sharp, infos = [segs[0][0]], [segs[0][0]], [None]
    i = 0
    while i < len(segs):
        if straight[i]:
            pts_round.append(segs[i][3])
            pts_sharp.append(segs[i][3])
            infos.append(None)
            i += 1
            continue
        j = i
        while j < len(segs) and not straight[j]:
            j += 1
        run = segs[i:j]
        max_turn = CHAMFER_TURN if chamfer else MAX_CORNER_TURN
        flat = [bezier(*s, t / ARC_STEPS) for s in run for t in range(1, ARC_STEPS + 1)]
        line_in = unit(sub(segs[i - 1][3], segs[i - 1][0])) if (i > 0 or cyc) and straight[i - 1] else None
        line_out = unit(sub(segs[j % len(segs)][3], segs[j % len(segs)][0])) \
            if (j < len(segs) or cyc) and straight[j % len(segs)] else None
        targets = corners_for_run(run, line_in, line_out, max_turn)
        if i > 0 or cyc:
            pts_sharp[-1] = targets[0][0]  # 曲線の始点も最初の角へ（線の端点なら動かさない）
            infos[-1] = targets[0][1]
        if j == len(segs) and not cyc:
            targets[-1] = (flat[-1], targets[-1][1])  # 線の端点は動かさない
        pts_round.extend(flat)
        pts_sharp.extend(t for t, _ in targets)
        infos.extend(info for _, info in targets)
        i = j
    if cyc:
        pts_round.pop()
        pts_sharp.pop()
        infos.pop()
    return pts_round, pts_sharp, infos


# ---- 線の輪郭 ------------------------------------------------------------

def trim_cap(side_pt, cap_pts, other_pt, line):
    """線幅に飲み込まれた短い線の端のキャップを、隣の線の縁 line で切る。

    端の縁の点 side_pt は隣の線の中（line の内側）にある。キャップを side_pt から
    たどって最初に line を越える所を求め、それより手前の点をその交点にそろえる。
    丸ペンで描いたときと同じく、隣の線の縁がキャップの円とぶつかる所で角になる。
    """
    origin, d = line
    chain = [side_pt] + list(cap_pts) + [other_pt]
    side = lambda q: cross(d, sub(q, origin))
    s0 = side(side_pt)
    if abs(s0) < 1e-9:
        return cap_pts
    for k in range(1, len(chain)):
        if side(chain[k]) * s0 <= 0:
            a, b = chain[k - 1], chain[k]
            x = line_intersection(a, sub(b, a), origin, d) or b
            return [x if m < k - 1 else q for m, q in enumerate(cap_pts)]
    return cap_pts


class Stroke:
    def __init__(self, segs, cyc, chamfer=False):
        self.round, self.sharp, self.infos = polyline(segs, cyc, chamfer)
        self.cyc = cyc
        self.n = len(self.round)
        self.sharp_points = set()  # 丸め版でも角にする頂点
        self.butt = set()  # 角版でキャップを延ばさない端点

    def directions(self, i, sharp):
        """頂点 i の前後の進行方向。角版では同じ位置に集めた頂点の外側の向きを使う。"""
        pts, n = (self.sharp if sharp else self.round), self.n

        def neighbor(step):
            k = i
            for _ in range(n):
                k += step
                if not self.cyc and not 0 <= k < n:
                    return None
                if pts[k % n] != pts[i]:
                    return pts[k % n]
            return None

        prev, nxt = neighbor(-1), neighbor(1)
        if sharp and (prev is None and nxt is None):
            return self.directions(i, False)
        d_in = unit(sub(pts[i], prev)) if prev is not None else None
        d_out = unit(sub(nxt, pts[i])) if nxt is not None else None
        return d_in or d_out, d_out or d_in

    def sharp_chain(self, d_in, d_out, s):
        """角版の外側の角（単位ベクトルで、法線から法線まで）。ペンの正方形で切ってある。"""
        n_in, n_out = normal(d_in), normal(d_out)
        c = dot(d_in, d_out)
        miter = mul(add(n_in, n_out), 1 / (1 + c)) if c > -0.999 else n_in
        if math.hypot(*miter) <= MITER_LIMIT:
            chain = [mul(n_in, s), mul(miter, s), mul(n_out, s)]
        else:
            k = math.sqrt(MITER_LIMIT**2 - 1)
            chain = [mul(n_in, s), add(mul(n_in, s), mul(d_in, k)), add(mul(n_out, s), mul(d_out, -k)), mul(n_out, s)]
        return chain_in_box(chain)

    def side_points(self, i, h, sharp_mode):
        """頂点 i での (左の縁の点の列, 右の縁の点の列, 内側の縁)。

        点の列はどちらも K_JOIN 点。内側の縁は "L" か "R"（直進なら None）。
        """
        v = (self.sharp if sharp_mode else self.round)[i]
        d_in, d_out = self.directions(i, sharp_mode)
        n_in, n_out = normal(d_in), normal(d_out)
        c = dot(d_in, d_out)
        miter = mul(add(n_in, n_out), 1 / (1 + c)) if c > -0.999 else n_in
        if c > math.cos(math.radians(1)):
            return [add(v, mul(miter, h))] * K_JOIN, [add(v, mul(miter, -h))] * K_JOIN, None
        turn = math.atan2(cross(d_in, d_out), c)
        s = -1 if turn > 0 else 1  # 外側の縁（左に曲がるなら右側）
        if sharp_mode or self.round[i] in self.sharp_points:
            outer = collapse_to_corner(self.sharp_chain(d_in, d_out, s), [k / (K_JOIN - 1) for k in range(K_JOIN)])
        else:
            outer = arc_chain(mul(n_in, s), turn, K_JOIN)
        outer = [add(v, mul(p, h)) for p in outer]
        inner_pt = add(v, mul(miter, -s * h))
        info = self.infos[i]
        if not sharp_mode and info is not None and h > info[4] * 0.98:
            # 線幅が丸めの半径を超えると内側の縁がねじれるので、前後の直線の内側の縁の交点に集める
            start, di, end, do, _ = info
            p = line_intersection(add(start, mul(normal(di), -s * h)), di, add(end, mul(normal(do), -s * h)), do)
            if p is not None:
                inner_pt = p
        inner = [inner_pt] * K_JOIN
        return ((inner, outer, "L") if s < 0 else (outer, inner, "R"))

    def groups(self):
        """角版で同じ位置に集めた、連続する頂点のまとまり（2つ以上のもの）。"""
        pts, n = self.sharp, self.n
        seen, result = set(), []
        for i in range(n):
            if i in seen:
                continue
            g = [i]
            k = i
            while (self.cyc or k + 1 < n) and pts[(k + 1) % n] == pts[i] and (k + 1) % n != i:
                k += 1
                g.append(k % n)
            if self.cyc:
                k = i
                while pts[(k - 1) % n] == pts[i] and (k - 1) % n not in g:
                    k -= 1
                    g.insert(0, k % n)
            seen.update(g)
            if len(g) > 1:
                result.append(g)
        return result

    def map_group(self, g, h, sides):
        """角版で1か所に集めた頂点のまとまりの外側の縁を、丸め版の円弧の向きに合わせて並べ直す。

        丸め版の円弧上の各点を、その向きの割合に応じて角版の角の頂点に集める
        （collapse_to_corner）。中間の丸みでは、角の丸みの半径だけが小さくなっていく。
        """
        v = self.sharp[g[0]]
        d_in, d_out = self.directions(g[0], True)
        c = dot(d_in, d_out)
        if c > math.cos(math.radians(1)):
            return
        total = math.atan2(cross(d_in, d_out), c)
        s = -1 if total > 0 else 1
        chain = [add(v, mul(p, h)) for p in self.sharp_chain(d_in, d_out, s)]
        base = math.atan2(*mul(normal(d_in), s)[::-1])
        # 丸め版の各点の向き → 円弧の中での割合
        fractions = []
        for i in g:
            ri, ro = self.directions(i, False)
            rc = dot(ri, ro)
            rturn = 0.0 if rc > math.cos(math.radians(1)) else math.atan2(cross(ri, ro), rc)
            a0 = math.atan2(*mul(normal(ri), s)[::-1])
            for k in range(K_JOIN):
                a = a0 + rturn * k / (K_JOIN - 1)
                diff = (a - base + math.pi) % (2 * math.pi) - math.pi
                fractions.append(min(1.0, max(0.0, diff / total)))
        pts = collapse_to_corner(chain, fractions)
        side = 0 if s > 0 else 1  # 外側が左なら左の列、右なら右の列を置き換える
        for idx, i in enumerate(g):
            l, r, inner = sides[i]
            seg = pts[idx * K_JOIN:(idx + 1) * K_JOIN]
            sides[i] = (seg, r, "R") if side == 0 else (l, seg, "L")

    def edge(self, j, h, sign, sharp_mode):
        """線分 j（頂点 j → j+1）の、左 (sign=1) か右 (sign=-1) の縁の直線 (点, 向き)。"""
        pts = self.sharp if sharp_mode else self.round
        a, b = pts[j], pts[(j + 1) % self.n]
        d = unit(sub(b, a))
        return add(a, mul(normal(d), sign * h)), d

    def clean_side(self, lists, inner, side_name, sign, h, sharp_mode):
        """縁の点の列から、線幅に飲み込まれた短い線分による折り返しを取り除く。

        内側の縁で前後の点が逆向きに並んだら、その線分は飲み込まれている。
        そのときは、飲み込まれた線分を飛ばして、前後の線分の縁どうしの交点に寄せる。
        縁の延長線上に点を置くので、縦線や横線の縁は斜めにならない。
        """
        pts, n = (self.sharp if sharp_mode else self.round), self.n
        segs = list(range(n)) if self.cyc else list(range(n - 1))
        live = [j for j in segs if pts[j] != pts[(j + 1) % n]]
        swallowed = set()
        endpoints = set() if self.cyc else {0, n - 1}

        def adjustable(i):
            return i in endpoints or inner[i] in (side_name, None)

        # 先に内側どうしをまとめ、そのあとで端点や外側の点を合わせる
        # （先に端点を動かすと、鋭角で遠くへ飛んだ内側の点に端点が引っぱられる）
        moved = {}
        for phase in (1, 2):
            self._clean_pass(lists, inner, adjustable, endpoints, live, swallowed, segs, pts, n, h, sign, sharp_mode,
                             phase, moved)
        return lists, moved

    def _clean_pass(self, lists, inner, adjustable, endpoints, live, swallowed, segs, pts, n, h, sign, sharp_mode,
                    phase, moved):
        for _ in range(4 * n):
            changed = False
            for j in live:
                if j in swallowed:
                    continue
                i0, i1 = j, (j + 1) % n
                a, b = lists[i0][-1], lists[i1][0]
                d = unit(sub(pts[i1], pts[i0]))
                if dot(sub(b, a), d) >= -0.5:
                    continue
                both_inner = adjustable(i0) and adjustable(i1) and i0 not in endpoints and i1 not in endpoints
                if both_inner != (phase == 1):
                    continue
                changed = True
                if both_inner:
                    swallowed.add(j)
                    prev = next((k for k in reversed(live) if k < j and k not in swallowed), None) if not self.cyc else \
                        next((live[(live.index(j) - m) % len(live)] for m in range(1, len(live))
                              if live[(live.index(j) - m) % len(live)] not in swallowed), None)
                    nxt = next((k for k in live if k > j and k not in swallowed), None) if not self.cyc else \
                        next((live[(live.index(j) + m) % len(live)] for m in range(1, len(live))
                              if live[(live.index(j) + m) % len(live)] not in swallowed), None)
                    # 飲み込まれた線分でつながる頂点を、すべて交点に寄せる
                    cluster = {i0, i1}
                    for k in segs:
                        if k in swallowed or pts[k] == pts[(k + 1) % n]:
                            if k in cluster or (k + 1) % n in cluster:
                                cluster.update((k, (k + 1) % n))
                    center = mul(tuple(map(sum, zip(*(pts[i] for i in cluster)))), 1 / len(cluster))
                    p = None
                    if prev is not None and nxt is not None:
                        e_prev, e_next = self.edge(prev, h, sign, sharp_mode), self.edge(nxt, h, sign, sharp_mode)
                        p = line_intersection(*e_prev, *e_next)
                        if p is None or math.hypot(*sub(p, center)) > 4 * h:
                            # 前後の縁がほぼ平行（? の折り返しなど）。折り返しの中心から腕の内側へ寄せる
                            inward = unit(sub(e_next[1], e_prev[1]))
                            p = add(center, mul(inward, h))
                    if p is None:
                        p = mul(add(a, b), .5)
                    for i in cluster:
                        if i not in endpoints and adjustable(i):
                            lists[i] = [p] * K_JOIN
                elif i0 in endpoints or (adjustable(i0) and not adjustable(i1)):
                    lists[i0] = [b] * K_JOIN
                    if i0 in endpoints:
                        # 端点を寄せた先の点が乗る縁（飲み込まれていない次の線分の縁）を覚えておく
                        k = next((k for k in live if k > j and k not in swallowed), None)
                        if k is not None:
                            moved[i0] = self.edge(k, h, sign, sharp_mode)
                else:
                    lists[i1] = [a] * K_JOIN
                    if i1 in endpoints:
                        k = next((k for k in reversed(live) if k < j and k not in swallowed), None)
                        if k is not None:
                            moved[i1] = self.edge(k, h, sign, sharp_mode)
            if not changed:
                break

    def cap(self, i, h, sharp_mode):
        """端点 i のキャップ。左の縁から右の縁へ、進行方向の外側を回る K_CAP 点（両端は含まない）。"""
        v = (self.sharp if sharp_mode else self.round)[i]
        d_in, d_out = self.directions(i, sharp_mode)
        d = d_in if i == self.n - 1 else mul(d_out, -1)  # 外向き
        n = normal(d)
        flagged = self.round[i] in self.sharp_points
        if sharp_mode and i in self.butt:
            e = mul(d, 0.05)
            chain = self.square_cap([n, add(n, e), add(mul(n, -1), e), mul(n, -1)])
        elif sharp_mode or flagged:
            # 線に直角な長方形の端。斜めの線では延ばす長さを縮め、線の主な向き
            # （横に近い線なら横、縦に近い線なら縦）で、長方形の角が丸い端と同じ所
            # （端点から線幅/2）まで届くようにする。
            # そうしないと斜めの端が張り出し、ROND を下げると字が左右にずれる
            a = 0 if abs(d[0]) >= abs(d[1]) else 1
            reach = min(1.0, (1 - abs(n[a])) / abs(d[a]))
            e = mul(d, reach)
            chain = self.square_cap([n, add(n, e), add(mul(n, -1), e), mul(n, -1)])
        else:
            chain = arc_chain(n, -math.pi, K_CAP + 2)
        return [add(v, mul(p, h)) for p in chain[1:-1]]

    @staticmethod
    def square_cap(rect):
        """四角い端の点。丸い端（半円）の点と向きで対応させる。

        半円の 90° より手前の点は手前の角へ、先の点は先の角へ、ちょうど 90° の点は
        前の辺の中点へ集める。補間の途中は角丸の長方形になり、線の軸に対して左右対称になる。
        """
        n_pt, c1, c2, m_pt = rect
        pts = [n_pt]
        for k in range(1, K_CAP + 1):
            angle = 180 * k / (K_CAP + 1)
            pts.append(c1 if angle < 90 - 1e-9 else c2 if angle > 90 + 1e-9 else mul(add(c1, c2), .5))
        return pts + [m_pt]

    def contours(self, h, sharp_mode):
        pts, n = (self.sharp if sharp_mode else self.round), self.n
        sides = [self.side_points(i, h, sharp_mode) for i in range(n)]
        if not self.cyc:
            # 端点の左右の縁は、端の線分の法線の位置
            for i, d in ((0, self.directions(0, sharp_mode)[1]), (n - 1, self.directions(n - 1, sharp_mode)[0])):
                sides[i] = ([add(pts[i], mul(normal(d), h))] * K_JOIN, [add(pts[i], mul(normal(d), -h))] * K_JOIN, None)
        if sharp_mode:
            for g in self.groups():
                self.map_group(g, h, sides)
        inner = [x[2] for x in sides]
        orig_ends = {i: (sides[i][0][0], sides[i][1][0]) for i in (0, n - 1)} if not self.cyc else {}
        left, moved_l = self.clean_side([x[0] for x in sides], inner, "L", 1, h, sharp_mode)
        right, moved_r = self.clean_side([x[1] for x in sides], inner, "R", -1, h, sharp_mode)
        if self.cyc:
            return [[p for l in left for p in l], [p for r in right for p in r][::-1]]
        cap_end = self.cap(n - 1, h, sharp_mode)  # 左の縁 → 右の縁
        cap_start = self.cap(0, h, sharp_mode)  # 右の縁 → 左の縁
        l0, r0 = orig_ends[0]
        le, re = orig_ends[n - 1]
        if 0 in moved_r:
            cap_start = trim_cap(r0, cap_start, l0, moved_r[0])
        if 0 in moved_l:
            cap_start = trim_cap(l0, cap_start[::-1], r0, moved_l[0])[::-1]
        if n - 1 in moved_l:
            cap_end = trim_cap(le, cap_end, re, moved_l[n - 1])
        if n - 1 in moved_r:
            cap_end = trim_cap(re, cap_end[::-1], le, moved_r[n - 1])[::-1]
        out = [left[0][0]]
        for i in range(1, n - 1):
            out += left[i]
        out.append(left[n - 1][0])
        out += cap_end
        out.append(right[n - 1][0])
        for i in range(n - 2, 0, -1):
            out += right[i][::-1]
        out.append(right[0][0])
        out += cap_start
        return [out]


# ---- 点（ドット）・塗り・補いの角 ----------------------------------------

def convex_hull(points):
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in points))
    if len(pts) < 3:
        return pts
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(sub(lower[-1], lower[-2]), sub(p, lower[-2])) <= 1e-9:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(sub(upper[-1], upper[-2]), sub(p, upper[-2])) <= 1e-9:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]  # 反時計回り


def ray_polygon(poly, angle):
    """原点から angle 方向へ伸ばした半直線と凸多角形の交点。"""
    d = (math.cos(angle), math.sin(angle))
    best = None
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        e = sub(b, a)
        den = cross(d, e)
        if abs(den) < 1e-12:
            continue
        t = cross(a, e) / den
        u = cross(a, d) / den
        if t > 0 and -1e-9 <= u <= 1 + 1e-9 and (best is None or t < best):
            best = t
    return mul(d, best)


SQUARE = [(1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0), (1.0, -1.0)]


def dot_ring(center, h, sharp_mode):
    """点（ドット）の輪郭。丸め版は円、角版は正方形。オン・オフ交互に 2*RING 点。"""
    angles = [2 * math.pi * k / RING + math.pi / 4 for k in range(RING)]
    pts = []
    for i, a in enumerate(angles):
        b = angles[(i + 1) % RING]
        gap = 2 * math.pi / RING
        if sharp_mode:
            on = ray_polygon(SQUARE, a)
            off = mul(add(on, ray_polygon(SQUARE, b)), .5)
        else:
            on = (math.cos(a), math.sin(a))
            r = 1 / math.cos(gap / 2)
            off = (r * math.cos(a + gap / 2), r * math.sin(a + gap / 2))
        pts += [(add(center, mul(on, h)), True), (add(center, mul(off, h)), False)]
    return pts


# ---- 太いときの字形の調整 -----------------------------------------------

# 太いと内側の空間が潰れる字の、中心線の調整。
# 字ごとに「範囲 (x0, y0, x1, y1) にある中心線の点を (dx, dy) 動かす」を並べる。
# 量は最も太いマスター（wght 900）でのもの。標準の太さ以下では動かさず、その間は比例させる。
HEAVY_ADJUST = {
    # 上下の横線を外側へ（上下の短い縦棒は、内側の端だけが横線と一緒に動く）
    "dollar": [((150, 500, 575, 600), (0, 55)), ((150, 180, 575, 260), (0, -55))],
    # 上の部分の下端を上げて、点との間を空ける
    "question": [((200, 150, 320, 260), (0, 56))],
    # 縦線を外側へ、横線を上下へ広げて、中の四角を残す
    "numbersign": [((250, 0, 275, 800), (-20, 0)), ((450, 0, 475, 800), (20, 0)),
                   ((150, 340, 575, 356), (0, -15)), ((150, 552, 575, 568), (0, 15))],
    # 二重線の内側の線を字の中央寄りへずらして、二重線の間を空ける
    "bracketleft": [((300, 0, 325, 800), (76, 0))],
    "bracketright": [((400, 0, 425, 800), (-76, 0))],
    # 上の輪の上半分を上へ広げて、輪の中の長方形の穴を残す
    # （穴がつぶれると内側の縁が裏返り、途中の太さで斜めに歪む）
    "Aring": [((200, 640, 525, 760), (0, 55))],
}


def heavy_amount(width):
    """標準の太さで 0、最も太いマスターで 1。"""
    heavy = WEIGHTS[max(WEIGHTS)]
    return max(0.0, (width - STANDARD_WIDTH) / (heavy - STANDARD_WIDTH))


def adjusted(prep, name, width):
    """太さに合わせて中心線を動かした字形（調整がなければそのまま）。"""
    rules = HEAVY_ADJUST.get(name)
    k = heavy_amount(width)
    if not rules or k == 0:
        return prep
    ensure_orient(prep)

    def move(p):
        for (x0, y0, x1, y1), (dx, dy) in rules:
            if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
                return (p[0] + dx * k, p[1] + dy * k)
        return p

    new = copy.deepcopy(prep)
    for st in new["strokes"]:
        # 角版の点も、同じ番号の丸め版の点が範囲に入るかで動かす（両方を同じだけ動かす）
        deltas = [sub(move(p), p) for p in st.round]
        st.round = [add(p, d) for p, d in zip(st.round, deltas)]
        st.sharp = [add(p, d) for p, d in zip(st.sharp, deltas)]
        st.infos = [None if x is None else (move(x[0]), x[1], move(x[2]), x[3], x[4]) for x in st.infos]
        st.sharp_points = {move(p) for p in st.sharp_points}
    new["dots"] = [move(p) for p in new["dots"]]
    new["fills"] = [[move(p) for p in poly] for poly in new["fills"]]
    new["extra_corners"] = [move(p) for p in new["extra_corners"]]
    return new


# ---- 字形 ------------------------------------------------------------------

MAX_MERGE_TURN = 135  # 端どうしが出会う2本の線を、曲がりがこれ以内（度）ならつなぐ


def reverse_segs(segs):
    return [(q3, q2, q1, q0) for q0, q1, q2, q3 in reversed(segs)]


def merge_strokes(strokes):
    """端どうしが同じ点で出会う2本の線を1本につなぐ。

    元の定義では別々の線でも、丸ペンなら丸いキャップが重なって自然につながる。
    角版では四角いキャップがそれぞれ突き出すので、1本の線にして角（留め継ぎ）にする。
    その点に3本以上の端が集まる所と、折り返し（曲がりが大きい所）はつながない。
    """
    strokes = [([q for q in segs if math.hypot(*sub(q[3], q[0])) > 0.5], cyc) for segs, cyc in strokes]
    strokes = [x for x in strokes if x[0]]

    def ends():
        result = []
        for k, (segs, cyc) in enumerate(strokes):
            if not cyc:
                result += [(k, 0, segs[0][0]), (k, 1, segs[-1][3])]
        return result

    def overlap_merge():
        """同じ直線上で同じ向きに重なる2本の線（from_mid / to_mid）を、重なりを除いてつなぐ。

        重なった所では2つの輪郭の縁が同じ線上に来て、縁に筋が出る。
        """
        for ka in range(len(strokes)):
            for kb in range(len(strokes)):
                if strokes[ka][1] or strokes[kb][1]:
                    continue
                for ra in (False, True):
                    for rb in ((ra,) if ka == kb else (False, True)):
                        a = reverse_segs(strokes[ka][0]) if ra else strokes[ka][0]
                        b = reverse_segs(strokes[kb][0]) if rb else strokes[kb][0]
                        la, fb = a[-1], b[0]
                        if not (is_straight(la) and is_straight(fb)):
                            continue
                        da, db = unit(sub(la[3], la[0])), unit(sub(fb[3], fb[0]))
                        if dot(da, db) < math.cos(math.radians(2)):
                            continue
                        # b の始点が a の最後の線分の上に、a の終点が b の最初の線分の上にある
                        if distance_to_segment(fb[0], la[0], la[3]) > 0.5 or distance_to_segment(la[3], fb[0], fb[3]) > 0.5:
                            continue
                        if dot(sub(fb[0], la[0]), da) <= 0.5:
                            continue
                        bridge = (la[0], la[0], fb[0], fb[0])
                        if ka == kb:
                            # 自分の始まりに重なって終わる線は、重なりを除いて閉じる
                            strokes[ka] = (a[:-1] + [bridge], True)
                        else:
                            strokes[ka] = (a[:-1] + [bridge] + b, False)
                            del strokes[kb]
                        return True
        return False

    while overlap_merge():
        pass

    changed = True
    while changed:
        changed = False
        e = ends()
        for a in range(len(e)):
            for b in range(a + 1, len(e)):
                ka, wa, pa = e[a]
                kb, wb, pb = e[b]
                if math.hypot(*sub(pa, pb)) > 0.5:
                    continue
                if sum(1 for x in e if math.hypot(*sub(x[2], pa)) <= 0.5) != 2:
                    continue
                segs_a = strokes[ka][0] if wa == 1 else reverse_segs(strokes[ka][0])  # 点へ向かう
                if ka == kb:
                    arrive, leave = tangent_out(segs_a[-1]), tangent_in(segs_a[0])
                else:
                    segs_b = strokes[kb][0] if wb == 0 else reverse_segs(strokes[kb][0])  # 点から出る
                    arrive, leave = tangent_out(segs_a[-1]), tangent_in(segs_b[0])
                if math.degrees(math.acos(max(-1, min(1, dot(arrive, leave))))) > MAX_MERGE_TURN:
                    continue
                if ka == kb:
                    strokes[ka] = (segs_a, True)
                else:
                    strokes[ka] = (segs_a + segs_b, False)
                    del strokes[kb]
                changed = True
                break
            if changed:
                break
    return strokes


def prepare(g):
    """字形ごとの線を用意し、角の指定と、延ばさないキャップを決める。"""
    chamfer = g["name"] in CHAMFER_GLYPHS
    strokes = [Stroke(segs, cyc, chamfer) for segs, cyc in merge_strokes(g["strokes"])]
    extra_corners = []
    for c in g["corners"]:
        hit = False
        for st in strokes:
            for p in st.round:
                if math.hypot(*sub(p, c)) < 0.5:
                    st.sharp_points.add(p)
                    hit = True
        if not hit:
            extra_corners.append(c)
    for k, st in enumerate(strokes):
        if st.cyc:
            continue
        for i in (0, st.n - 1):
            p = st.round[i]
            # 自分自身の線の、端から離れた所の上で終わる端（" の両端など）も延ばさない
            pts, n = st.round, st.n
            along, near = 0.0, set()
            order = range(n - 1) if i == 0 else range(n - 1, 0, -1)
            for a in order:
                b = a + 1 if i == 0 else a - 1
                near.add(min(a, b))
                along += math.hypot(*sub(pts[b], pts[a]))
                if along > 1.5 * STANDARD_WIDTH:
                    break
            if any(distance_to_segment(p, pts[a], pts[a + 1]) < 0.25 * STANDARD_WIDTH
                   for a in range(n - 1) if a not in near):
                st.butt.add(i)
            for m, other in enumerate(strokes):
                if m == k:
                    continue
                # 他の線の端どうしが出会う角は対象外（延ばさないと角が欠ける）
                if not other.cyc and min(math.hypot(*sub(p, other.round[0])),
                                         math.hypot(*sub(p, other.round[-1]))) < 0.5 * STANDARD_WIDTH:
                    continue
                pts, n = other.round, other.n
                if any(distance_to_segment(p, pts[a], pts[(a + 1) % n]) < 0.25 * STANDARD_WIDTH
                       for a in range(n if other.cyc else n - 1)):
                    st.butt.add(i)
    return {"strokes": strokes, "extra_corners": extra_corners, "dots": g["dots"],
            "fills": [[s[0] for s in segs] for segs, _ in g["fills"]], "orient": None}


def glyph_contours(prep, width, sharp_mode):
    """(点の列, オンカーブか) の輪郭のリスト。"""
    h = width / 2
    contours = []
    has_fill = bool(prep["fills"])
    holes = []
    for st in prep["strokes"]:
        cs = st.contours(h, sharp_mode)
        if st.cyc:
            ref = st.contours(STANDARD_WIDTH / 2, False)
            outer = max(range(2), key=lambda k: abs(area(ref[k])))
            if has_fill:
                # 閉じた線の内側は塗りで埋まるので、外側の縁だけにする（内側の縁は塗りを打ち消す）
                cs = [cs[outer]]
            else:
                holes.append(len(contours) + 1 - outer)
        contours += [[(p, True) for p in c] for c in cs]
    for c in prep["dots"]:
        contours.append(dot_ring(c, h, sharp_mode))
    for poly in prep["fills"]:
        contours.append([(p, True) for p in poly])
    for c in prep["extra_corners"]:
        contours.append([(add(c, mul(v, h)), True) for v in SQUARE])
    prep["holes"] = holes
    return contours


def ensure_orient(prep):
    """輪郭の向きを丸め版・標準線幅で決めておく（時計回りが外側、穴だけ反時計回り）。"""
    if prep["orient"] is None:
        ref = glyph_contours(prep, STANDARD_WIDTH, False)
        prep["orient"] = [(area([p for p, _ in c]) > 0) != (k in prep["holes"]) for k, c in enumerate(ref)]


def draw_glyph(prep, width, roundness):
    """roundness: 1 で丸め版、0 で角版。点の数と並びはどのマスターでも同じ。"""
    contours = glyph_contours(prep, width, roundness < 0.5)
    ensure_orient(prep)  # 向きはどのマスターでも同じにする
    coords, flags, ends = [], [], []
    for c, reverse in zip(contours, prep["orient"]):
        if reverse:
            c = [c[0]] + c[1:][::-1]
        for p, on in c:
            coords.append((round(p[0]), round(p[1])))
            flags.append(1 if on else 0)
        ends.append(len(coords) - 1)
    glyph = Glyph()
    if not coords:
        glyph.numberOfContours = 0
        return glyph
    glyph.coordinates = GlyphCoordinates(coords)
    glyph.flags = bytearray(flags)
    glyph.endPtsOfContours = ends
    glyph.numberOfContours = len(ends)
    glyph.program = ttProgram.Program()
    glyph.program.fromBytecode(b"")
    return glyph


# ---- フォントの組み立て --------------------------------------------------

def axis_map(wght):
    """wght の値を線幅に変換する（WEIGHTS の間は直線補間）。"""
    keys = sorted(WEIGHTS)
    for a, b in zip(keys, keys[1:]):
        if a <= wght <= b:
            return WEIGHTS[a] + (WEIGHTS[b] - WEIGHTS[a]) * (wght - a) / (b - a)
    raise ValueError(wght)


def build_master(glyphs, preps, width, roundness, style):
    order = [".notdef"] + [g["name"] for g in glyphs]
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    cmap = {}
    for g in glyphs:
        # ASCII はソースの文字コードを使う（circumflex, tilde を ^ ~ に割り当てるため）
        # 代替字形（question.alt など）は文字に割り当てない。
        # toUnicode は ".alt" を無視するので、そのままだと元の字を上書きしてしまう
        if "." in g["name"]:
            continue
        u = chr(g["code"]) if 32 <= g["code"] < 127 else toUnicode(g["name"])
        if len(u) == 1:
            cmap[ord(u)] = g["name"]
    fb.setupCharacterMap(cmap)
    empty = Glyph()
    empty.numberOfContours = 0
    fb.setupGlyf({".notdef": empty, **{g["name"]: draw_glyph(adjusted(preps[g["name"]], g["name"], width), width, roundness)
                                       for g in glyphs}})
    adv = int(round(glyphs[0]["advance"]))
    glyf = fb.font["glyf"]
    for g in glyf.glyphs.values():
        g.recalcBounds(glyf)
    # 左サイドベアリングは輪郭の左端と同じにする。0 のままだと、ROND や wght で
    # 輪郭の左端が動いたとき、ラスタライザがその分だけ字を横へずらして置いてしまう
    fb.setupHorizontalMetrics({n: (adv, getattr(glyf[n], "xMin", 0)) for n in order})
    fb.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT)
    fb.setupNameTable({"familyName": FAMILY, "styleName": style, "copyright": COPYRIGHT,
                       "description": "OCR-A rebuilt from its ANSI X3.17 centerlines with variable stroke width and corner roundness."})
    fb.setupOS2(sTypoAscender=ASCENT, sTypoDescender=DESCENT, usWinAscent=ASCENT + 100, usWinDescent=-DESCENT + 100)
    fb.setupPost(isFixedPitch=1)
    fb.font["head"].glyphDataFormat = 0
    for g in fb.font["glyf"].glyphs.values():
        g.recalcBounds(fb.font["glyf"])
    fb.setupMaxp()
    return fb.font


def main(skeleton, outdir):
    out = Path(outdir)
    master_dir = out / "masters"
    master_dir.mkdir(parents=True, exist_ok=True)
    glyphs = read_skeleton(skeleton)
    preps = {g["name"]: prepare(g) for g in glyphs}

    doc = DesignSpaceDocument()
    w_axis = AxisDescriptor()
    w_axis.tag, w_axis.name = "wght", "Weight"
    w_axis.minimum, w_axis.default, w_axis.maximum = 100, 400, 900
    w_axis.map = [(u, d) for u, d in WEIGHTS.items()]
    r_axis = AxisDescriptor()
    r_axis.tag, r_axis.name = "ROND", "Roundness"
    r_axis.minimum, r_axis.default, r_axis.maximum = 0, 100, 100
    doc.addAxis(w_axis)
    doc.addAxis(r_axis)
    for wght, width in WEIGHTS.items():
        for rond in (0, 100):
            font = build_master(glyphs, preps, width, rond / 100, f"w{wght} r{rond}")
            path = master_dir / f"master-w{wght}-r{rond}.ttf"
            font.save(path)
            src = SourceDescriptor()
            src.path = str(path)
            src.location = {"Weight": width, "Roundness": rond}
            doc.addSource(src)
    for wght, wname in STYLES.items():
        for rond, rname in ((100, ""), (0, "Sharp")):
            style = " ".join(x for x in (rname, wname) if x) if wname != "Regular" or not rname else rname
            inst = InstanceDescriptor()
            inst.familyName, inst.styleName = FAMILY, style
            inst.designLocation = {"Weight": axis_map(wght), "Roundness": rond}
            doc.addInstance(inst)
    vf, _, _ = varLib.build(doc)
    vf.save(out / "OCRAStroke-VF.ttf")
    print("wrote", out / "OCRAStroke-VF.ttf")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
