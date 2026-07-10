#!/usr/bin/env python3
"""sgix.py — extract IRIX 3/4/5/6 install images (.idb + .sw [+ .man]).
"""

import argparse
import io
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import uncompresspy   # pure-Python LZW (.Z): avoids a subprocess per file
except ImportError:
    uncompresspy = None


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
    archive: str = ""   # routing tag from the idb: 'sw', 'man', or '' if absent
    status: str = ""    # transient extraction outcome, for the summary report


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
    4: Format(entry_header_len=2, initial_offset=13, split_archives=True,
              inline_gzip_z=False, emit_symlinks=True,  strict=True),
    # IRIX 5/6 raw distribution media use the same split, tag-routed SGI inst
    # layout as IRIX 4 (separate <product>.man etc., header magic imNNNVx00).
    # Kept non-strict pending a verified clean run on real 5/6 media.
    5: Format(entry_header_len=2, initial_offset=13, split_archives=True,
              inline_gzip_z=False, emit_symlinks=True,  strict=False),
    6: Format(entry_header_len=2, initial_offset=13, split_archives=True,
              inline_gzip_z=False, emit_symlinks=True,  strict=False),
}

offset_pad=2 # bytes added to the running offset per file entry


def detect_irix(archive_path: str) -> Optional[int]:
    """Infer the IRIX generation from a data archive's header.

    IRIX >= 4 archives begin with an inst magic such as b'im001V400P15'; the
    digit right after 'V' is the major version. IRIX 3 archives have no magic
    (the first entry's path starts at offset 2). Returns 3/4/5/6, or None if
    the file can't be read."""
    try:
        with open(archive_path, 'rb') as f:
            head = f.read(16)
    except OSError:
        return None
    if head[:2] == b'im' and b'V' in head[:10]:
        i = head.index(b'V')
        d = head[i+1:i+2]
        if d.isdigit() and int(d) in FORMATS:
            return int(d)
    return 3  # readable archive, no magic -> old IRIX 3 layout

# IDB parsing -----------------------------------------------------------------

def _split_field(line: bytes) -> tuple[bytes, bytes]:
    i = line.find(b' ')
    if i == -1:
        return line, b''
    return line[:i], line[i+1:]


def _split_field_p(line: bytes) -> tuple[bytes, Optional[bytes], bytes]:
    """Parse `key(value)` or a bare `key`. Returns (key, value-or-None, rest).

    IRIX 6.5 idbs carry op fields like exitop("...shell...") whose quoted value
    contains spaces and nested parentheses, so we can't just split on space or
    grab the first ')'. Scan for the matching ')' at paren-depth 0, ignoring
    anything inside a double-quoted string."""
    x, rest = _split_field(line)
    i = x.find(b'(')
    if i == -1:
        return x, None, rest
    key = line[:i]
    pos = i + 1
    depth = 1
    in_quote = False
    while pos < len(line):
        c = line[pos]
        if in_quote:
            if c == 0x22:            # closing "
                in_quote = False
        elif c == 0x22:              # opening "
            in_quote = True
        elif c == 0x28:              # (
            depth += 1
        elif c == 0x29:              # )
            depth -= 1
            if depth == 0:
                break
        pos += 1
    else:
        raise ValueError("missing ')'")
    value = line[i+1:pos]
    tail = line[pos+1:]
    if not tail:
        rest = b''
    elif tail[:1] == b' ':
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
        e.mode = int(f.strip(b'"'), 8)
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
            # The bare archive tag is <product>.<image>.<subsys> (SGI inst
            # naming). The middle field names the data archive the file lives
            # in (e.g. 'sw', 'man', 'cmplrs_sw', 'data', 'opt', 'hlp'); the
            # file on disk is <product>.<image>. Capture that image name.
            if not e.archive and b'(' not in key:
                parts = key.split(b'.')
                if len(parts) >= 3:
                    e.archive = parts[1].decode('latin-1')
            continue
        if key == b'sum':
            e.sum = int(value.strip(b'"'))
        elif key == b'size':
            e.size = int(value.strip(b'"'))
        elif key == b'cmpsize':
            e.cmpsize = int(value.strip(b'"'))
        elif key == b'symval':
            e.symval = value.strip(b'"').decode()
        elif key not in _KNOWN_BARE:
            # Matches Go: only parenthesised, unrecognised keys are UNKNOWN.
            if verbose:
                vprint(f'UNKNOWN: "{key.decode()}"')
    return e


