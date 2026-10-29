"""Turns the designers' plate names into one canonical colour name.

The plate names are hand-typed ("Bamabu - Charcaol", "Bambu Matte - Ivory White",
"Mamdarin Orange"), so they are cleaned and snapped to the Bambu colour list.
"""
import difflib
import re

CANON = [
    'Ivory White', 'Bone White', 'Charcoal', 'Scarlet Red', 'Dark Red', 'Red',
    'Lemon Yellow', 'Sunflower Yellow', 'Yellow', 'Gold', 'Mandarin Orange',
    'Pumpkin Orange', 'Orange', 'Terracotta', 'Sakura Pink', 'Hot Pink', 'Pink',
    'Magenta', 'Beige', 'Lilac Purple', 'Indigo Purple', 'Purple', 'Marine Blue',
    'Sky Blue', 'Ice Blue', 'Dark Blue', 'Cobalt Blue', 'Blue', 'Blue Gray',
    'Cyan', 'Turquoise', 'Grass Green', 'Apple Green', 'Dark Green',
    'Mistletoe Green', 'Green', 'Ash Gray', 'Nardo Gray', 'Light Gray',
    'Dark Gray', 'Silver', 'Latte Brown', 'Dark Brown', 'Cocoa Brown', 'Brown',
    'Caramel', 'Desert Tan', 'Desert Sand', 'Dark Chocolate',
]
ALIAS = {
    'charcoal black': 'Charcoal', 'black': 'Charcoal', 'white': 'Ivory White',
    'cobolt blue': 'Cobalt Blue', 'indego purple': 'Indigo Purple',
    'ash grey': 'Ash Gray', 'light grey': 'Light Gray', 'dark grey': 'Dark Gray',
}
_BRAND = re.compile(r'^(bambu|bamabu|bambnu|banmbu|bmabu|bambi|banbu|bamub|bambo)\b', re.I)
_MATERIAL = re.compile(r'\b(pla|petg|matte|basic|silk|tough|lab|labs)\b', re.I)
_LOWER = {c.lower(): c for c in CANON}


def canon(plate_name):
    """Canonical colour for a plate name, or None when the name says nothing."""
    s = re.sub(r'\(.*?\)', ' ', plate_name or '')
    s = _BRAND.sub(' ', s.strip())
    s = _MATERIAL.sub(' ', s)
    s = re.sub(r'[\d\-_:]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    if not s:
        return None
    if s in ALIAS:
        return ALIAS[s]
    if s in _LOWER:
        return _LOWER[s]
    m = difflib.get_close_matches(s, list(_LOWER) + list(ALIAS), n=1, cutoff=0.8)
    if m:
        return ALIAS.get(m[0]) or _LOWER[m[0]]
    return s.title()


def hex_rgb(h):
    h = (h or '#888888').lstrip('#')[:6].ljust(6, '8')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class HexNamer:
    """Names a bare filament hex by the colour name the other files use for it."""

    def __init__(self):
        self.votes = {}

    def vote(self, hexc, name):
        if hexc and name:
            d = self.votes.setdefault(hexc.upper(), {})
            d[name] = d.get(name, 0) + 1

    def name(self, hexc):
        d = self.votes.get((hexc or '').upper())
        if d:
            return max(d, key=d.get)
        # no file names this exact hex: take the nearest hex that has a name
        r, g, b = hex_rgb(hexc)
        best = None
        for h, names in self.votes.items():
            r2, g2, b2 = hex_rgb(h)
            dist = (r - r2) ** 2 + (g - g2) ** 2 + (b - b2) ** 2
            if best is None or dist < best[0]:
                best = (dist, max(names, key=names.get))
        return best[1] if best else (hexc or 'Unknown')
