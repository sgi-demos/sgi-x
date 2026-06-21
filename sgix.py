#!/usr/bin/env python3
"""sgix.py — extract IRIX 3/4/5/6 install images (.idb + .sw [+ .man]).
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


def vprint(*args) -> None:
    sys.stdout.write(" ".join(str(a) for a in args) + "\n")


@dataclass
class Entry:
    ty: str = ""
    mode: int = 0
    user: str = ""
    group: str = ""
    path: str = ""
    sum: int = 0
    size: int = 0
    cmpsize: int = 0
    offset: int = 0
    symval: str = ""


@dataclass
class Format:
    entry_header_len: int   # bytes read+skipped before the path at EXTRACTION time
    initial_offset: int     # byte offset of the first entry's header
    split_archives: bool    # IRIX 3: route *.z to .man, everything else to .sw
    inline_gzip_z: bool     # IRIX 5/6: *.z files are gzip-compressed inline in .sw
    emit_symlinks: bool     # IRIX 3 symlink targets aren't trustworthy here
    strict: bool            # error on sync/path issues vs. warn-and-skip


# IRIX 4 is untested; default it to the v5/6 layout per README.
FORMATS: dict[int, Format] = {
    3: Format(entry_header_len=0, initial_offset=2,  split_archives=True,
              inline_gzip_z=False, emit_symlinks=False, strict=False),
    4: Format(entry_header_len=2, initial_offset=13, split_archives=False,
              inline_gzip_z=True,  emit_symlinks=True,  strict=True),
    5: Format(entry_header_len=2, initial_offset=13, split_archives=False,
              inline_gzip_z=True,  emit_symlinks=True,  strict=True),
    6: Format(entry_header_len=2, initial_offset=13, split_archives=False,
              inline_gzip_z=True,  emit_symlinks=True,  strict=True),
}

offset_pad=2 # bytes added to the running offset per file entry

# IDB parsing -----------------------------------------------------------------

def _split_field(line: bytes) -> tuple[bytes, bytes]:
    i = line.find(b' ')
    if i == -1:
        return line, b''
    return line[:i], line[i+1:]


def _split_field_p(line: bytes) -> tuple[bytes, Optional[bytes], bytes]:
    """Parse `key(value)` or a bare `key`. Returns (key, value-or-None, rest)."""
    x, rest = _split_field(line)
    i = x.find(b'(')
    if i == -1:
        return x, None, rest
    key = line[:i]
    after = line[i+1:]
    j = after.find(b')')
    if j == -1:
        raise ValueError("missing ')'")
    value = after[:j]
    tail = after[j+1:]
    if not tail:
        rest = b''
    elif tail.startswith(b' '):
        rest = tail[1:]
    else:
        raise ValueError("no space after ')'")
    return key, value, rest


_KNOWN_BARE = {b'f', b'exitop', b'nohist', b'nostrip', b'mach', b'postop', b'config'}


def _parse_entry(line: bytes, verbose: bool) -> Optional[Entry]:
    f, line = _split_field(line)
    if not f or f[0] == 0:
        return None
    if len(f) != 1 or not (0x61 <= f[0] <= 0x7a):
        raise ValueError(f"invalid type: {f!r}")
    e = Entry(ty=f.decode())
    f, line = _split_field(line)
    try:
        e.mode = int(f, 8)
    except ValueError:
        raise ValueError(f"invalid mode: {f!r}")
    f, line = _split_field(line); e.user = f.decode()
    f, line = _split_field(line); e.group = f.decode()
    f, line = _split_field(line); e.path = f.decode()
    _, line = _split_field(line)  # checksum field; matches Go's discarded field
    if verbose:
        vprint("parseEntry:", e.mode, e.user, e.group, e.path)
    while line:
        key, value, line = _split_field_p(line)
        if verbose:
            # Go prints every field token here (before the nil-value continue),
            # 11-space literal + Println's separator == 12 leading columns.
            vprint("           ", key.decode(),
                "" if value is None else value.decode())
        if value is None:
            continue
        if key == b'sum':
            e.sum = int(value)
        elif key == b'size':
            e.size = int(value)
        elif key == b'cmpsize':
            e.cmpsize = int(value)
        elif key == b'symval':
            e.symval = value.decode()
        elif key not in _KNOWN_BARE:
            # Matches Go: only parenthesised, unrecognised keys are UNKNOWN.
            if verbose:
                vprint(f'UNKNOWN: "{key.decode()}"')
    return e


def read_idb(name: str, fmt: Format, verbose: bool = False) -> list[Entry]:
    sw_off = man_off = fmt.initial_offset
    entries: list[Entry] = []
    with open(name, 'rb') as fp:
        for lineno, raw in enumerate(fp, 1):
            line = raw.rstrip(b'\r\n')
            if not line or line[0] == 0:
                continue
            try:
                e = _parse_entry(line, verbose)
            except ValueError as ex:
                raise ValueError(f"{name}:{lineno}: {ex}") from ex
            if e is None:
                continue
            if e.ty == 'f':
                payload = (e.cmpsize if e.cmpsize > 0 else e.size)
                is_z = fmt.split_archives and e.path.endswith('.z')
                if verbose:
                    vprint("            .sw  offset=", sw_off)
                    vprint("            .man offset=", man_off)
                    if is_z:
                        vprint("            using .man offset")
                start = man_off if is_z else sw_off
                e.offset = start
                if verbose:
                    vprint("            offset start=", start)
                nxt = start + len(e.path) + offset_pad
                if verbose:
                    vprint("                   + path+2=", e.path, nxt)
                nxt += payload
                if verbose:
                    if e.cmpsize > 0:
                        vprint("                   + cmpsize=", e.cmpsize, nxt)
                    else:
                        vprint("                   + size=", e.size, nxt)
                if is_z:
                    man_off = nxt
                else:
                    sw_off = nxt
            entries.append(e)
    return entries


# Extraction ------------------------------------------------------------------

def _is_safe_path(name: str) -> bool:
    for part in name.split('/'):
        if part == '' or part == '.' or part == '..':
            return False
    return True


def _run_pipe(argv: list[str], src, n: int, dst) -> None:
    """Stream exactly n bytes from src through argv, writing stdout to dst."""
    data = src.read(n)
    if len(data) != n:
        raise EOFError(f"short read for {argv[0]}: wanted {n}, got {len(data)}")
    subprocess.run(argv, input=data, stdout=dst, stderr=sys.stderr, check=True)


def _copy_n(src, dst, n: int, chunk: int = 1 << 16) -> None:
    while n > 0:
        buf = src.read(min(chunk, n))
        if not buf:
            raise EOFError("short read")
        dst.write(buf)
        n -= len(buf)


def _extract_file(e: Entry, src, dest: str, fmt: Format, verbose: bool) -> None:
    if verbose:
        vprint("extractFile ", dest)
    if dest and os.path.lexists(dest):
        if verbose:
            vprint("already exists ", dest)
        return
    src.seek(e.offset)
    header = src.read(fmt.entry_header_len + len(e.path))
    # The last len(path) bytes of the header should equal the entry's path.
    tail = header[fmt.entry_header_len:]
    if verbose:
        vprint("   seeked to ", e.offset, " and found ",
            tail.decode('latin-1'), " required= ", e.path)
    if tail != e.path.encode():
        msg = f"out of sync at offset {e.offset}: wanted {e.path!r}, got {tail!r}"
        if fmt.strict:
            raise RuntimeError(msg)
        if verbose:
            print("seek failure!", file=sys.stderr)  # Go: builtin print() -> stderr
        else:
            print(f"skip (sync mismatch): {e.path}", file=sys.stderr)
        return
    if not dest:
        return
    with open(dest, 'wb') as fp:
        if e.cmpsize > 0:
            if verbose:
                vprint("    uncompress ", dest)
            _run_pipe(['uncompress'], src, e.cmpsize, fp)
            return
        if fmt.inline_gzip_z and e.path.endswith('.z'):
            if verbose:
                vprint("gzip -d ", e.path)
            _run_pipe(['gzip', '-d'], src, e.size, fp)
            return
        _copy_n(src, fp, e.size)


def _extract_dir(dest: str, verbose: bool) -> None:
    if verbose:
        vprint("extractDirectory ", dest)
    if dest and not os.path.exists(dest):
        os.mkdir(dest, 0o777)


def _extract_link(e: Entry, dest: str, fmt: Format) -> None:
    if not fmt.emit_symlinks or not dest or not e.symval:
        return
    if os.path.lexists(dest):
        return
    os.symlink(e.symval, dest)


def _extract_entry(e: Entry, src, outdir: str, fmt: Format,
                   is_man_archive: bool, verbose: bool) -> None:
    if fmt.split_archives and e.ty == 'f':
        in_man = e.path.endswith('.z')
        if in_man != is_man_archive:
            if verbose:
                vprint("skip ", e.path)
            return
        if verbose:
            vprint("extract ", e.path)
    name = os.path.normpath(e.path)
    if not _is_safe_path(name):
        if fmt.strict:
            raise RuntimeError(f"invalid path: {e.path}")
        if verbose:
            vprint("skip invalid path", e.path)
        else:
            print(f"skip invalid path: {e.path}", file=sys.stderr)
        return
    dest = ""
    if outdir:
        dest = os.path.join(outdir, name)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
    if e.ty == 'f':
        _extract_file(e, src, dest, fmt, verbose)
    elif e.ty == 'd':
        _extract_dir(dest, verbose)
    elif e.ty == 'l':
        _extract_link(e, dest, fmt)
    else:
        raise RuntimeError(f"unknown entry type: {e.ty!r}")


def extract(entries: list[Entry], sw_file: Optional[str], man_file: Optional[str],
            outdir: str, fmt: Format, verbose: bool = False) -> None:
    sources: list[tuple[str, bool]] = []
    if sw_file:
        sources.append((sw_file, False))
    if fmt.split_archives and man_file:
        sources.append((man_file, True))
    if not sources:
        return
    for path_, is_man in sources:
        if verbose:
            print(f"EXTRACT: Opening file: {path_}", file=sys.stderr)
        with open(path_, 'rb') as src:
            for e in entries:
                try:
                    _extract_entry(e, src, outdir, fmt, is_man, verbose)
                except Exception as ex:
                    raise RuntimeError(f"{e.path}: {ex}") from ex


# CLI -------------------------------------------------------------------------

def _classify_positional(args: list[str]) -> tuple[Optional[str], Optional[str],
                                                    Optional[str], Optional[str]]:
    idb = sw = man = outdir = None
    for a in args:
        if a.endswith('.idb'):
            idb = a
        elif a.endswith('.sw'):
            sw = a
        elif a.endswith('.man'):
            man = a
        else:
            outdir = a
    return idb, sw, man, outdir


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog='sgix.py',
        description='Extract SGI IRIX 3/4/5/6 install images (.idb + .sw [+ .man]).',
        epilog=('Positional files are recognized by suffix (.idb, .sw, .man); '
                'anything else is treated as the output directory. Explicit '
                'flags (--idb/--sw/--man/-o) override positional matches.'))
    p.add_argument('--irix', type=int, choices=[3, 4, 5, 6], default=6,
                   help='IRIX generation (default: 6)')
    p.add_argument('--idb', help='IDB index file')
    p.add_argument('--sw', help='.sw data archive')
    p.add_argument('--man', help='.man data archive (IRIX 3 only)')
    p.add_argument('-o', '--out', help='output directory (omit to verify only)')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('files', nargs='*',
                   help='positional files matched by suffix; non-matching arg is outdir')
    ns = p.parse_args(argv)

    idb, sw, man, outdir = _classify_positional(ns.files)
    idb = ns.idb or idb
    sw = ns.sw or sw
    man = ns.man or man
    outdir = ns.out or outdir

    if not idb:
        print('error: no .idb file specified', file=sys.stderr)
        return 1

    fmt = FORMATS[ns.irix]

    # If the data archives weren't named, look for siblings next to the .idb
    # (e.g. eoe1.idb -> eoe1.sw / eoe1.man). Lets a bare .idb path extract,
    # which is what `find ... -exec sgix.py --irix 3 {} --out dir` expects.
    # Only when extracting, so a bare `.idb` with no --out still just parses.
    if outdir:
        stem = idb[:-4] if idb.endswith('.idb') else os.path.splitext(idb)[0]
        if not sw and os.path.exists(stem + '.sw'):
            sw = stem + '.sw'
        if not man and os.path.exists(stem + '.man'):
            man = stem + '.man'

    if fmt.split_archives and sw and not man:
        print('warning: --irix 3 typically needs a .man archive too',
              file=sys.stderr)

    if ns.verbose:
        # Reproduce the reference Go tool's header (printed before readIDB).
        vprint("INFO: idb = ", idb or "", "\nsw = ", sw or "",
            "\nman = ", man or "", "\noutput = ", outdir or "")

    try:
        entries = read_idb(idb, fmt, ns.verbose)
    except (OSError, ValueError) as ex:
        print(f'error: {ex}', file=sys.stderr)
        return 1

    if not sw and not man:
        if not ns.verbose:
            print(f'parsed {len(entries)} entries from {idb}')
        return 0

    if outdir:
        os.makedirs(outdir, exist_ok=True)

    if ns.verbose:
        vprint("RUNNING EXTRACT", sw or "", man or "", outdir or "", "\n")
    elif outdir:
        print(f'Extracting to {outdir} (irix {ns.irix})...')
    else:
        print(f'Verifying (irix {ns.irix})...')

    try:
        extract(entries, sw, man, outdir or '', fmt, ns.verbose)
    except (OSError, RuntimeError, EOFError) as ex:
        print(f'error: {ex}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
