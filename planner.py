"""Turns the index into colour groups, packs them onto beds, and finds the
batch of N not-yet-printed items that needs the fewest plates."""
import itertools
import os

from colours import HexNamer, canon

BED = 256.0
EDGE = 3.0          # keep off the bed edge
GAP = 5.0           # between parts
EXCLUDE = (0.0, 0.0, 18.0, 28.0)    # P1S front-left corner, from bed_exclude_area


class Part:
    """One thing to place on a bed. From build_parts: a whole source object
    (scoring only). From split: geo = the mesh parts it carries (one, or a
    whole object that keeps its support blocker), box = their world footprint."""
    __slots__ = ('item', 'file', 'oid', 'colour', 'hex', 'box', 'pos', 'w', 'h', 'comp', 'geo', 'zmin', 'label')

    def __init__(self, item, file, oid, colour, hexc, box, pos, comp=None, geo=None, zmin=None, label=''):
        self.item, self.file, self.oid, self.colour, self.hex = item, file, oid, colour, hexc
        self.box, self.pos, self.comp, self.geo, self.zmin, self.label = box, pos, comp, geo, zmin, label
        self.w, self.h = box[2] - box[0], box[3] - box[1]


def split(parts, index, names, progress=None):
    """Object-level parts -> one Part per printable piece, measured from the
    meshes. Objects holding a support blocker/enforcer stay whole."""
    import footprint
    by_file = {}
    for p in parts:
        by_file.setdefault(p.file, []).append(p)
    out = []
    for i, (f, ps) in enumerate(by_file.items()):
        if progress:
            progress(i, len(by_file), f)
        geo = footprint.object_parts(f, index[f]['sig'], [p.oid for p in ps])
        for p in ps:
            entry = geo.get(p.oid) or {'parts': []}
            pieces = entry['parts']
            normal = [g for g in pieces if g['subtype'] == 'normal_part']
            if not normal:
                continue
            # an object holding a support blocker/enforcer moves as one; the rest split
            groups = [pieces] if len(normal) != len(pieces) else [[g] for g in normal]
            for grp in groups:
                nb = [g for g in grp if g['subtype'] == 'normal_part']
                box = [min(g['box'][0] for g in nb), min(g['box'][1] for g in nb),
                       max(g['box'][2] for g in nb), max(g['box'][3] for g in nb)]
                out.append(Part(p.item, f, p.oid, p.colour, p.hex, box, None,
                                comp=nb[0]['comp'], geo=grp, label=names.get(p.item, '')))
    return out


def build_parts(items, index, choice):
    """item key -> list of Part, using the chosen file per item."""
    namer = HexNamer()
    for f, c in index.items():
        if f == '_version' or not c.get('info'):
            continue
        cols = c['info']['filament_colour']
        for pl in c['info']['plates']:
            name = canon(pl['name'])
            for o in pl['objects']:
                e = o['extruders'][0] - 1
                if name and 0 <= e < len(cols):
                    namer.vote(cols[e], name)
    parts = {}
    for it in items:
        f = choice.get(it['key']) or (it['files'][0] if it['files'] else None)
        c = index.get(f) if f else None
        if not c or not c.get('info'):
            continue
        cols = c['info']['filament_colour']
        out = []
        for pl in c['info']['plates']:
            for o in pl['objects']:
                if not o['bbox']:
                    continue
                e = o['extruders'][0] - 1
                hexc = cols[e] if 0 <= e < len(cols) else '#888888'
                colour = canon(pl['name']) or namer.name(hexc)
                out.append(Part(it['key'], f, o['id'], colour, hexc, o['bbox'], o['pos']))
        if out:
            parts[it['key']] = out
    return parts


# ---------------------------------------------------------------- packing

class MaxRects:
    """MaxRects bin, best-short-side-fit, 90 degree turns allowed."""

    def __init__(self):
        size = BED - 2 * EDGE + GAP
        self.free = [(0.0, 0.0, size, size)]
        # the exclusion corner, in packing coordinates
        ex = (EXCLUDE[0] - EDGE, EXCLUDE[1] - EDGE, EXCLUDE[2] - EDGE + GAP, EXCLUDE[3] - EDGE + GAP)
        self._cut((max(0.0, ex[0]), max(0.0, ex[1]), ex[2] - max(0.0, ex[0]), ex[3] - max(0.0, ex[1])))

    def insert(self, w, h):
        best = None
        for fx, fy, fw, fh in self.free:
            for rw, rh, rot in ((w, h, False), (h, w, True)):
                if rw <= fw + 1e-6 and rh <= fh + 1e-6:
                    short = min(fw - rw, fh - rh)
                    long_ = max(fw - rw, fh - rh)
                    score = (short, long_, fy, fx)
                    if best is None or score < best[0]:
                        best = (score, fx, fy, rw, rh, rot)
        if best is None:
            return None
        _, x, y, rw, rh, rot = best
        self._cut((x, y, rw, rh))
        return x, y, rot

    def _cut(self, used):
        ux, uy, uw, uh = used
        new = []
        for fx, fy, fw, fh in self.free:
            if ux >= fx + fw or ux + uw <= fx or uy >= fy + fh or uy + uh <= fy:
                new.append((fx, fy, fw, fh))
                continue
            if ux > fx:
                new.append((fx, fy, ux - fx, fh))
            if ux + uw < fx + fw:
                new.append((ux + uw, fy, fx + fw - ux - uw, fh))
            if uy > fy:
                new.append((fx, fy, fw, uy - fy))
            if uy + uh < fy + fh:
                new.append((fx, uy + uh, fw, fy + fh - uy - uh))
        # drop rectangles contained in another
        keep = []
        for i, a in enumerate(new):
            inside = False
            for j, b in enumerate(new):
                if i != j and a[0] >= b[0] and a[1] >= b[1] and a[0] + a[2] <= b[0] + b[2] \
                        and a[1] + a[3] <= b[1] + b[3] and (a != b or i > j):
                    inside = True
                    break
            if not inside:
                keep.append(a)
        self.free = keep