def read_idb(name: str, fmt: Format, verbose: bool = False) -> list[Entry]:
    offs: dict[str, int] = {}   # per-archive running offset
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
                comp = _component(e, fmt)
                start = offs.get(comp, fmt.initial_offset)
                e.offset = start
                if verbose:
                    vprint(f"            {comp} offset=", start)
                nxt = start + len(e.path) + offset_pad
                if verbose:
                    vprint("                   + path+2=", e.path, nxt)
                nxt += payload
                if verbose:
                    if e.cmpsize > 0:
                        vprint("                   + cmpsize=", e.cmpsize, nxt)
                    else:
                        vprint("                   + size=", e.size, nxt)
                offs[comp] = nxt
            entries.append(e)
    return entries


# Extraction ------------------------------------------------------------------

def _component(e: Entry, fmt: Format) -> str:
    """Which data archive (image) holds entry e's bytes.

    Non-split formats (IRIX 5/6) keep everything in one 'sw' archive. Split
    formats (IRIX 3/4) use the idb's archive tag; when an entry carries no tag
    we fall back to the old heuristic (*.z -> man, else sw)."""
    if not fmt.split_archives:
        return 'sw'
    if e.archive:
        return e.archive
    return 'man' if e.path.endswith('.z') else 'sw'


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


def _decompress_lzw(src, n: int, dst) -> None:
    """Inflate n bytes of LZW '.Z' data (compress format, magic 1f 9d).

    Uses the in-process uncompresspy decoder when available (no per-file
    subprocess spawn, much faster on Windows), else falls back to `uncompress`,
    then `gzip -d` (which also decodes .Z)."""
    data = src.read(n)
    if len(data) != n:
        raise EOFError(f"short read for decompress: wanted {n}, got {len(data)}")
    if uncompresspy is not None:
        dst.write(uncompresspy.LZWFile(io.BytesIO(data)).read())
        return
    for argv in (['uncompress'], ['gzip', '-dc']):
        try:
            subprocess.run(argv, input=data, stdout=dst, stderr=sys.stderr,
                           check=True)
            return
        except FileNotFoundError:
            continue  # this tool isn't installed; try the next one
    raise RuntimeError(
        "need 'uncompress' or 'gzip' on PATH to inflate LZW (.Z) data "
        "(MSYS2: pacman -S gzip)")


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
        e.status = 'exists'
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
        e.status = 'sync-mismatch'
        if verbose:
            print("seek failure!", file=sys.stderr)  # Go: builtin print() -> stderr
        else:
            print(f"skip (sync mismatch): {e.path}", file=sys.stderr)
        return
    if not dest:
        e.status = 'ok'     # verify-only: sync checked, nothing written
        return
    with open(dest, 'wb') as fp:
        if e.cmpsize > 0:
            if verbose:
                vprint("    uncompress ", dest)
            _decompress_lzw(src, e.cmpsize, fp)
        elif fmt.inline_gzip_z and e.path.endswith('.z'):
            if verbose:
                vprint("gzip -d ", e.path)
            _run_pipe(['gzip', '-d'], src, e.size, fp)
        else:
            _copy_n(src, fp, e.size)
    e.status = 'ok'


def _extract_dir(dest: str, verbose: bool) -> None:
    if verbose:
        vprint("extractDirectory ", dest)
    if dest and not os.path.exists(dest):
        os.mkdir(dest, 0o777)


_symlinks_blocked = False   # set once a symlink attempt fails (e.g. no privilege)


