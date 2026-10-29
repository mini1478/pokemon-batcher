"""Writes one Bambu Studio project from parts of several SPLIT projects.

Every placed piece gets its own mesh file with its final orientation baked into
the vertices, centred on its own origin. Component transforms and part matrices
are identity and the build item is a pure move, so Bambu Studio cannot read the
placement differently from what is written. Triangles (and any paint data on
them) are copied unchanged. Each plate holds one colour; each colour gets one
filament slot whose settings come from the first file that used that colour.
"""
import io
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from scan import NS, PROD, load_json_lenient, meta, parse_model_xml, plate_cols, STRIDE

FLUSH = '280'
IDENTITY = '1 0 0 0 1 0 0 0 1 0 0 0'
IDENTITY4 = '1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1'
_VERT = re.compile(rb'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"')
SUB_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
            f'xmlns:p="{PROD}" requiredextensions="p">\n'
            ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n')


def per_filament_keys(index):
    """project_settings keys that hold a fixed number of entries per filament,
    learnt from files with different filament counts. How many per filament
    depends on the Bambu Studio version that saved the file: newer ones keep
    one value per nozzle type (Standard, High Flow), so it is not stored here."""
    seen = {}
    for f, c in index.items():
        if f == '_version' or not c.get('info'):
            continue
        n = c['info']['n_filaments']
        if not n:
            continue
        for k, ln in c['info']['list_lens'].items():
            seen.setdefault(k, set()).add((n, ln))
    keys = set()
    for k, pairs in seen.items():
        if len({n for n, _ in pairs}) < 2 or len({ln for _, ln in pairs}) < 2:
            continue
        if all(ln >= n and ln % n == 0 for n, ln in pairs):
            keys.add(k)
    return keys - {'flush_volumes_matrix', 'filament_self_index'}


class MeshSource:
    """Reads sub-object meshes out of the source files, one model file at a time."""

    def __init__(self):
        self.files = {}

    def block(self, path3mf, model_path, objectid):
        key = (path3mf, model_path)
        if key not in self.files:
            with zipfile.ZipFile(path3mf) as z:
                data = z.read(model_path.lstrip('/'))
            blocks = {}
            for m in re.finditer(rb'<object id="(\d+)"', data):
                end = data.find(b'</object>', m.end())
                blocks[m.group(1).decode()] = (m.start(), end)
            self.files[key] = (data, blocks)
        data, blocks = self.files[key]
        s, e = blocks[objectid]
        body = data[s:e]
        v0, v1 = body.find(b'<vertices>'), body.find(b'</vertices>')
        t0, t1 = body.find(b'<triangles>'), body.find(b'</triangles>')
        verts = np.array(_VERT.findall(body, v0, v1), dtype=np.float64)
        return verts, body[t0:t1 + len(b'</triangles>')]

    def drop(self, path3mf):
        for k in [k for k in self.files if k[0] == path3mf]:
            del self.files[k]


def _world(verts, w, rot):
    m = np.array(w[:9]).reshape(3, 3)
    pts = verts @ m + np.array(w[9:12])
    if rot:
        pts = np.column_stack((-pts[:, 1], pts[:, 0], pts[:, 2]))
    return pts


def _write_vertices(out, pts):
    buf = io.StringIO()
    np.savetxt(buf, pts, fmt='     <vertex x="%.6f" y="%.6f" z="%.6f"/>')
    out.write(buf.getvalue().encode())


