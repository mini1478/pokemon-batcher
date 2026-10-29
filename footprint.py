"""Exact bed footprint of every part of an object, from its mesh.

A part's world placement is its component transform followed by the object's
build transform. Its footprint is the min/max of the transformed vertices, and
its lowest point is what has to sit on the bed. Results are cached per file.
"""
import json
import os
import re
import zipfile

import numpy as np

import config
from scan import NS, PROD, compose, meta, parse_model_xml, tf
import xml.etree.ElementTree as ET

CACHE = config.data_path('footprints.json')
_VERT = re.compile(rb'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"')
_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CACHE, encoding='utf-8') as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save():
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(_cache, f)


def _vertices(z, path, wanted):
    """objectid -> Nx3 array, for the wanted ids in one sub-model file."""
    data = z.read(path.lstrip('/'))
    out = {}
    for m in re.finditer(rb'<object id="(\d+)"', data):
        oid = m.group(1).decode()
        if oid not in wanted:
            continue
        end = data.find(b'</object>', m.end())
        vs = _VERT.findall(data, m.end(), end)
        out[oid] = np.array(vs, dtype=np.float64) if vs else np.zeros((0, 3))
    return out


def object_parts(path, sig, object_ids):
    """For each object id: {'build': its build transform, 'parts': [...]}, each part
    {'comp': i, 'path', 'objectid', 'name', 'subtype', 'world': transform,
     'box': [xmin, ymin, xmax, ymax], 'zmin': float}."""
    cache = _load()
    key = f'{path}|{sig}'
    have = cache.get(key, {})
    missing = [o for o in object_ids if o not in have]
    if missing:
        with zipfile.ZipFile(path) as z:
            model = parse_model_xml(z.read('3D/3dmodel.model'))
            cfg = ET.fromstring(z.read('Metadata/model_settings.config'))
            build = {it.get('objectid'): tf(it.get('transform'))
                     for it in model.find('c:build', NS).findall('c:item', NS)}
            cparts = {o.get('id'): o.findall('part') for o in cfg.findall('object')}
            comps = {}
            for o in model.find('c:resources', NS).findall('c:object', NS):
                if o.get('id') in missing:
                    cs = o.find('c:components', NS)
                    comps[o.get('id')] = [(c.get(f'{{{PROD}}}path'), c.get('objectid'), tf(c.get('transform')))
                                          for c in (cs.findall('c:component', NS) if cs is not None else [])]
            by_path = {}
            for oid, cl in comps.items():
                for p, sub, _ in cl:
                    by_path.setdefault(p, set()).add(sub)
            verts = {}
            for p, subs in by_path.items():
                for sub, arr in _vertices(z, p, subs).items():
                    verts[(p, sub)] = arr
        for oid in missing:
            parts = []
            plist = cparts.get(oid, [])
            for i, (p, sub, ct) in enumerate(comps.get(oid, [])):
                w = compose(ct, build[oid])
                v = verts.get((p, sub))
                if v is None or not len(v):
                    continue
                m = np.array(w[:9]).reshape(3, 3)
                pts = v @ m + np.array(w[9:12])
                pm = meta(plist[i]) if i < len(plist) else {}
                parts.append({
                    'comp': i, 'path': p, 'objectid': sub, 'world': w,
                    'name': pm.get('name', f'part {i + 1}'),
                    'subtype': plist[i].get('subtype', 'normal_part') if i < len(plist) else 'normal_part',
                    'box': [float(pts[:, 0].min()), float(pts[:, 1].min()),
                            float(pts[:, 0].max()), float(pts[:, 1].max())],
                    'zmin': float(pts[:, 2].min()),
                })
            have[oid] = {'build': build[oid], 'parts': parts}
        cache[key] = have
        _save()
    return {o: have[o] for o in object_ids}