def _extract_link(e: Entry, dest: str, fmt: Format) -> None:
    global _symlinks_blocked
    if e.status:            # already attempted in an earlier archive pass
        return
    if not fmt.emit_symlinks:
        e.status = 'link-disabled'
        return
    if not dest or not e.symval:
        e.status = 'link-skip'
        return
    if _symlinks_blocked:    # a prior attempt failed; don't retry (and don't stall)
        e.status = 'link-skip'
        return
    if os.path.lexists(dest):
        e.status = 'link-ok'
        return
    try:
        os.symlink(e.symval, dest)
        e.status = 'link-ok'
    except OSError as ex:
        # e.g. Windows without symlink privilege (WinError 1314). Not fatal -
        # skip this link and, since the cause is usually environment-wide, skip
        # all remaining symlink attempts too (each failed call is slow).
        e.status = 'link-skip'
        _symlinks_blocked = True
        print(f"symlink creation failed ({ex}); skipping all remaining symlinks",
              file=sys.stderr)


def _extract_entry(e: Entry, src, outdir: str, fmt: Format,
                   archive: str, verbose: bool) -> None:
    if e.ty == 'f' and fmt.split_archives:
        if _component(e, fmt) != archive:
            if verbose:
                vprint("skip ", e.path)
            return
        if verbose:
            vprint("extract ", e.path)
    name = os.path.normpath(e.path)
    if not _is_safe_path(name):
        # '.' (package root) and any odd path: skip, never fatal. We never write
        # outside outdir, so this is safe even in strict mode.
        if not e.status:        # warn once, not once per archive pass
            if verbose:
                vprint("skip invalid path", e.path)
            else:
                print(f"skip invalid path: {e.path}", file=sys.stderr)
        e.status = 'skip-path'
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
        e.status = 'dir'
    elif e.ty == 'l':
        _extract_link(e, dest, fmt)
    else:
        # Device nodes (c/b), fifos (p), sockets (s): no archive payload and
        # not creatable without mknod/privileges. Skip, never abort.
        if not e.status:
            print(f"skip {e.ty}-node {e.path}", file=sys.stderr)
        e.status = 'special'


def extract(entries: list[Entry], sources: list[tuple[str, str]],
            outdir: str, fmt: Format, verbose: bool = False) -> None:
    """sources: list of (archive-image, filepath) to read, in pass order."""
    global _symlinks_blocked
    _symlinks_blocked = False
    seen_collisions: set[str] = set()
    seen_write_errors: set[tuple[str, object]] = set()
    for archive, path_ in sources:
        if verbose:
            print(f"EXTRACT: Opening file: {path_}", file=sys.stderr)
        with open(path_, 'rb') as src:
            for e in entries:
                try:
                    _extract_entry(e, src, outdir, fmt, archive, verbose)
                except FileExistsError as ex:
                    # A path component needed as a directory already exists as a
                    # file -- on a case-insensitive filesystem this is the
                    # Cdplayer-file vs cdplayer/-dir clash. Many entries share
                    # the same colliding parent, so warn once per parent.
                    fn = getattr(ex, 'filename', None) or e.path
                    if fn not in seen_collisions:
                        seen_collisions.add(fn)
                        print(f"skip (name collision) {fn}: a file with this name "
                              f"already exists (case-insensitive filesystem); "
                              f"entries under it were skipped", file=sys.stderr)
                    e.status = e.status or 'write-error'
                except OSError as ex:
                    # Other per-file filesystem failure (e.g. illegal name on
                    # Windows: ':' in 'usr/mail/:saved', or '::' in the Perl man
                    # pages). Group by parent dir + reason so a whole directory
                    # of the same problem collapses to one line, not dozens.
                    parent = os.path.dirname(e.path)
                    key = (parent, getattr(ex, 'errno', None))
                    if key not in seen_write_errors:
                        seen_write_errors.add(key)
                        reason = ex.strerror or str(ex)
                        where = f"{parent}/" if parent else e.path
                        print(f"skip (write error) under {where}: {reason} "
                              f"(e.g. {os.path.basename(e.path)})",
                              file=sys.stderr)
                    e.status = e.status or 'write-error'
                except Exception as ex:
                    raise RuntimeError(f"{e.path}: {ex}") from ex


