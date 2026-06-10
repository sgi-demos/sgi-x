# SGI Extractor

Extracts files from `*.sw`, `*.idb`, `*.man` install images.  This is a Python fork of https://github.com/depp/sgix, expanded to handle all IRIX releases (3, 4, 5, 6).  The goal with this fork is to be able to extract SGI demo source and man pages, so it has only been tested on install files containing those things. IRIX 4 is currently untested.

## Requirements

- Python 3.9 or newer. Standard library only is needed.
- `uncompress`, automatically used to decompress LZW-compressed (`cmpsize`) entries.
- `gzip`, automatically used to decompress inline `.z` entries on IRIX 5/6.
- `gunzip`, manually used to decompress IRIX 3 `.z` man pages after extraction.

## Usage

```
sgix.py [--irix {3,4,5,6}] [-v] <file.idb> [<file.sw>] [<file.man>] [<output dir>]
```

- Specify arguments in any order.
- Arguments `*.idb`, `*.sw`, and `*.man` are matched automatically by their suffix, and anything else is treated as the output directory.
- Pass only the `.idb` file to parse the index and report how many entries are found.

Options:
- `--irix {3,4,5,6}` — IRIX generation. Defaults to `6`. IRIX 3 needs the `.man` archive in addition to `.sw`.
- `-o`, `--out DIR` — output directory. Omit it entirely to run in verify-only mode, which checks that every entry lines up with its archive but writes nothing.
- `-v`, `--verbose` — emit a detailed trace (entry parsing, offset math, and per-file extraction steps) to stdout.
- Explicit flags (`--idb`, `--sw`, `--man`, `-o/--out`) override the suffix matching if you need them.

## Examples

1. Extract an IRIX 5/6 tardist set from an IRIX ISO or tape image:

```
sgix.py dev.idb dev.sw outdir
```

2. Extract the `gview` demo man page from an IRIX 3.3 tape image:

```
# Tape image from https://fsck.technology/software/Silicon%20Graphics/IRIX%20Install%20Media/SGI%20IRIX%204D1%203.3%20%28Tape%29/Tape%20Images.rar
# Unarchive Tape Images.rar
cd 4d1-3.3-eoe-tape-2
sgix.py --irix 3 eoe2.idb eoe2.sw eoe2.man outdir
cd outdir
find . -name "*gview*" -print
cd usr/catman/u_man/cat6
gunzip -c gview.z > gview-man-page.txt
```

## How the IRIX versions differ

`sgix.py` drives all per-version quirks from a single format table:

- **IRIX 3** splits the data across two archives: `.z` entries are read from the `.man` file and everything else from `.sw`.  The `.z` man pages are copied raw, symlinks in the index are not recreated, and sync or path problems are warned about and skipped rather than treated as fatal. Archive offsets start at 2.
- **IRIX 5 and 6** use a single `.sw` archive, decompress inline `.z` entries with `gzip`, recreate symlinks, and treat any sync or unsafe-path issue as a hard error. Archive offsets start at 13.
- **IRIX 4** is untested and uses the IRIX 5/6 layout.

In every version, an entry's compressed payload (`cmpsize`) is run through `uncompress` when present.

## Further Development
 - Will fix bugs as necessary to extract files needed.
 - A hex editor (like https://hexfiend.com/) is useful for debugging (getting the info from the .idb to match the reality of the .sw and .man files).


## License

Licensed under the MIT license. See `LICENSE.txt`.

## See also

- Original Go tool: https://github.com/depp/sgix
- Reference IDB format notes: http://persephone.cps.unizar.es/~spd/src/other/mydb.c
