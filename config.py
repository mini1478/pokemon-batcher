"""Where the app keeps its settings and caches, and the user's settings.

Nothing personal lives next to the code: settings, the file index, measured
footprints and the printed list go to the per-user data folder."""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if sys.platform == 'win32':
    DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Pokemon Batcher')
elif sys.platform == 'darwin':
    DATA_DIR = os.path.expanduser('~/Library/Application Support/Pokemon Batcher')
else:
    DATA_DIR = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'),
                            'pokemon-batcher')
SETTINGS = os.path.join(DATA_DIR, 'settings.json')


def data_path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


def _migrate():
    """Early builds kept their files next to the code; move them once."""
    for name in ('index.json', 'footprints.json', 'state.json'):
        old = os.path.join(HERE, name)
        if os.path.exists(old) and not os.path.exists(os.path.join(DATA_DIR, name)):
            os.makedirs(DATA_DIR, exist_ok=True)
            shutil.move(old, os.path.join(DATA_DIR, name))


def find_bambu():
    if sys.platform == 'win32':
        for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                     os.environ.get('LOCALAPPDATA')):
            if root:
                p = os.path.join(root, 'Bambu Studio', 'bambu-studio.exe')
                if os.path.exists(p):
                    return p
    elif sys.platform == 'darwin' and os.path.exists('/Applications/BambuStudio.app'):
        return '/Applications/BambuStudio.app'
    return ''


def default_out_dir():
    docs = os.path.join(os.path.expanduser('~'), 'Documents')
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser('~'), 'Pokemon Batches')


class Settings:
    def __init__(self):
        _migrate()
        try:
            with open(SETTINGS, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        self.folders = [f for f in d.get('folders', []) if f]
        self.out_dir = d.get('out_dir') or default_out_dir()
        self.bambu = d.get('bambu') or find_bambu()
        # Windows: show a green tick on a printed Pokemon's folder in Explorer
        self.explorer_tick = d.get('explorer_tick', sys.platform == 'win32')

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS, 'w', encoding='utf-8') as f:
            json.dump({'folders': self.folders, 'out_dir': self.out_dir, 'bambu': self.bambu,
                       'explorer_tick': self.explorer_tick}, f, indent=1)
