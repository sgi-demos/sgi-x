# SGI Extractor

Extracts from `*.sw`, `*.idb`, etc. sets of SGI install files.  This is a fork of https://github.com/depp/sgix, that aims to expand handling of install files to all major IRIX releases (3, 4, 5, 6).  The goal with this fork is to be able to extract SGI demo source and man pages, so it has only been tested on install files containing those things. IRIX 4 is untested, it may work with the IRIX 5 & 6 version of sgi-x.

## Build

1. Install Go.
```
brew install go
```

2a. For IRIX 5 & 6, build the top-level Go files:

```
go get
go build
```

2b. For IRIX 3, build sgi-x specific version from irix3 dir:
```
cd irix3
go get
go build
```

Note: IRIX 4 is untested, it may work with the IRIX 5 & 6 version of sgi-x.

## Extract

```
Usage: sgix <file.idb> [<file.sw> [<file.man>] [<output dir>]] 
```

1. General example: Let's say you have a `*.sw` and `*.idb` file. It's an extracted "tardist" file from an SGI IRIX iso or tape image. Do this:
```
sgix dev.idb dev.sw outdir
```

2. Specific example using IRIX 3.3 tape image to obtain the gview demo man page:
```
# Tape image from https://fsck.technology/software/Silicon%20Graphics/IRIX%20Install%20Media/SGI%20IRIX%204D1%203.3%20%28Tape%29/Tape%20Images.rar
# Unarchive Tape Images.rar
cd 4d1-3.3-eoe-tape-2
sgi-x/irix3/sgix . eoe2.idb eoe2.sw eoe2.man outdir
cd outdir
find . -name "*gview*" -print
cd usr/catman/u_man/cat6
gunzip -c gview.z > gview-man-page.txt
```

This will create a folder called `outdir` with the extracted contents.

## Further Development

 - Really should unify the binary and test extraction for all IRIX releases 3-6.
 - A hex editor (like https://hexfiend.com/) is useful for debugging (getting the info from the .idb to match the reality of the .sw and .man files).

## License

Licensed under the MIT license. See `LICENSE.txt`.

## See Also

 - http://persephone.cps.unizar.es/~spd/src/other/mydb.c
 - https://github.com/depp/sgix
