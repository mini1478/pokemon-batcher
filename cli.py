"""Command-line run of the batcher: python cli.py [N] [key ...]
Prints the best batch and writes it; keys given are forced into the batch."""
import os
import sys
import time

import config
import merge
import planner
import scan
import state

if __name__ == '__main__':
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    must = sys.argv[2:]
    cfg = config.Settings()
    if not cfg.folders:
        sys.exit('No library folders set yet: run app.py once and pick them in Settings.')
    items, index = scan.build_index(cfg.folders, cfg.out_dir)
    st = state.State(items, cfg)
    parts = planner.build_parts(items, index, st.choice)
    pool = [it['key'] for it in items if not st.is_made(it)]
    t = time.time()
    ranked = planner.best_batches(parts, pool, size, must)
    print(f'searched {len(pool)} items in {time.time() - t:.1f}s')
    for plates, ncol, keys in ranked[:8]:
        print(plates, 'plates', ncol, 'colours', keys)
    plates, ncol, keys = ranked[0]
    names = {it['key']: it['name'] for it in items}
    t = time.time()
    pieces = planner.split([p for k in keys for p in parts[k]], index, names,
                           lambda i, n, f: print(f'  measuring {os.path.basename(f)}'))
    print(f'measured {len(pieces)} pieces in {time.time() - t:.1f}s')
    lay = planner.layout(pieces)
    for colour, pl in lay:
        print(f'  {colour:18} {len(pl)} parts  ' + ', '.join(sorted({p.item for p, *_ in pl})))
    out = os.path.join(cfg.out_dir, state.batch_name(items, keys))
    os.makedirs(cfg.out_dir, exist_ok=True)
    t = time.time()
    merge.write_batch(out, lay, index, ' + '.join(keys))
    print(f'wrote {out} ({os.path.getsize(out) / 1e6:.0f} MB) in {time.time() - t:.1f}s')
