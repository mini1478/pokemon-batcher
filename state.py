"""What is printed, what is waiting in a batch file, which file each item uses.

Printed prints are kept in state.json. With the Explorer tick on (Windows), a
Pokemon that has its own folder is printed when that folder carries the green
tick instead, so ticking the folder by hand and ticking it here agree. Items in
a batch file that still exists are 'queued' and are not offered again until the
batch is marked printed or the file is deleted.
"""
import json
import os
import re

import config
import tick

STATE = config.data_path('state.json')


class State:
    def __init__(self, items, settings):
        self.items = {it['key']: it for it in items}
        self.settings = settings
        try:
            with open(STATE, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        self.made = set(d.get('made', [])) | set(d.get('balls_made', []))
        self.choice = d.get('choice', {})
        self.include_balls = d.get('include_balls', True)
        self.size = d.get('size', 3)
        self.pins = set(d.get('pins', []))
        self.batches = d.get('batches', [])
        if self._migrate_keys():
            self.save()
        self.refresh_ticks()

    def _migrate_keys(self):
        """Early builds keyed Pokemon by folder name ('#0001 Bulbasaur V3')."""
        by_folder = {os.path.basename(it['folder']): k for k, it in self.items.items() if it['folder']}

        def fix(k):
            return k if k in self.items else by_folder.get(k, k)
        before = json.dumps([sorted(self.made), self.choice, sorted(self.pins), self.batches], sort_keys=True)
        self.made = {fix(k) for k in self.made}
        self.choice = {fix(k): v for k, v in self.choice.items()}
        self.pins = {fix(k) for k in self.pins}
        for b in self.batches:
            b['keys'] = [fix(k) for k in b['keys']]
            for pl in b.get('plates', []):
                pl['items'] = [fix(k) for k in pl['items']]
        return before != json.dumps([sorted(self.made), self.choice, sorted(self.pins), self.batches], sort_keys=True)

    def _uses_tick(self, it):
        return bool(self.settings.explorer_tick and tick.SUPPORTED and it['folder'])

    def refresh_ticks(self):
        self._ticks = {k: tick.ticked(it['folder']) for k, it in self.items.items() if self._uses_tick(it)}

    def save(self):
        with open(STATE, 'w', encoding='utf-8') as f:
            json.dump({'made': sorted(self.made), 'choice': self.choice,
                       'include_balls': self.include_balls, 'size': self.size,
                       'pins': sorted(self.pins), 'batches': self.batches}, f, indent=1)

    def is_made(self, it):
        if self._uses_tick(it):
            return self._ticks.get(it['key'], False)
        return it['key'] in self.made

    def set_made(self, it, made):
        if self._uses_tick(it):
            tick.set_ticked(it['folder'], made)
            self._ticks[it['key']] = tick.ticked(it['folder'])
        (self.made.add if made else self.made.discard)(it['key'])
        self.save()

    def live_batches(self):
        """Batches whose file still exists; a deleted file frees its items."""
        live = [b for b in self.batches if os.path.exists(b['file'])]
        if len(live) != len(self.batches):
            self.batches = live
            self.save()
        return live

    def queued(self):
        return {k for b in self.live_batches() for k in b['keys']}


def short(it):
    return f"#{it['dex']:04d} {it['name']}" if it['kind'] == 'pokemon' else it['name']


def batch_name(items, keys):
    by = {it['key']: it for it in items}
    names = ' + '.join(short(by[k]).lstrip('#') for k in keys)
    return re.sub(r'[<>:"/\\|?*]', '', f'Batch - {names}.3mf')