# Reporting -------------------------------------------------------------------

def _summarize(idb: str, entries: list[Entry], missing: list[str],
               fmt: Format, extracted: bool = True, elapsed: float = 0.0,
               log_path: str = '') -> None:
    files = [e for e in entries if e.ty == 'f']
    dirs = [e for e in entries if e.ty == 'd']
    links = [e for e in entries if e.ty == 'l']

    ok = sum(1 for e in files if e.status in ('ok', 'exists'))
    sync = sum(1 for e in files if e.status == 'sync-mismatch')
    # A file with no status was never reached: its archive image was missing,
    # or a hard error stopped extraction before it.
    noarch = sum(1 for e in files if e.status == '')
    pct = (100.0 * ok / len(files)) if files else 100.0
    # Bytes actually written this run (uncompressed size of freshly-written files).
    nbytes = sum(e.size for e in files if e.status == 'ok')

    link_ok = sum(1 for e in links if e.status == 'link-ok')
    link_skip = sum(1 for e in links if e.status in ('link-skip', 'link-disabled'))
    badpath = sum(1 for e in entries if e.status == 'skip-path')
    special = sum(1 for e in entries if e.status == 'special')
    werr = sum(1 for e in entries if e.status == 'write-error')

    name = os.path.basename(idb)
    verb = 'extracted' if extracted else 'verified'
    lines = [f"{name}: {ok}/{len(files)} files {verb} ({pct:.0f}%)"]

    # Only list non-empty "didn't extract" categories, to keep it terse.
    notes = []
    if noarch:
        if missing:
            notes.append(f"not reached: {noarch} (missing archive: {', '.join(missing)})")
        else:
            notes.append(f"not reached: {noarch} (stopped early?)")
    if sync:
        notes.append(f"sync mismatch: {sync}")
    if werr:
        notes.append(f"write error: {werr} (e.g. illegal filename on Windows)")
    if notes:
        lines.append(f"  not extracted: {'; '.join(notes)}")
    if links:
        seg = f"  symlinks: {link_ok} created"
        if link_skip:
            seg += f", {link_skip} skipped (e.g. no privilege on Windows)"
        lines.append(seg)
    if dirs:
        lines.append(f"  directories: {len(dirs)}")
    if badpath:
        lines.append(f"  paths skipped: {badpath} (e.g. package root '.')")
    if special:
        lines.append(f"  device/special nodes skipped: {special}")
    if extracted and elapsed > 0:
        mb = nbytes / 1_000_000
        rate = f", {mb / elapsed:.1f} MB/s" if elapsed > 0 else ""
        lines.append(f"  {mb:.1f} MB in {elapsed:.1f}s{rate}")
    # On Windows, flag anything that couldn't be fully captured here.
    incomplete = (ok < len(files)) or link_skip or special
    if extracted and os.name == 'nt' and incomplete:
        lines.append("  WARNING: Run on Mac or Linux for best archive "
                     "extraction; Windows filesystem limitations prevent full "
                     "extraction (e.g. case-insensitive filenames, ':' in "
                     "names, symlinks).")

    for ln in lines:
        print(ln)
    # Append the same block to a single per-output-directory log, so a sweep
    # (e.g. find -exec) accumulates every package's summary in one place.
    if log_path:
        try:
            with open(log_path, 'a', encoding='utf-8') as lf:
                lf.write('\n'.join(lines) + '\n\n')
        except OSError as ex:
            print(f"warning: could not write log {log_path}: {ex}",
                  file=sys.stderr)


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
        epilog=('examples:\n'
                '  extract an .idb    sgix.py eoe.idb -o outdir\n'
                '  extract a tree     find . -name "*.idb" -print -exec sgix.py {} -o outdir \\;'),
                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--irix', type=int, choices=[3, 4, 5, 6], default=None,
                   help='IRIX generation (default: auto-detect from archive header)')
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

    idb_dir = os.path.dirname(idb)
    stem = os.path.basename(idb)
    stem = stem[:-4] if stem.endswith('.idb') else os.path.splitext(stem)[0]

    # Pick the IRIX generation: explicit --irix wins; otherwise sniff a sibling
    # data archive's header. Probe an explicit/likely archive first, else any
    # <stem>.<image> file next to the idb.
    irix = ns.irix
    if irix is None:
        probe = None
        for cand in (sw, man, os.path.join(idb_dir, stem + '.sw'),
                     os.path.join(idb_dir, stem + '.man')):
            if cand and os.path.exists(cand):
                probe = cand
                break
        if probe is None and os.path.isdir(idb_dir or '.'):
            for fn in sorted(os.listdir(idb_dir or '.')):
                if fn.startswith(stem + '.') and not fn.endswith('.idb'):
                    probe = os.path.join(idb_dir, fn)
                    break
        if probe:
            irix = detect_irix(probe) or 6
            if ns.verbose:
                print(f'detected IRIX {irix} from {os.path.basename(probe)}',
                      file=sys.stderr)
        else:
            irix = 6  # no archive to sniff (parse-only); version is moot here
    fmt = FORMATS[irix]

    if ns.verbose:
        # Reproduce the reference Go tool's header (printed before readIDB).
        vprint("INFO: idb = ", idb or "", "\nsw = ", sw or "",
            "\nman = ", man or "", "\noutput = ", outdir or "")

    try:
        entries = read_idb(idb, fmt, ns.verbose)
    except (OSError, ValueError) as ex:
        print(f'error: {ex}', file=sys.stderr)
        return 1

    # Work out which data archives this idb references. Each entry's archive
    # image maps to a sibling file <stem>.<image> next to the idb (SGI inst
    # naming, e.g. dev.sw, dev.man, dev.cmplrs_sw). Explicit --sw/--man override
    # the 'sw'/'man' images. Read 'sw' first, then 'man', then the rest.
    explicit = {}
    if sw:
        explicit['sw'] = sw
    if man:
        explicit['man'] = man

    # Bare `.idb` with no output dir and no named archive: just report the count.
    if not outdir and not explicit:
        if not ns.verbose:
            print(f'parsed {len(entries)} entries from {idb}')
        return 0

    used: list[str] = []
    for e in entries:
        if e.ty == 'f':
            c = _component(e, fmt)
            if c not in used:
                used.append(c)
    used.sort(key=lambda c: (c != 'sw', c != 'man', c))

    sources: list[tuple[str, str]] = []
    missing: list[str] = []
    for c in used:
        fp = explicit.get(c) or os.path.join(idb_dir, f'{stem}.{c}')
        if os.path.exists(fp):
            sources.append((c, fp))
        else:
            missing.append(c)

    if not sources:
        print(f'error: found no data archives next to {idb} '
              f'(looked for {stem}.<image>: {", ".join(missing)})',
              file=sys.stderr)
        return 1

    if missing:
        print(f'warning: no data archive for image(s) {", ".join(missing)} '
              f'(looked for {stem}.<image>); those files were skipped',
              file=sys.stderr)

    if outdir:
        os.makedirs(outdir, exist_ok=True)

    if ns.verbose:
        vprint("RUNNING EXTRACT", sw or "", man or "", outdir or "", "\n")
    elif outdir:
        print(f'Extracting to {outdir} (irix {irix})...')
    else:
        print(f'Verifying (irix {irix})...')

    err = None
    t0 = time.monotonic()
    try:
        extract(entries, sources, outdir or '', fmt, ns.verbose)
    except (OSError, RuntimeError, EOFError) as ex:
        err = ex  # report after the summary, so the summary always prints
    elapsed = time.monotonic() - t0

    # One log per output directory (not per idb): "<output_dir>.log" beside the
    # directory, appended to across a sweep so every package lands in one file.
    log_path = (os.path.normpath(outdir) + '.log') if outdir else ''
    _summarize(idb, entries, missing, fmt, extracted=bool(outdir),
               elapsed=elapsed, log_path=log_path)

    if err:
        print(f'error: {err}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
