# SGI Extractor

`sgix.py` extracts files from SGI IRIX install images — the `*.idb` index plus
its data archives (`*.sw`, `*.man`, and other per-product images). It is a
Python fork of the Go version of this tool at https://github.com/depp/sgix, expanded to handle all IRIX generations (3-6), with versions auto-detected, and aiming for 100% extraction rate.

Tested against IRIX 3.3 (tape images), 4.01, 5.1.1, and 6.5 on Windows so far.  More testing to come on Mac and Linux.

## Requirements

- Python 3.9 or newer. Standard library only for the core.
- A decompressor for LZW (`.Z`) data, in this order of preference:
  - `uncompresspy` (recommended) — a pure-Python module used **in-process**, so
    there's no per-file subprocess. Install with `pip install uncompresspy`
    (on MSYS2: `pip install --break-system-packages uncompresspy`).
  - otherwise `uncompress`, or failing that `gzip` (which also decodes `.Z`),
    invoked as an external command. On MSYS2 there's no `uncompress` package, so
    `pacman -S gzip` covers the fallback.
- Some extracted man pages are still compressed on disk (`.z`, in SGI `pack` or
  `compress` format). Decompress them afterward with `gunzip`/`zcat` (compress
  format) or `pcat`/`unpack` (pack format) — check the first two bytes if unsure
  (`1f 9d` = compress, `1f 1e` = pack).

## Usage

```
sgix.py [--irix {3,4,5,6}] [-v] <file.idb> [<archives...>] [<output dir>]
sgix.py -r <dir> [-o <output dir>] [--irix {3,4,5,6}] [-v]
```

In the common case you only need the `.idb` and an output directory:

```
sgix.py eoe.idb -o outdir
```

The matching data archives (`eoe.sw`, `eoe.man`, and any others the index
references) are found automatically next to the `.idb`. To extract a whole
distribution tree in one pass, point `-r` at its top directory:

```
sgix.py -r IRIX-6.5
```

This finds every `*.idb` under `IRIX-6.5`, auto-discovers each one's data
archives, and extracts them all into a single `IRIX-6.5-output` directory
(override the name with `-o`).

To process several distribution trees at once, loop over them with a shell
glob — each tree gets its own `<tree>-output`, and any tree already extracted
is skipped on a re-run:

```
for d in */; do sgix.py -r "${d%/}"; done
```

Behaviour:

- Arguments may be given in any order. `*.idb`, `*.sw`, and `*.man` are matched
  by suffix; anything else is the output directory.
- The IRIX generation is auto-detected from the archive header; pass `--irix N`
  only to override.
- Pass only the `.idb` (no output dir) to just parse the index and report how
  many entries it contains.
- `-r DIR` recursively finds every `*.idb` under `DIR` and extracts them all
  into one auto-named `<DIR>-output` directory, auto-discovering each idb's data
  archives. The other options still apply; positional files are ignored in this
  mode.
- If the output directory already exists, `sgix.py` prints a message and does
  nothing — it never writes into or appends to an existing target. Remove it to
  re-extract. This applies to both single-file and `-r` runs.

Options:

- `--irix {3,4,5,6}` — force the IRIX generation instead of auto-detecting.
- `-r`, `--recursive DIR` — recursively find every `*.idb` under `DIR` and
  extract them all into one auto-named `<DIR>-output` directory (override the
  name with `-o`). Data archives are auto-discovered next to each idb.
- `-o`, `--out DIR` — output directory. Omit it to run in verify-only mode,
  which checks that every entry lines up with its archive but writes nothing.
- `-v`, `--verbose` — emit a detailed trace (entry parsing, offset math, and
  per-file extraction steps).
- `--idb`, `--sw`, `--man` — name archives explicitly, overriding suffix
  matching and auto-discovery.

## Output summary

After each extraction, `sgix.py` prints a per-package summary comparing what was
written against the index, e.g.:

```
eoe.idb: 9310/9409 files extracted (99%)
  not extracted: write error: 101 (e.g. illegal filename on Windows)
  symlinks: 0 created, 1787 skipped (e.g. no privilege on Windows)
  directories: 288
  paths skipped: 1 (e.g. package root '.')
  510.0 MB in 145.3s, 3.5 MB/s
```

Each summary is also appended to a single log file named after the output
directory, so a `-r` sweep over a whole distribution collects every package's
summary in one place:

```
sgix.py -r /data/IRIX-6.5
# ... all summaries land in /data/IRIX-6.5-output.log
```

Within one run the log accumulates across packages (append, not overwrite).
Since `sgix.py` won't write into an existing output directory, you can't append
to a previous run's log by accident; to redo an extraction, remove both the
`<output>` directory and its `<output>.log` first. Only the summary blocks are
logged; the per-file `skip …` notices stay on stderr. Verify-only mode (no
output dir) writes no log.

## How the IRIX versions differ

`sgix.py` drives all per-version quirks from a single format table, selected by
auto-detection:

- Every entry's archive tag in the `.idb` (`product.image.subsystem`) names the
  data archive that holds it, so a product can span several image files
  (`sw`, `man`, and others like `sw64`, `books`, `src`, `cmplrs_sw`). Each is a
  sibling file `<product>.<image>` and is routed and offset-tracked separately.
  Compressed payloads (`cmpsize`) are inflated as LZW `.Z`.
- **IRIX 3** archives have no header magic: entry data starts at offset 2 with
  no per-entry header. Symlinks in the index are not recreated, and sync or path
  problems are warned about and skipped rather than fatal.
- **IRIX 4 / 5 / 6** archives start with an inst header magic (`imNNNVx00...`);
  the version digit after `V` is what auto-detection reads. Entry data starts at
  offset 13 behind a 2-byte per-entry header, and symlinks are recreated where
  the operating system permits.

## Platform notes

Extract on **macOS or Linux for archival-grade results**; use Windows only for
convenience. The data and the tool are platform-independent, but the Windows
filesystem (NTFS) can't represent some IRIX files faithfully:

- **Illegal characters.** Names containing `:` are rejected by Windows — e.g.
  the Perl module man pages (`Bundle::CPAN.z`, `CGI::Apache.z`, …) and the
  `:saved` mail spool dirs. These are skipped on Windows; they extract fine on a
  case-sensitive Unix filesystem.
- **Case-only collisions.** IRIX ships files that differ only in case, such as
  `usr/gfx/arch/*/libGL.so` vs `libgl.so`, and `app-defaults/Cdplayer` (a file)
  alongside `app-defaults/cdplayer/` (a directory). On case-insensitive NTFS
  these map to one name: one silently overwrites the other, or a file/dir clash
  errors out. On Unix they coexist correctly.
- **Symlinks and device nodes** need privileges (or Developer Mode) on Windows
  and aren't created there; they're handled normally on Unix.

The per-package summary reports how many files were skipped for these reasons,
so a Windows run showing e.g. `9310/9409 (99%)` will typically be a full `100%`
when the same media is extracted on macOS, Linux, or under WSL.

## License

Licensed under the MIT license. See `LICENSE.txt`.

## See also

- Original Go tool: https://github.com/depp/sgix
- Reference IDB format notes: http://persephone.cps.unizar.es/~spd/src/other/mydb.c