def pack(parts):
    """Packs one colour's parts. Returns a list of plates, each a list of
    (part, x, y, rotated) with x, y the part's footprint corner on the bed."""
    order = sorted(parts, key=lambda p: (max(p.w, p.h), p.w * p.h), reverse=True)
    bins, plates = [], []
    for p in order:
        w, h = p.w + GAP, p.h + GAP
        for b, pl in zip(bins, plates):
            r = b.insert(w, h)
            if r:
                pl.append((p, r[0] + EDGE, r[1] + EDGE, r[2]))
                break
        else:
            b = MaxRects()
            r = b.insert(w, h)
            if r is None:       # bigger than a bed: give it a plate as it was
                plates.append([(p, p.box[0], p.box[1], False)])
                bins.append(b)
                continue
            bins.append(b)
            plates.append([(p, r[0] + EDGE, r[1] + EDGE, r[2])])
    # packing fills from the front-left corner; centre each plate's group on the bed
    out = []
    for pl in plates:
        x0 = min(x for _, x, _, _ in pl)
        y0 = min(y for _, _, y, _ in pl)
        x1 = max(x + (p.h if rot else p.w) for p, x, _, rot in pl)
        y1 = max(y + (p.w if rot else p.h) for p, _, y, rot in pl)
        dx, dy = max(0.0, (BED - x1 - x0) / 2), max(0.0, (BED - y1 - y0) / 2)
        out.append([(p, x + dx, y + dy, rot) for p, x, y, rot in pl])
    return out


def layout(parts):
    """All parts -> [(colour, [(part, x, y, rot), ...]), ...], colours in a stable order."""
    by = {}
    for p in parts:
        by.setdefault(p.colour, []).append(p)
    out = []
    for colour in sorted(by, key=lambda c: (-sum(p.w * p.h for p in by[c]), c)):
        for pl in pack(by[colour]):
            out.append((colour, pl))
    return out


# ---------------------------------------------------------------- search

def best_batches(parts, pool, size=3, must=(), limit=30):
    """Ranks batches of `size` items from `pool`. Returns
    [(plates, colours, keys), ...] best first."""
    pool = [k for k in pool if k in parts]
    must = [k for k in must if k in parts]
    rest = [k for k in pool if k not in must]
    need = size - len(must)
    if need < 0 or len(rest) < need:
        return []
    if need > 3:
        # too many combinations to try them all: grow the best 3 one item at a time
        out = []
        for _, _, keys in best_batches(parts, pool, len(must) + 3, must, limit=limit):
            keys = list(keys)
            while len(keys) < size:
                cols = {p.colour for k in keys for p in parts[k]}
                add = min((k for k in rest if k not in keys),
                          key=lambda k: (len({p.colour for p in parts[k]} - cols), k))
                keys.append(add)
            plates = layout([p for k in keys for p in parts[k]])
            ncol = len({p.colour for k in keys for p in parts[k]})
            if (len(plates), ncol, sorted(keys)) not in out:
                out.append((len(plates), ncol, sorted(keys)))
        out.sort(key=lambda t: (t[0], t[1], t[2]))
        return out
    allc = sorted({p.colour for k in parts for p in parts[k]})
    bit = {c: 1 << i for i, c in enumerate(allc)}
    mask, area = {}, {}
    usable = (BED - 2 * EDGE) ** 2 * 0.8
    for k in pool:
        m = 0
        a = {}
        for p in parts[k]:
            m |= bit[p.colour]
            a[p.colour] = a.get(p.colour, 0.0) + (p.w + GAP) * (p.h + GAP)
        mask[k], area[k] = m, a
    base_m = 0
    for k in must:
        base_m |= mask[k]

    # cheap pass: number of colours, then an area estimate of the plates
    cands = []
    for combo in itertools.combinations(rest, need):
        m = base_m
        for k in combo:
            m |= mask[k]
        cands.append((m.bit_count(), combo))
    cands.sort(key=lambda t: t[0])
    if not cands:
        return []
    cut = cands[0][0] + 1
    short = [c for c in cands if c[0] <= cut][:20000]
    est = []
    for ncol, combo in short:
        keys = list(must) + list(combo)
        tot = {}
        for k in keys:
            for c, a in area[k].items():
                tot[c] = tot.get(c, 0.0) + a
        lb = sum(max(1, -(-a // usable)) for a in tot.values())
        est.append((lb, ncol, keys))
    est.sort(key=lambda t: (t[0], t[1], sorted(t[2])))

    # exact pass: really pack the best few hundred
    seen, out = set(), []
    for lb, ncol, keys in est[:300]:
        sig = tuple(sorted(keys))
        if sig in seen:
            continue
        seen.add(sig)
        plates = layout([p for k in keys for p in parts[k]])
        out.append((len(plates), ncol, sorted(keys)))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return out[:limit]