def write_batch(out_path, plate_layout, index, title, progress=None):
    """plate_layout: [(colour, [(part, x, y, rotated), ...]), ...] from planner.layout
    over planner.split pieces."""
    per_fil = per_filament_keys(index)
    files = []
    for _, pl in plate_layout:
        for p, *_ in pl:
            if p.file not in files:
                files.append(p.file)
    base_file = files[0]

    # one filament slot per colour, in plate order
    slots, slot_src = [], []
    for colour, pl in plate_layout:
        if colour not in slots:
            p = pl[0][0]
            info = index[p.file]['info']
            fid = 0
            for o in (o for plate in info['plates'] for o in plate['objects'] if o['id'] == p.oid):
                fid = o['extruders'][0] - 1
            slots.append(colour)
            slot_src.append((p.file, fid, p.hex))
    slot_of = {c: i for i, c in enumerate(slots)}

    srcs = {}
    for f in files:
        with zipfile.ZipFile(f) as z:
            srcs[f] = {
                'model': parse_model_xml(z.read('3D/3dmodel.model')),
                'settings': ET.fromstring(z.read('Metadata/model_settings.config')),
                'proj': load_json_lenient(z.read('Metadata/project_settings.config')),
            }
    app = 'BambuStudio-02.05.00.66'
    for m in srcs[base_file]['model'].findall('c:metadata', NS):
        if m.get('name') == 'Application' and m.text:
            app = m.text

    cols = plate_cols(len(plate_layout))
    res_xml, build_xml, cfg_objects, plates_xml, rels = [], [], [], [], []
    meshes = MeshSource()
    total = sum(len(pl) for _, pl in plate_layout)
    done = 0
    tmp = out_path + '.part'
    z = zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=1)
    try:
        oid = 0
        identify = 1000
        # pieces from one source file are written together so its meshes are read once
        order = sorted(((pi, j) for pi, (_, pl) in enumerate(plate_layout) for j in range(len(pl))),
                       key=lambda t: files.index(plate_layout[t[0]][1][t[1]][0].file))
        ids = {}
        current_file = None
        for pi, j in order:
            colour, pl = plate_layout[pi]
            p, x, y, rot = pl[j]
            if p.file != current_file:
                if current_file:
                    meshes.drop(current_file)
                current_file = p.file
            if progress:
                progress(done, total)
            done += 1
            # Bambu keys meshes by number alone, so every mesh and object number
            # is unique in the project: the meshes first, then their object
            sub_ids = [oid + 1 + k for k in range(len(p.geo))]
            oid += len(p.geo) + 1
            ids[(pi, j)] = oid
            ox, oy = (pi % cols) * STRIDE, -(pi // cols) * STRIDE

            # bake: world placement (+ quarter turn), then centre on the piece's own origin
            baked = []
            for g in p.geo:
                verts, tris = meshes.block(p.file, g['path'], g['objectid'])
                baked.append((g, _world(verts, g['world'], rot), tris))
            normal = [b for b in baked if b[0]['subtype'] == 'normal_part'] or baked
            allp = np.vstack([b[1] for b in normal])
            lo, hi = allp.min(axis=0), allp.max(axis=0)
            centre = (lo + hi) / 2
            w, h = hi[0] - lo[0], hi[1] - lo[1]
            tx, ty, tz = x + w / 2 + ox, y + h / 2 + oy, (hi[2] - lo[2]) / 2

            name = f'3D/Objects/object_{oid}.model'
            with z.open(name, 'w', force_zip64=True) as out:
                out.write(SUB_HEAD.encode())
                for k, (g, pts, tris) in enumerate(baked):
                    out.write(f'  <object id="{sub_ids[k]}" p:UUID="{oid:04x}{k:04x}-81cb-4c03-9d28-80fed5dfa1dc" '
                              f'type="model">\n   <mesh>\n    <vertices>\n'.encode())
                    _write_vertices(out, pts - centre)
                    out.write(b'    </vertices>\n    ')
                    out.write(tris)
                    out.write(b'\n   </mesh>\n  </object>\n')
                out.write(b' </resources>\n <build/>\n</model>\n')
            rels.append(f'/{name}')
            comps = '\n'.join(f'    <component p:path="/{name}" objectid="{sub_ids[k]}" '
                              f'p:UUID="{oid:04x}{k:04x}-b206-40ff-9872-83e8017abed1" transform="{IDENTITY}"/>'
                              for k in range(len(baked)))
            res_xml.append(f'  <object id="{oid}" p:UUID="{oid:08x}-61cb-4c03-9d28-80fed5dfa1dc" type="model">\n'
                           f'   <components>\n{comps}\n   </components>\n  </object>')
            build_xml.append(f'  <item objectid="{oid}" p:UUID="{oid:08x}-b1ec-4553-aec9-835e5b724bb4" '
                             f'transform="1 0 0 0 1 0 0 0 1 {tx:.6f} {ty:.6f} {tz:.6f}" printable="1"/>')
            cfg_objects.append(_settings_object(p, oid, sub_ids, baked, srcs, index, base_file,
                                                str(slot_of[colour] + 1)))

        for pi, (colour, pl) in enumerate(plate_layout):
            lines = [f'    <metadata key="plater_id" value="{pi + 1}"/>',
                     f'    <metadata key="plater_name" value="{_esc(colour)}"/>',
                     '    <metadata key="locked" value="false"/>',
                     '    <metadata key="filament_map_mode" value="Auto For Flush"/>',
                     f'    <metadata key="filament_maps" value="{" ".join(["1"] * len(slots))}"/>']
            for j in range(len(pl)):
                identify += 1
                lines += ['    <model_instance>',
                          f'      <metadata key="object_id" value="{ids[(pi, j)]}"/>',
                          '      <metadata key="instance_id" value="0"/>',
                          f'      <metadata key="identify_id" value="{identify}"/>',
                          '    </model_instance>']
            plates_xml.append('  <plate>\n' + '\n'.join(lines) + '\n  </plate>')

        model_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
            f'xmlns:p="{PROD}" requiredextensions="p">\n'
            f' <metadata name="Application">{_esc(app)}</metadata>\n'
            ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
            f' <metadata name="Title">{_esc(title)}</metadata>\n'
            ' <resources>\n' + '\n'.join(res_xml) + '\n </resources>\n'
            f' <build p:UUID="{uuid.uuid4()}">\n' + '\n'.join(build_xml) + '\n </build>\n</model>\n')
        cfg = '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
        for so in cfg_objects:
            ET.indent(so, space='  ', level=1)
            cfg += '  ' + ET.tostring(so, encoding='unicode').rstrip() + '\n'
        cfg += '\n'.join(plates_xml) + '\n</config>\n'
        proj = _merge_project(srcs[base_file]['proj'], slots, slot_src, srcs, per_fil)

        z.writestr('[Content_Types].xml',
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                   ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                   ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
                   ' <Default Extension="png" ContentType="image/png"/>\n'
                   ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n</Types>\n')
        z.writestr('_rels/.rels',
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                   ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n</Relationships>\n')
        z.writestr('3D/3dmodel.model', model_xml)
        z.writestr('3D/_rels/3dmodel.model.rels',
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                   + ''.join(f' <Relationship Target="{r}" Id="rel-{i + 1}" '
                             'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
                             for i, r in enumerate(rels))
                   + '</Relationships>\n')
        z.writestr('Metadata/model_settings.config', cfg)
        z.writestr('Metadata/project_settings.config', json.dumps(proj, indent=4))
    finally:
        z.close()
    os.replace(tmp, out_path)
    return slots


def _settings_object(p, oid, sub_ids, baked, srcs, index, base_file, slot):
    """The object's entry in model_settings: the source object's own settings,
    then one part per mesh with an identity matrix."""
    src_obj = None
    for o in srcs[p.file]['settings'].findall('object'):
        if o.get('id') == p.oid:
            src_obj = o
    src_parts = src_obj.findall('part') if src_obj is not None else []
    so = ET.Element('object', id=str(oid))
    have = set()
    if src_obj is not None:
        for m in src_obj.findall('metadata'):
            k = m.get('key')
            if k is None:
                continue
            have.add(k)
            v = m.get('value')
            if k == 'extruder':
                v = slot
            elif k in ('support_filament', 'support_interface_filament'):
                v = '0'
            elif k == 'name':
                single = len(baked) == 1
                v = f'{p.label} - {baked[0][0]["name"]}' if single else f'{p.label} - {v}'
            ET.SubElement(so, 'metadata', key=k, value=v)
    if 'name' not in have:
        ET.SubElement(so, 'metadata', key='name', value=f'{p.label} - {baked[0][0]["name"]}')
    if 'extruder' not in have:
        ET.SubElement(so, 'metadata', key='extruder', value=slot)
    if p.file != base_file:
        # the source's own print settings where they differ from the base file's
        bproc = index[base_file]['info']['process']
        for k, v in index[p.file]['info']['process'].items():
            if k not in have and bproc.get(k) != v and not isinstance(v, list) \
                    and k != 'print_sequence' and not k.startswith(('prime_tower', 'wipe_tower')):
                ET.SubElement(so, 'metadata', key=k, value=str(v))
    for k, (g, _, _) in enumerate(baked):
        sp = src_parts[g['comp']] if g['comp'] < len(src_parts) else None
        part = ET.SubElement(so, 'part', id=str(sub_ids[k]), subtype=g['subtype'])
        ET.SubElement(part, 'metadata', key='name', value=g['name'])
        ET.SubElement(part, 'metadata', key='matrix', value=IDENTITY4)
        if sp is not None:
            for m in sp.findall('metadata'):
                key = m.get('key') or ''
                if key in ('name', 'matrix') or key.startswith('source_') or not key:
                    continue
                ET.SubElement(part, 'metadata', key=key, value=slot if key == 'extruder' else m.get('value'))
    return so


def _esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _per_filament(values, n):
    """Entries per filament of a list setting, or None if it does not divide."""
    if not isinstance(values, list) or not values or not n or len(values) % n:
        return None
    return len(values) // n


def _merge_project(base, slots, slot_src, srcs, per_fil):
    """The base file's settings, with every per-filament list rebuilt for the
    batch's filaments in the base file's own layout."""
    proj = json.loads(json.dumps(base))
    n_out = len(slots)
    n_base = len(base.get('filament_colour', [])) or 1
    for k in per_fil:
        bv = proj.get(k)
        kper = _per_filament(bv, n_base)
        if not kper:
            continue
        vals = []
        for f, fid, _ in slot_src:
            src = srcs[f]['proj']
            v = src.get(k)
            n_src = len(src.get('filament_colour', [])) or 1
            sk = _per_filament(v, n_src)
            if sk and fid < n_src:
                # a file from another Bambu Studio version may keep fewer values per
                # filament: its own first, the base file's for the rest
                vals += (v[fid * sk:(fid + 1) * sk] + bv[sk:kper])[:kper]
            else:
                vals += bv[:kper]
        proj[k] = vals
    proj['filament_colour'] = [h for _, _, h in slot_src]
    # packed plates have no room for print-by-object clearances
    proj['print_sequence'] = 'by layer'
    if 'filament_self_index' in proj:
        k = _per_filament(proj['filament_self_index'], n_base) or 1
        proj['filament_self_index'] = [str(i + 1) for i in range(n_out) for _ in range(k)]
    if 'flush_volumes_matrix' in proj:
        proj['flush_volumes_matrix'] = ['0' if i == j else FLUSH for i in range(n_out) for j in range(n_out)]
    for k in ('different_settings_to_system', 'inherits_group'):
        v = proj.get(k)
        if isinstance(v, list) and len(v) >= 2:
            mid = []
            for f, fid, _ in slot_src:
                sv = srcs[f]['proj'].get(k) or []
                mid.append(sv[1 + fid] if len(sv) > 2 + fid else '')
            proj[k] = [v[0]] + mid + [v[-1]]
    for k, v in list(proj.items()):
        if k.endswith('_filament') and isinstance(v, str) and v.isdigit() and int(v) > n_out:
            proj[k] = '0' if k.startswith('support') else '1'
    return proj
