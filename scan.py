"""Reads every SPLIT 3MF's metadata into a cached index (meshes are not loaded,
except a ball's small ones when its file has no plate footprints).

For each file the index keeps its plates: plate name, the objects on it, each
object's filament, footprint on the bed and position.
"""
import hashlib
import json
import os
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

import config

CACHE = config.data_path('index.json')
UNZIP_DIR = config.data_path('unzipped')
INDEX_VERSION = 3

BED = 256.0
STRIDE = BED * 1.2          # Bambu lays plates out with a 1/5 bed gap
CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PROD = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
NS = {'c': CORE, 'p': PROD}
# process settings worth carrying onto objects from a file that is not the base
PROCESS_KEYS = ('layer_height', 'enable_support', 'support_type', 'support_style',
                'support_threshold_angle', 'support_on_build_plate_only', 'brim_type',
                'brim_width', 'wall_loops', 'sparse_infill_density', 'sparse_infill_pattern',
                'top_shell_layers', 'bottom_shell_layers', 'seam_position', 'ironing_type')


def plate_cols(count):
    v = count ** 0.5
    r = round(v)
    return int(r + 1 if v > r else r)


def plate_origin(index0, count):
    cols = plate_cols(count)
    return (index0 % cols) * STRIDE, -(index0 // cols) * STRIDE


# ---------------------------------------------------------------- finding the prints
#
# The library folders are searched all the way down, zips included. What a file
# is comes from its name or a folder above it, whichever says so first:
#   "#0100 Voltorb/02. Files/#0100 Voltorb - SPLIT Profile.3mf"   folder
#   "0001+-+Bulbasaur+-+SPLIT+Profile+-+V3.3mf"                   file name
#   "Great_Ball_-_SPLIT_Profile.3mf"                              a ball
# so files can sit sorted in folders, loose in Downloads, or still zipped.

# variants that are not the main print of a folder
VARIANT = re.compile(r'no feet|no spoons|no nose|optional|zzz|custom|tail\b', re.I)
OTHER_FORMAT = re.compile(r'\bams\b|\bmc\b', re.I)        # one-plate multicolour versions
DEX = re.compile(r'^#?(\d{3,4})(?:\s+-)?\s+(.+)$')
TRAILING = re.compile(r'(\s+v\d+(\.\d+)*\b.*|\s+\b(split|ams|mc|profile)\b.*)$', re.I)
SKIP_DIRS = {'__MACOSX', '.git', 'node_modules', '$RECYCLE.BIN'}


def _words(name):
    """'0001+-+Bulbasaur+-+SPLIT' -> '0001 - Bulbasaur - SPLIT'."""
    name = re.sub(r'\.3mf$|\.zip$', '', name, flags=re.I)
    name = name.replace('+', ' ').replace('_', ' ')
    return re.sub(r'\s+', ' ', name).strip()


def _title(name):
    # 'Poké Ball' and 'Poke Ball' are the same print
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    name = TRAILING.sub('', name.strip(' -')).strip(' -')
    return name[:1].upper() + name[1:]


def _dex(text):
    """'#0100 Voltorb V2' or '0001 - Bulbasaur - SPLIT' -> (100, 'Voltorb')."""
    segs = re.split(r'\s+-\s+', _words(text))
    m = DEX.match(segs[0] + (' - ' + segs[1] if len(segs) > 1 else ''))
    if not m or not 1 <= int(m.group(1)) <= 1100:
        return None
    rest = re.split(r'\s+-\s+', m.group(2))
    name = _title(rest[0])
    return (int(m.group(1)), name) if name else None


def identify(parts):
    """Path components (folders..., file name) -> (kind, dex, name, folder depth)
    or None. Folder names win over the file name, so a user's own sorting sticks."""
    for depth in range(len(parts) - 1):
        d = _dex(parts[depth])
        if d:
            return 'pokemon', d[0], d[1], depth
    fname = parts[-1]
    d = _dex(fname)
    if d:
        return 'pokemon', d[0], d[1], None
    words = _words(fname)
    if 'split' not in words.lower():
        return None         # unknown models only count when they are a SPLIT profile
    title = _title(re.split(r'\s+-\s+', words)[0])
    title = re.sub(r'(?i)^poke ?ball$', 'Poke Ball', title)
    if not title:
        return None
    return ('ball' if re.search(r'\bball\b', title, re.I) else 'other'), 0, title, None


def _usable(fname):
    low = fname.lower()
    return low.endswith('.3mf') and not fname.startswith('._') and not low.startswith('batch - ') \
        and not OTHER_FORMAT.search(_words(fname))


def _entry_name(info):
    """Zip tools that skip the UTF-8 flag store names as UTF-8 anyway."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode('cp437').decode('utf-8')
    except UnicodeError:
        return info.filename


def _unzip(zpath, have, progress=None):
    """SPLIT models inside a zip, extracted once into the data folder. A model
    already found unzipped (same name and size, in `have`) is left alone.
    Returns [(extracted path, path components inside the zip)]."""
    out = []
    try:
        st = os.stat(zpath)
        with zipfile.ZipFile(zpath) as z:
            entries = []
            for info in z.infolist():
                n = _entry_name(info)
                base = n.rsplit('/', 1)[-1]
                if n.startswith('__MACOSX/') or not _usable(base) or (base.lower(), info.file_size) in have:
                    continue
                have.add((base.lower(), info.file_size))
                entries.append((info, n))
            if not entries:
                return out
            dest = os.path.join(UNZIP_DIR, hashlib.sha1(zpath.lower().encode('utf-8')).hexdigest()[:12])
            stamp = os.path.join(dest, '.source')
            sig = f'{st.st_size}:{int(st.st_mtime)}'
            fresh = os.path.exists(stamp) and open(stamp, encoding='utf-8').read() == sig
            for info, n in entries:
                parts = [_words(os.path.basename(zpath))] + [p for p in n.split('/') if p]
                if not identify(parts):
                    continue
                target = os.path.join(dest, *[re.sub(r'[<>:"\|?*]', '_', p) for p in n.split('/') if p])
                if not fresh or not os.path.exists(target):
                    if progress:
                        progress(f'Unzipping {os.path.basename(zpath)}')
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                out.append((target, parts))
            os.makedirs(dest, exist_ok=True)
            with open(stamp, 'w', encoding='utf-8') as f:
                f.write(sig)
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError):
        pass        # an unreadable or password zip is skipped, not fatal
    return out


def _walk(root, skip):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')
                   and os.path.normcase(os.path.join(dirpath, d)) not in skip]
        for f in files:
            yield dirpath, f


def list_items(folders, out_dir=None, progress=None):
    """Every print found under the library folders, sorted: Pokemon by number,
    then balls, then other SPLIT models."""
    skip = {os.path.normcase(os.path.abspath(p)) for p in (out_dir, config.DATA_DIR) if p}
    found = []      # (file, path components relative to the library folder, library folder or None)
    zips = []
    for root in folders:
        if not os.path.isdir(root):
            continue
        for dirpath, f in _walk(root, skip):
            rel = os.path.relpath(dirpath, root)
            parts = ([] if rel == '.' else rel.split(os.sep)) + [f]
            full = os.path.join(dirpath, f)
            if f.lower().endswith('.zip'):
                zips.append((full, parts))
            elif _usable(f):
                found.append((full, parts, root))
    # zips last, so a model that is already unpacked somewhere is not taken twice
    have = set()
    for full, *_ in found:
        try:
            have.add((os.path.basename(full).lower(), os.path.getsize(full)))
        except OSError:
            pass
    for full, parts in zips:
        for target, zparts in _unzip(full, have, progress):
            found.append((target, parts[:-1] + zparts, None))

    items = {}
    for path, parts, root in found:
        ident = identify(parts)
        if not ident:
            continue
        kind, dex, name, depth = ident
        key = f'#{dex:04d} {name}' if kind == 'pokemon' else f'{kind}:{name}'
        k = key.lower()
        it = items.get(k)
        if it is None:
            it = items[k] = {'key': key, 'kind': kind, 'dex': dex, 'name': name, 'folder': None, 'files': []}
        if root and depth is not None and not it['folder']:
            it['folder'] = os.path.join(root, *parts[:depth + 1])
        it['files'].append(path)

    for it in items.values():
        files = it['files']
        split = [f for f in files if 'split' in _words(os.path.basename(f)).lower()]
        files = split or files
        # newest copy of a name first; then unpacked files before zipped ones,
        # the main print before its variants
        unz = os.path.normcase(UNZIP_DIR)
        files.sort(key=lambda p: (os.path.normcase(p).startswith(unz), -os.path.getmtime(p)))
        seen, uniq = set(), []
        for f in files:
            n = re.sub(r'\s*\(\d+\)$', '', _words(os.path.basename(f)).lower())
            if n not in seen:
                seen.add(n)
                uniq.append(f)
        uniq.sort(key=lambda p: (os.path.normcase(p).startswith(unz), bool(VARIANT.search(os.path.basename(p))),
                                 len(os.path.basename(p))))
        it['files'] = uniq
    order = {'pokemon': 0, 'ball': 1, 'other': 2}
    return sorted(items.values(), key=lambda it: (order[it['kind']], it['dex'], it['name'].lower()))


def parse_model_xml(raw):
    """Parses a 3MF model; some exporters leave the p: prefix undeclared."""
    text = raw.decode('utf-8', 'replace')
    if 'p:' in text and 'xmlns:p=' not in text[:2000]:
        text = text.replace('<model ', f'<model xmlns:p="{PROD}" ', 1)
    return ET.fromstring(text)


def load_json_lenient(raw):
    # a few files carry a signature line after the JSON
    return json.JSONDecoder().raw_decode(raw.decode('utf-8', 'replace').lstrip('\ufeff'))[0]


def meta(el):
    return {m.get('key'): m.get('value') for m in el.findall('metadata') if m.get('key')}


def tf(s):
    return [float(x) for x in s.split()] if s else [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]


def apply(t, x, y, z):
    """3MF row-vector transform."""
    return (x * t[0] + y * t[3] + z * t[6] + t[9],
            x * t[1] + y * t[4] + z * t[7] + t[10],
            x * t[2] + y * t[5] + z * t[8] + t[11])


def compose(a, b):
    """Transform that applies a, then b."""
    out = []
    for r in range(3):
        row = a[r * 3:r * 3 + 3]
        out += [sum(row[k] * b[k * 3 + c] for k in range(3)) for c in range(3)]
    tx, ty, tz = apply(b, a[9], a[10], a[11])
    return out + [tx, ty, tz]


_VERT = re.compile(rb'<vertex x="([-\d.eE+]+)" y="([-\d.eE+]+)" z="([-\d.eE+]+)"')


def sub_object_box(z, path, objectid, cache):
    key = (path, objectid)
    if key not in cache:
        data = z.read(path.lstrip('/'))
        for m in re.finditer(rb'<object id="(\d+)"', data):
            start = m.end()
            end = data.find(b'</object>', start)
            xs, ys, zs = [], [], []
            for v in _VERT.finditer(data, start, end):
                xs.append(float(v.group(1))); ys.append(float(v.group(2))); zs.append(float(v.group(3)))
            if xs:
                cache[(path, m.group(1).decode())] = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    return cache.get(key)


def mesh_footprint(z, comps, build_t, cache):
    """World XY box of an object, from its meshes' corner points."""
    pts = []
    for path, oid, ct in comps:
        b = sub_object_box(z, path, oid, cache)
        if not b:
            continue
        t = compose(ct, build_t)
        for x in (b[0], b[3]):
            for y in (b[1], b[4]):
                for zz in (b[2], b[5]):
                    pts.append(apply(t, x, y, zz))
    if not pts:
        return None
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def read_3mf(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        model = parse_model_xml(z.read('3D/3dmodel.model'))
        settings = ET.fromstring(z.read('Metadata/model_settings.config'))
        proj = load_json_lenient(z.read('Metadata/project_settings.config'))
        plate_json = {}
        for n in names:
            m = re.match(r'Metadata/plate_(\d+)\.json$', n)
            if m:
                plate_json[int(m.group(1))] = load_json_lenient(z.read(n))

        comps = {}
        for o in model.find('c:resources', NS).findall('c:object', NS):
            cs = o.find('c:components', NS)
            if cs is not None:
                comps[o.get('id')] = [(c.get(f'{{{PROD}}}path') or '', c.get('objectid'), tf(c.get('transform')))
                                      for c in cs.findall('c:component', NS)]
        build = {it.get('objectid'): tf(it.get('transform'))
                 for it in model.find('c:build', NS).findall('c:item', NS)}

        objects = {}
        for o in settings.findall('object'):
            md = meta(o)
            ext = {int(md.get('extruder', '1'))}
            for p in o.findall('part'):
                pm = meta(p)
                if p.get('subtype', 'normal_part') == 'normal_part' and 'extruder' in pm:
                    ext.add(int(pm['extruder']))
            objects[o.get('id')] = {'name': md.get('name', ''), 'extruders': sorted(ext)}

        plates = []
        pl_els = settings.findall('plate')
        mesh_cache = {}
        for pl in pl_els:
            md = meta(pl)
            pid = int(md['plater_id'])
            ox, oy = plate_origin(pid - 1, len(pl_els))
            ids = [meta(mi).get('object_id') for mi in pl.findall('model_instance')]
            boxes = [b['bbox'] for b in plate_json.get(pid, {}).get('bbox_objects', [])]
            objs = []
            for oid in ids:
                t = build.get(oid)
                if t is None or oid not in objects:
                    continue
                cx, cy = t[9] - ox, t[10] - oy
                # the plate json box that holds the object's origin is its footprint
                best = None
                for b in boxes:
                    inside = b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1
                    d = abs((b[0] + b[2]) / 2 - cx) + abs((b[1] + b[3]) / 2 - cy)
                    if inside and (best is None or d < best[0]):
                        best = (d, b)
                bbox = best[1] if best else None
                if bbox is None and oid in comps:
                    w = mesh_footprint(z, comps[oid], t, mesh_cache)
                    if w:
                        bbox = [w[0] - ox, w[1] - oy, w[2] - ox, w[3] - oy]
                objs.append({'id': oid, 'extruders': objects[oid]['extruders'], 'bbox': bbox,
                             'pos': [cx, cy]})
            plates.append({'id': pid, 'name': md.get('plater_name', ''), 'objects': objs})

    nfil = len(proj.get('filament_colour', []))
    diff = proj.get('different_settings_to_system') or ['']
    pkeys = set(PROCESS_KEYS) | {k for k in (diff[0] or '').split(';') if k}
    return {
        'plates': plates,
        'n_plates': len(pl_els),
        'filament_colour': proj.get('filament_colour', []),
        'filament_settings_id': proj.get('filament_settings_id', []),
        'printer': proj.get('printer_settings_id', ''),
        'list_lens': {k: len(v) for k, v in proj.items() if isinstance(v, list)},
        'n_filaments': nfil,
        'process': {k: proj[k] for k in sorted(pkeys) if k in proj},
    }


def build_index(folders, out_dir=None, progress=None, note=None):
    """progress(i, n, file) while reading models; note(text) while searching."""
    try:
        with open(CACHE, encoding='utf-8') as f:
            cache = json.load(f)
        if cache.get('_version') != INDEX_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    items = list_items(folders, out_dir, note)
    files = sorted({f for it in items for f in it['files']})
    out = {'_version': INDEX_VERSION}
    for i, f in enumerate(files):
        if progress:
            progress(i, len(files), f)
        st = os.stat(f)
        sig = f'{st.st_size}:{int(st.st_mtime)}'
        c = cache.get(f)
        if c and c.get('sig') == sig:
            out[f] = c
            continue
        try:
            out[f] = {'sig': sig, 'info': read_3mf(f), 'error': None}
        except Exception as e:  # one odd file must not stop the scan
            out[f] = {'sig': sig, 'info': None, 'error': f'{type(e).__name__}: {e}'}
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    return items, out


if __name__ == '__main__':
    s = config.Settings()
    items, idx = build_index(s.folders, s.out_dir, lambda i, n, f: print(f'{i + 1}/{n}', end='\r'))
    print()
    for it in items:
        print(it['key'], '|', it['folder'], '|', ' ; '.join(os.path.basename(f) for f in it['files']))
    for f, c in idx.items():
        if f == '_version':
            continue
        if c['error']:
            print('ERR', os.path.basename(f), c['error'])
            continue
        for pl in c['info']['plates']:
            nobox = [o for o in pl['objects'] if not o['bbox']]
            if nobox:
                print('NOBOX', os.path.basename(f), pl['name'], len(nobox))
