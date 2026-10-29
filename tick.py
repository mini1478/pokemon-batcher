"""Windows only: a green tick on a printed Pokemon's folder in Explorer.

The folder gets a desktop.ini that points its icon at printed.ico and carries
the line 'PokemonPrinted=1', which is also how the app reads the tick back.
Unticking removes only what was added here, and the folder's Date modified is
put back afterwards."""
import ctypes
import os
import sys

MARKER = 'PokemonPrinted=1'
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'printed.ico')
SUPPORTED = sys.platform == 'win32'

_NORMAL, _READONLY, _HIDDEN_SYSTEM = 0x80, 0x01, 0x02 | 0x04


def ticked(folder):
    try:
        with open(os.path.join(folder, 'desktop.ini'), encoding='utf-16') as f:
            return any(line.strip() == MARKER for line in f)
    except (OSError, UnicodeError):
        return False


def _attrs(path, value):
    ctypes.windll.kernel32.SetFileAttributesW(str(path), value)


def _folder_readonly(folder, on):
    a = ctypes.windll.kernel32.GetFileAttributesW(str(folder))
    if a != -1 and a != 0xFFFFFFFF:
        _attrs(folder, (a | _READONLY) if on else (a & ~_READONLY))


def set_ticked(folder, on):
    if not SUPPORTED or ticked(folder) == on:
        return
    ini = os.path.join(folder, 'desktop.ini')
    stamp = os.stat(folder)
    lines = []
    if os.path.exists(ini):
        try:
            with open(ini, encoding='utf-16') as f:
                lines = f.read().splitlines()
        except UnicodeError:
            with open(ini, encoding='mbcs', errors='replace') as f:
                lines = f.read().splitlines()
        _attrs(ini, _NORMAL)        # a hidden file cannot be overwritten
    if on:
        if '[.ShellClassInfo]' not in lines:
            lines.insert(0, '[.ShellClassInfo]')
        lines = [ln for ln in lines if not ln.startswith('IconResource=')]
        at = lines.index('[.ShellClassInfo]') + 1
        lines[at:at] = [f'IconResource={ICON},0', MARKER]
    else:
        lines = [ln for ln in lines if ln != MARKER
                 and not (ln.startswith('IconResource=') and 'printed.ico' in ln.lower())]
    if any(ln.strip() and ln.strip() != '[.ShellClassInfo]' for ln in lines):
        with open(ini, 'w', encoding='utf-16', newline='\r\n') as f:
            f.write('\n'.join(lines) + '\n')
        _attrs(ini, _HIDDEN_SYSTEM)
        _folder_readonly(folder, True)      # Explorer only reads desktop.ini of read-only folders
    else:
        if os.path.exists(ini):
            os.remove(ini)
        _folder_readonly(folder, False)
    os.utime(folder, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    # SHCNE_UPDATEITEM alone leaves Explorer's cached icon; SHCNE_ASSOCCHANGED makes it redraw
    shell = ctypes.windll.shell32
    shell.SHChangeNotify(0x00002000, 0x0005, ctypes.c_wchar_p(folder), None)
    shell.SHChangeNotify(0x08000000, 0x0000, None, None)
