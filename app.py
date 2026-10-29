"""Pokemon Batcher: pick the next N unprinted Pokemon/balls that share the most
colours and write them into one Bambu Studio project, one plate per colour."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config
import merge
import planner
import scan
import state as st
import tick

ICON = os.path.join(config.HERE, 'icon.ico')
ICON_PNG = os.path.join(config.HERE, 'icon.png')

BG, PANEL, ROW_HI, FG, DIM, ACCENT, LINE = '#1e1f22', '#26282c', '#2f3237', '#e8e8e8', '#9a9ca0', '#00ae42', '#3a3d42'
QUEUED, PIN = '#e0a526', '#f0c419'
ROW_H = 30
FONT = ('Segoe UI', 10)


class App:
    def __init__(self, root):
        self.root = root
        root.title('Pokémon Batcher')
        root.geometry('1240x820')
        root.configure(bg=BG)
        try:
            if sys.platform == 'win32':
                root.iconbitmap(default=ICON)
            else:
                root.iconphoto(True, tk.PhotoImage(file=ICON_PNG))
        except tk.TclError:
            pass
        self.settings = config.Settings()
        self.items, self.index, self.parts, self.state = [], {}, {}, None
        self.hexes = {}
        self.ranked, self.rank_i, self.current = [], 0, None
        self.filter = tk.StringVar(value='todo')
        self.search = tk.StringVar()
        self.size = tk.IntVar(value=3)
        self.balls = tk.BooleanVar(value=True)
        self.busy = False
        self._calls = queue.Queue()
        self._style()
        self._build()
        self._drain()
        if self.settings.folders:
            self.reload()
        else:
            self.status('Pick the folder your model files are in to get started.')
            root.after(300, self.open_settings)

    def reload(self):
        """Searches the library folders again and re-reads changed files."""
        if self.busy:
            return
        self.busy = True
        self._buttons()
        self.status('Looking for model files…')
        threading.Thread(target=self._load, daemon=True).start()

    def ui(self, fn, *args):
        """Runs fn on the Tk thread; worker threads must not touch Tk."""
        self._calls.put((fn, args))

    def _drain(self):
        while True:
            try:
                fn, args = self._calls.get_nowait()
            except queue.Empty:
                break
            fn(*args)
        self.root.after(50, self._drain)

    # ------------------------------------------------------------ layout
    def _style(self):
        s = ttk.Style()
        s.theme_use('clam')
        s.configure('.', background=BG, foreground=FG, font=FONT, fieldbackground=PANEL)
        s.configure('TFrame', background=BG)
        s.configure('Panel.TFrame', background=PANEL)
        s.configure('TLabel', background=BG, foreground=FG)
        s.configure('Panel.TLabel', background=PANEL, foreground=FG)
        s.configure('Dim.TLabel', background=PANEL, foreground=DIM)
        s.configure('H.TLabel', background=BG, foreground=FG, font=('Segoe UI Semibold', 15))
        s.configure('PH.TLabel', background=PANEL, foreground=FG, font=('Segoe UI Semibold', 13))
        s.configure('TRadiobutton', background=BG, foreground=FG)
        s.map('TRadiobutton', background=[('active', BG)])
        s.configure('TCheckbutton', background=PANEL, foreground=FG)
        s.map('TCheckbutton', background=[('active', PANEL)])
        s.configure('TButton', background=ROW_HI, foreground=FG, borderwidth=0, padding=(12, 6))
        s.map('TButton', background=[('active', LINE), ('disabled', PANEL)], foreground=[('disabled', DIM)])
        s.configure('Go.TButton', background=ACCENT, foreground='white', font=('Segoe UI Semibold', 12), padding=(16, 12))
        s.map('Go.TButton', background=[('active', '#00c24a'), ('disabled', LINE)])
        s.configure('TEntry', fieldbackground=PANEL, foreground=FG, insertcolor=FG, borderwidth=0)
        s.configure('TSpinbox', fieldbackground=ROW_HI, foreground=FG, arrowcolor=FG, borderwidth=0)
        s.configure('Vertical.TScrollbar', background=ROW_HI, troughcolor=BG, borderwidth=0, arrowcolor=FG)

    def _build(self):
        left = ttk.Frame(self.root, padding=(14, 12, 6, 0))
        left.pack(side='left', fill='both', expand=True)
        right = ttk.Frame(self.root, padding=(6, 12, 14, 0), width=460)
        right.pack(side='right', fill='y')
        right.pack_propagate(False)

        top = ttk.Frame(left)
        top.pack(fill='x')
        ttk.Label(top, text='All prints', style='H.TLabel').pack(side='left')
        self.counts = ttk.Label(top, text='', foreground=DIM)
        self.counts.pack(side='left', padx=12, pady=(6, 0))
        ttk.Button(top, text='⚙  Settings', command=self.open_settings).pack(side='right')
        ttk.Button(top, text='⟳  Rescan', command=self.reload).pack(side='right', padx=(0, 6))
        bar = ttk.Frame(left)
        bar.pack(fill='x', pady=(8, 6))
        e = ttk.Entry(bar, textvariable=self.search, width=24)
        e.pack(side='left', ipady=3)
        self.search.trace_add('write', lambda *_: self.redraw())
        for val, text in (('todo', 'To print'), ('all', 'All'), ('queued', 'In a batch'), ('made', 'Printed')):
            ttk.Radiobutton(bar, text=text, value=val, variable=self.filter,
                            command=self.redraw).pack(side='left', padx=(12, 0))

        wrap = ttk.Frame(left)
        wrap.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Button-1>', self._click)
        self.canvas.bind('<Button-3>', self._menu)
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<Leave>', lambda e: self._set_hover(None))
        self.canvas.bind('<Configure>', lambda e: self.redraw())
        self.canvas.bind_all('<MouseWheel>', lambda e: self.canvas.yview_scroll(-e.delta // 40, 'units'))
        self.hover_row = None
        self.rows = []
        ttk.Label(left, text='Click the box to mark a print as printed.   Right-click a row: always include it, '
                             'pick its file, open its folder.', foreground=DIM).pack(anchor='w', pady=(6, 0))

        # ---- right side
        card = ttk.Frame(right, style='Panel.TFrame', padding=16)
        card.pack(fill='x')
        ttk.Label(card, text='Next batch', style='PH.TLabel').pack(anchor='w')
        row = ttk.Frame(card, style='Panel.TFrame')
        row.pack(fill='x', pady=(10, 4))
        ttk.Label(row, text='Prints per batch', style='Panel.TLabel').pack(side='left')
        sp = ttk.Spinbox(row, from_=1, to=8, width=4, textvariable=self.size, command=self._settings_changed)
        sp.pack(side='left', padx=8)
        sp.bind('<KeyRelease>', lambda e: self._settings_changed())
        ttk.Checkbutton(card, text='Balls count too', variable=self.balls,
                        command=self._settings_changed).pack(anchor='w', pady=(2, 10))
        self.go = ttk.Button(card, text='Make next batch', style='Go.TButton', command=self.make_batch)
        self.go.pack(fill='x')
        self.pins_lbl = ttk.Label(card, text='', style='Dim.TLabel', wraplength=420)
        self.pins_lbl.pack(anchor='w', pady=(8, 0))

        self.result = ttk.Frame(right, style='Panel.TFrame', padding=16)
        self.result.pack(fill='x', pady=(12, 0))
        self.res_title = ttk.Label(self.result, text='No batch made yet.', style='PH.TLabel', wraplength=420)
        self.res_title.pack(anchor='w')
        self.res_canvas = tk.Canvas(self.result, bg=PANEL, highlightthickness=0, height=10)
        self.res_canvas.pack(fill='x', pady=(8, 8))
        btns = ttk.Frame(self.result, style='Panel.TFrame')
        btns.pack(fill='x')
        self.b_open = ttk.Button(btns, text='Open in Bambu Studio', command=self.open_current)
        self.b_show = ttk.Button(btns, text='Show file', command=self.show_current)
        self.b_other = ttk.Button(btns, text='Try a different set', command=self.try_other)
        self.b_done = ttk.Button(btns, text='Mark printed', command=self.done_current)
        for i, b in enumerate((self.b_open, self.b_show, self.b_other, self.b_done)):
            b.grid(row=i // 2, column=i % 2, sticky='ew', padx=(0 if i % 2 == 0 else 6, 0), pady=(0, 6))
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        q = ttk.Frame(right, style='Panel.TFrame', padding=16)
        q.pack(fill='both', expand=True, pady=(12, 0))
        ttk.Label(q, text='Batch files waiting to print', style='PH.TLabel').pack(anchor='w')
        self.qframe = ttk.Frame(q, style='Panel.TFrame')
        self.qframe.pack(fill='both', expand=True, pady=(8, 0))

        self.statusbar = ttk.Label(self.root, text='', foreground=DIM, padding=(14, 4))
        self.statusbar.pack(side='bottom', fill='x', before=left)
        self._buttons()

    # ------------------------------------------------------------ data
    def _load(self):
        def prog(i, n, f):
            self.ui(self.status, f'Reading model files {i + 1}/{n}: {os.path.basename(f)}')
        try:
            items, index = scan.build_index(self.settings.folders, self.settings.out_dir, prog,
                                            lambda text: self.ui(self.status, text + '…'))
            stt = st.State(items, self.settings)
        except Exception as e:  # shown to the user, the app keeps running
            self.ui(self._load_failed, f'{type(e).__name__}: {e}')
            return
        self.ui(self._loaded, items, index, stt)

    def _load_failed(self, err):
        self.busy = False
        self._buttons()
        self.status('Reading the model files failed.')
        messagebox.showerror('Pokémon Batcher', f'Could not read the model files:\n{err}')

    def _loaded(self, items, index, stt):
        self.busy = False
        self.items, self.index = items, index
        self.by_key = {it['key']: it for it in items}
        self.state = stt
        if self.current and not os.path.exists(self.current['file']):
            self.current = None
        self.size.set(self.state.size)
        self.balls.set(self.state.include_balls)
        self._rebuild_parts()
        bad = [it['name'] for it in items if it['key'] not in self.parts]
        if not items:
            self.status('No model files found. Check the folders in Settings.')
        else:
            self.status(f'{len(self.parts)} prints ready.' + (f'  Could not read: {", ".join(bad)}' if bad else ''))
        self._buttons()
        self.redraw()
        self.refresh_queue()

    def _rebuild_parts(self):
        self.parts = planner.build_parts(self.items, self.index, self.state.choice)
        votes = {}
        for ps in self.parts.values():
            for p in ps:
                d = votes.setdefault(p.colour, {})
                d[p.hex] = d.get(p.hex, 0) + 1
        self.hexes = {c: max(d, key=d.get) for c, d in votes.items()}
        self.ranked = []

    def colours_of(self, key):
        seen = []
        for p in self.parts.get(key, []):
            if p.colour not in seen:
                seen.append(p.colour)
        return seen

    def status(self, text):
        self.statusbar.configure(text=text)

    def _settings_changed(self):
        if not self.state:
            return
        try:
            self.state.size = max(1, min(8, int(self.size.get())))
        except (tk.TclError, ValueError):
            return
        self.state.include_balls = bool(self.balls.get())
        self.state.save()
        self.ranked = []

    # ------------------------------------------------------------ list
    def visible(self):
        if not self.state:
            return []
        q = self.search.get().strip().lower()
        queued = self.state.queued()
        out = []
        for it in self.items:
            made = self.state.is_made(it)
            f = self.filter.get()
            if f == 'todo' and (made or it['key'] in queued):
                continue
            if f == 'made' and not made:
                continue
            if f == 'queued' and it['key'] not in queued:
                continue
            if q and q not in it['name'].lower() and q not in f"{it['dex']:04d}":
                continue
            out.append(it)
        return out

    def redraw(self):
        c = self.canvas
        c.delete('all')
        if not self.state:
            return
        self.rows = self.visible()
        if not self.items:
            c.create_text(24, 30, anchor='nw', fill=DIM, font=FONT, width=max(200, c.winfo_width() - 48),
                          text='No prints found yet.\n\nOpen ⚙ Settings and add the folder (or folders) your '
                               'downloaded 3MF files are in. Sub-folders and zip files are searched too.')
        queued = self.state.queued()
        w = c.winfo_width()
        for i, it in enumerate(self.rows):
            y = i * ROW_H
            tag = f'r{i}'
            c.create_rectangle(0, y, w, y + ROW_H, fill=ROW_HI if i == self.hover_row else PANEL,
                               width=0, tags=(tag, 'bg'))
            made = self.state.is_made(it)
            c.create_rectangle(12, y + 7, 28, y + 23, outline=ACCENT if made else DIM, width=2,
                               fill=ACCENT if made else PANEL)
            if made:
                c.create_line(15, y + 15, 19, y + 19, 25, y + 11, fill='white', width=2)
            num = f"#{it['dex']:04d}" if it['kind'] == 'pokemon' else 'ball'
            c.create_text(42, y + ROW_H / 2, text=num, anchor='w', fill=DIM, font=FONT)
            name_col = DIM if made else FG
            c.create_text(100, y + ROW_H / 2, text=it['name'], anchor='w', fill=name_col, font=FONT)
            x = 290
            if it['key'] in self.state.pins:
                c.create_text(x - 14, y + ROW_H / 2, text='★', fill=PIN, font=FONT)
            if it['key'] not in self.parts:
                c.create_text(x, y + ROW_H / 2, text='could not read its file', anchor='w', fill='#d0605e', font=FONT)
                continue
            for col in self.colours_of(it['key']):
                h = self.hexes.get(col, '#888')
                c.create_oval(x, y + 8, x + 14, y + 22, fill=h, outline='#6b6e73')
                x += 19
            names = ', '.join(self.colours_of(it['key']))
            c.create_text(x + 6, y + ROW_H / 2, text=names, anchor='w', fill=DIM, font=('Segoe UI', 9))
            if it['key'] in queued:
                c.create_text(w - 12, y + ROW_H / 2, text='in a batch', anchor='e', fill=QUEUED, font=FONT)
            elif len(it['files']) > 1:
                pick = self.state.choice.get(it['key']) or it['files'][0]
                c.create_text(w - 12, y + ROW_H / 2, text=os.path.basename(pick)[:38], anchor='e',
                              fill='#6b6e73', font=('Segoe UI', 8))
        c.configure(scrollregion=(0, 0, w, len(self.rows) * ROW_H))
        made = sum(1 for it in self.items if self.state.is_made(it))
        self.counts.configure(text=f'{made} printed · {len(queued)} in batch files · '
                                   f'{len(self.items) - made - len(queued - {it["key"] for it in self.items if self.state.is_made(it)})} to go')
        pins = [self.by_key[k]['name'] for k in self.state.pins if k in self.by_key]
        self.pins_lbl.configure(text=('Always included: ' + ', '.join(pins)) if pins else
                                'Tip: right-click a print → "Always include" to force it into the next batch.')

    def _row_at(self, event):
        y = self.canvas.canvasy(event.y)
        i = int(y // ROW_H)
        return i if 0 <= i < len(self.rows) else None

    def _set_hover(self, i):
        if i != self.hover_row:
            self.hover_row = i
            self.redraw()

    def _hover(self, event):
        self._set_hover(self._row_at(event))

    def _click(self, event):
        i = self._row_at(event)
        if i is None or event.x > 40:
            return
        it = self.rows[i]
        made = not self.state.is_made(it)
        self.status(f"{'Marking' if made else 'Unmarking'} {it['name']}…")

        def work():
            self.state.set_made(it, made)
            self.ui(lambda: (self.redraw(), self.status(
                f"{it['name']} {'marked printed' if made else 'no longer marked printed'}.")))
        threading.Thread(target=work, daemon=True).start()
        self.ranked = []

    def _menu(self, event):
        i = self._row_at(event)
        if i is None:
            return
        it = self.rows[i]
        m = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT)
        pinned = it['key'] in self.state.pins
        m.add_command(label='Don\'t force into batches' if pinned else 'Always include in the next batch',
                      command=lambda: self._toggle_pin(it))
        if len(it['files']) > 1:
            sub = tk.Menu(m, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT)
            cur = self.state.choice.get(it['key']) or it['files'][0]
            for f in it['files']:
                sub.add_command(label=('● ' if f == cur else '   ') + os.path.basename(f),
                                command=lambda f=f: self._choose(it, f))
            m.add_cascade(label='Use file', menu=sub)
        folder = it['folder'] or (os.path.dirname(it['files'][0]) if it['files'] else None)
        if folder:
            m.add_command(label='Open folder', command=lambda: open_path(folder))
        m.tk_popup(event.x_root, event.y_root)

    def _toggle_pin(self, it):
        (self.state.pins.discard if it['key'] in self.state.pins else self.state.pins.add)(it['key'])
        self.state.save()
        self.ranked = []
        self.redraw()

    def _choose(self, it, f):
        self.state.choice[it['key']] = f
        self.state.save()
        self._rebuild_parts()
        self.redraw()

    # ------------------------------------------------------------ batches
    def _pool(self):
        queued = self.state.queued()
        return [it['key'] for it in self.items
                if it['key'] in self.parts and not self.state.is_made(it) and it['key'] not in queued
                and (self.balls.get() or it['kind'] != 'ball')]

    def make_batch(self):
        if self.busy or not self.state:
            return
        self._settings_changed()
        pool = self._pool()
        must = [k for k in self.state.pins if k in pool]
        size = self.state.size
        if len(pool) < size:
            messagebox.showinfo('Pokémon Batcher', f'Only {len(pool)} prints left to batch.')
            if not pool:
                return
            size = len(pool)
        self.status('Looking for the prints that share the most colours…')
        self.root.update_idletasks()
        self.ranked = planner.best_batches(self.parts, pool, size, must)
        self.rank_i = 0
        if not self.ranked:
            self.status('No batch found.')
            return
        self._write(self.ranked[0])

    def try_other(self):
        if self.busy or not self.current:
            return
        old = self.current
        if self.ranked:
            self.rank_i += 1
        if not self.ranked or self.rank_i >= len(self.ranked):
            messagebox.showinfo('Pokémon Batcher', 'No more alternatives — make a new batch instead.')
            return
        self._discard(old, ask=False)
        self._write(self.ranked[self.rank_i])

    def _write(self, ranked):
        nplates, ncol, keys = ranked
        os.makedirs(self.settings.out_dir, exist_ok=True)
        out = os.path.join(self.settings.out_dir, st.batch_name(self.items, keys))
        title = ' + '.join(st.short(self.by_key[k]) for k in keys)
        names = {it['key']: it['name'] for it in self.items}
        self.busy = True
        self._buttons()

        def work():
            lay = []
            try:
                pieces = planner.split(
                    [p for k in keys for p in self.parts[k]], self.index, names,
                    lambda i, n, f: self.ui(self.status, f'Measuring parts {i + 1}/{n}: '
                                                                    f'{os.path.basename(f)} (first time is slow)…'))
                lay = planner.layout(pieces)
                merge.write_batch(out, lay, self.index, title,
                                  lambda i, n: self.ui(self.status, f'Copying meshes {i + 1}/{n}…'))
                err = None
            except Exception as e:  # shown to the user, the app keeps running
                err = f'{type(e).__name__}: {e}'
            self.ui(self._written, out, keys, lay, err)
        threading.Thread(target=work, daemon=True).start()

    def _written(self, out, keys, lay, err):
        self.busy = False
        if err:
            self._buttons()
            self.status('Writing failed.')
            messagebox.showerror('Pokémon Batcher', f'Could not write the batch file:\n{err}')
            return
        plates = [{'colour': c, 'items': sorted({p.item for p, *_ in pl}), 'parts': len(pl)} for c, pl in lay]
        batch = {'file': out, 'keys': keys, 'plates': plates}
        self.state.batches = [b for b in self.state.batches if b['file'] != out] + [batch]
        self.state.save()
        self.current = batch
        self.show_batch(batch)
        self.redraw()
        self.refresh_queue()
        self.status(f'Saved {out}')

    def show_batch(self, b):
        n = len(b['plates'])
        names = ' + '.join(self.by_key[k]['name'] for k in b['keys'] if k in self.by_key)
        self.res_title.configure(text=f'{names}\n{n} plate{"s" if n != 1 else ""}, one colour each')
        c = self.res_canvas
        c.delete('all')
        y = 4
        for i, pl in enumerate(b['plates']):
            h = self.hexes.get(pl['colour'], '#888')
            c.create_text(0, y + 11, text=f'{i + 1:02d}', anchor='w', fill=ACCENT, font=('Segoe UI Semibold', 10))
            c.create_oval(28, y + 4, 42, y + 18, fill=h, outline='#6b6e73')
            c.create_text(50, y + 11, text=pl['colour'], anchor='w', fill=FG, font=FONT)
            who = ', '.join(self.by_key[k]['name'] for k in pl['items'] if k in self.by_key)
            c.create_text(170, y + 11, text=f"{pl['parts']} parts · {who}", anchor='w', fill=DIM,
                          font=('Segoe UI', 9), width=250)
            y += 26
        c.configure(height=y + 4)
        self._buttons()

    def _buttons(self):
        have = self.current is not None and not self.busy
        for b in (self.b_open, self.b_show, self.b_other, self.b_done):
            b.state(['!disabled'] if have else ['disabled'])
        self.go.state(['disabled'] if self.busy else ['!disabled'])

    def open_current(self):
        if self.current:
            self._open(self.current)

    def show_current(self):
        if self.current:
            reveal(self.current['file'])

    def done_current(self):
        if self.current:
            self._done(self.current)

    def _open(self, b):
        exe = self.settings.bambu
        if exe and os.path.exists(exe):
            if sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', exe, b['file']])
            else:
                subprocess.Popen([exe, b['file']])
        else:
            open_path(b['file'])        # whatever opens .3mf files

    def _done(self, b):
        names = ', '.join(self.by_key[k]['name'] for k in b['keys'] if k in self.by_key)
        if not messagebox.askyesno('Pokémon Batcher', f'Mark these as printed?\n\n{names}'):
            return

        def work():
            for k in b['keys']:
                if k in self.by_key:
                    self.state.set_made(self.by_key[k], True)
            self.ui(finish)

        def finish():
            self.state.batches = [x for x in self.state.batches if x['file'] != b['file']]
            self.state.save()
            if self.current and self.current['file'] == b['file']:
                self.current = None
                self.res_title.configure(text='Marked printed. Make the next batch when ready.')
                self.res_canvas.delete('all')
                self.res_canvas.configure(height=10)
            self.ranked = []
            self._buttons()
            self.redraw()
            self.refresh_queue()
            self.status(f'Marked printed: {names}')
        threading.Thread(target=work, daemon=True).start()

    def _discard(self, b, ask=True):
        if ask and not messagebox.askyesno('Pokémon Batcher',
                                           f'Delete this batch file? Its prints go back to the list.\n\n'
                                           f'{os.path.basename(b["file"])}'):
            return
        try:
            os.remove(b['file'])
        except OSError:
            pass
        self.state.batches = [x for x in self.state.batches if x['file'] != b['file']]
        self.state.save()
        if self.current and self.current['file'] == b['file'] and ask:
            self.current = None
            self.res_title.configure(text='Batch deleted.')
            self.res_canvas.delete('all')
            self._buttons()
        self.redraw()
        self.refresh_queue()

    def refresh_queue(self):
        for w in self.qframe.winfo_children():
            w.destroy()
        live = self.state.live_batches() if self.state else []
        if not live:
            ttk.Label(self.qframe, text='None — every batch is printed.', style='Dim.TLabel').pack(anchor='w')
            return
        for b in live:
            row = ttk.Frame(self.qframe, style='Panel.TFrame')
            row.pack(fill='x', pady=3)
            names = ' + '.join(self.by_key[k]['name'] for k in b['keys'] if k in self.by_key)
            lbl = ttk.Label(row, text=f"{names}  ·  {len(b.get('plates', []))} plates", style='Panel.TLabel',
                            wraplength=420, cursor='hand2')
            lbl.pack(anchor='w')
            lbl.bind('<Button-1>', lambda e, b=b: self._select(b))
            bar = ttk.Frame(row, style='Panel.TFrame')
            bar.pack(fill='x', pady=(4, 6))
            ttk.Button(bar, text='Open', command=lambda b=b: self._open(b)).pack(side='left')
            ttk.Button(bar, text='Printed', command=lambda b=b: self._done(b)).pack(side='left', padx=(6, 0))
            ttk.Button(bar, text='Delete', command=lambda b=b: self._discard(b)).pack(side='left', padx=(6, 0))

    def _select(self, b):
        self.current = b
        self.ranked = []
        self.show_batch(b)

    def open_settings(self):
        if not self.busy:
            SettingsDialog(self.root, self.settings, self._settings_saved)

    def _settings_saved(self, rescan):
        if rescan or not self.state:
            self.reload()
        else:
            self.state.refresh_ticks()
            self.ranked = []
            self.redraw()


class SettingsDialog:
    def __init__(self, root, settings, on_save):
        self.s, self.on_save = settings, on_save
        w = self.w = tk.Toplevel(root, bg=BG)
        w.title('Settings')
        w.transient(root)
        w.grab_set()
        f = ttk.Frame(w, padding=18)
        f.pack(fill='both', expand=True)

        ttk.Label(f, text='Folders with your model files', style='H.TLabel').pack(anchor='w')
        ttk.Label(f, text='Every sub-folder and zip inside is searched. Prints are recognised by their '
                          'number and name, e.g. a folder "#0025 Pikachu" or a file '
                          '"0025 - Pikachu - SPLIT Profile.3mf", and balls by "Ball" in a SPLIT file name.',
                  foreground=DIM, wraplength=560).pack(anchor='w', pady=(2, 8))
        lf = ttk.Frame(f)
        lf.pack(fill='x')
        self.lst = tk.Listbox(lf, height=5, bg=PANEL, fg=FG, selectbackground=ACCENT, highlightthickness=0,
                              borderwidth=0, font=FONT, activestyle='none')
        self.lst.pack(side='left', fill='x', expand=True)
        for p in settings.folders:
            self.lst.insert('end', p)
        bf = ttk.Frame(lf)
        bf.pack(side='left', fill='y', padx=(8, 0))
        ttk.Button(bf, text='Add folder…', command=self._add).pack(fill='x')
        ttk.Button(bf, text='Remove', command=self._remove).pack(fill='x', pady=(6, 0))

        self.out = tk.StringVar(value=settings.out_dir)
        self.bambu = tk.StringVar(value=settings.bambu)
        self._path_row(f, 'Save batch files to', self.out, self._pick_out)
        self._path_row(f, 'Bambu Studio', self.bambu, self._pick_bambu)
        self.tick = tk.BooleanVar(value=settings.explorer_tick)
        if tick.SUPPORTED:
            s = ttk.Style()
            s.configure('Dlg.TCheckbutton', background=BG, foreground=FG)
            s.map('Dlg.TCheckbutton', background=[('active', BG)])
            ttk.Checkbutton(f, text='Show a green tick on a printed Pokémon\'s folder in Explorer',
                            variable=self.tick, style='Dlg.TCheckbutton').pack(anchor='w', pady=(14, 0))

        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=(18, 0))
        ttk.Button(bar, text='Save', style='Go.TButton', command=self._save).pack(side='right')
        ttk.Button(bar, text='Cancel', command=w.destroy).pack(side='right', padx=(0, 8))
        w.update_idletasks()
        w.geometry(f'+{root.winfo_rootx() + 80}+{root.winfo_rooty() + 60}')

    def _path_row(self, f, label, var, pick):
        ttk.Label(f, text=label, style='H.TLabel').pack(anchor='w', pady=(16, 4))
        row = ttk.Frame(f)
        row.pack(fill='x')
        ttk.Entry(row, textvariable=var).pack(side='left', fill='x', expand=True, ipady=3)
        ttk.Button(row, text='Browse…', command=pick).pack(side='left', padx=(8, 0))

    def _add(self):
        p = filedialog.askdirectory(parent=self.w, title='Folder with your model files')
        if p:
            p = os.path.normpath(p)
            if p not in self.lst.get(0, 'end'):
                self.lst.insert('end', p)

    def _remove(self):
        for i in reversed(self.lst.curselection()):
            self.lst.delete(i)

    def _pick_out(self):
        p = filedialog.askdirectory(parent=self.w, title='Save batch files to')
        if p:
            self.out.set(os.path.normpath(p))

    def _pick_bambu(self):
        types = [('Bambu Studio', '*.exe')] if sys.platform == 'win32' else []
        p = filedialog.askopenfilename(parent=self.w, title='Bambu Studio', filetypes=types)
        if p:
            self.bambu.set(os.path.normpath(p))

    def _save(self):
        folders = list(self.lst.get(0, 'end'))
        if not folders:
            messagebox.showinfo('Settings', 'Add at least one folder with model files.', parent=self.w)
            return
        out = self.out.get().strip() or config.default_out_dir()
        rescan = folders != self.s.folders or os.path.normcase(out) != os.path.normcase(self.s.out_dir)
        self.s.folders, self.s.out_dir = folders, out
        self.s.bambu = self.bambu.get().strip()
        self.s.explorer_tick = bool(self.tick.get())
        self.s.save()
        self.w.destroy()
        self.on_save(rescan)


def open_path(path):
    if sys.platform == 'win32':
        os.startfile(path)
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path])


def reveal(path):
    """Shows a file selected in the file manager."""
    if sys.platform == 'win32':
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', '-R', path])
    else:
        open_path(os.path.dirname(path))


if __name__ == '__main__':
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # its own taskbar button and icon instead of Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('PokemonBatcher.App')
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
