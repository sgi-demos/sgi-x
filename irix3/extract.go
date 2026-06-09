package main

import (
	//"bytes"
	//"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path"
	"strings"
)

func isSafePath(name string) bool {
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

func extractFile(e entry, src *os.File, dest string) error {

	fmt.Println("extractFile ", dest)

	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		fmt.Println("already exists ", dest)
		return nil
	}

	if _, err := src.Seek(int64(e.offset), io.SeekStart); err != nil {
		return err
	}

	buf := make([]byte, len(e.path)+0) // MAGIC: was 2
	if _, err := src.Read(buf); err != nil {
		return nil
	}

	fmt.Println("   seeked to ", e.offset, " and found ", string(buf), " required= ", e.path)
	if string(buf) != e.path {
		// return errors.New("seek failure", string(buf), e.path)
		print ("seek failure!\n")
		return nil
	}

	if dest == "" {
		return nil
	}

	fp, err := os.Create(dest)
	if err != nil {
		return err
	}

	if e.cmpsize > 0 {
		fmt.Println("    uncompress ", dest)
		exe := exec.Command("uncompress")
		exe.Stdin = &io.LimitedReader{R: src, N: int64(e.cmpsize)}
		exe.Stdout = fp
		exe.Stderr = os.Stderr
		return exe.Run()
	}

	// if e.path[len(e.path)-2:] == ".z" {
	// 	fmt.Println("gzip -d ", e.path)
	// 	exe := exec.Command("gzip", "-d")
	// 	exe.Stdin = &io.LimitedReader{R: src, N: int64(e.size)}
	// 	exe.Stdout = fp
	// 	exe.Stderr = os.Stderr
	// 	return exe.Run()
	// }

	_, err = io.CopyN(fp, src, int64(e.size))
	if err != nil {
		return err
	}
	err = fp.Close()
	return err
}

func extractDirectory(e entry, dest string) error {
	fmt.Println("extractDirectory ", dest)
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		return nil
	}

	if dest == "" {
		return nil
	}
	return os.Mkdir(dest, 0777)
}

func extractLink(e entry, dest string) error {
	return nil

	// if dest == "" {
	// 	return nil
	// }
	// fmt.Println("extractLink ",e.symval, dest)
	// return os.Symlink(e.symval, dest)
}

func extractEntry(e entry, src *os.File, dest string, isManFile bool) error {

	// skip man files for now
	pathLen := len(e.path)
	if e.ty == 'f' {
		if (e.path[pathLen-2:] == ".z" && !isManFile) || (e.path[pathLen-2:] != ".z" && isManFile) {
			fmt.Println("skip ", e.path)
			return nil
		} else {
			fmt.Println("extract ", e.path)
		}
	}


	name := path.Clean(e.path)
	println("extractEntry paths:\n",name,e.path)
	if !isSafePath(name) {
		fmt.Println("skip invalid path", e.path)
		//return errors.New("invalid path")
		return nil
	}

	if dest != "" {
		dest = path.Join(dest, name)
		if err := os.MkdirAll(path.Dir(dest), 0777); err != nil {
			return err
		}
	}
	switch e.ty {
	case 'f':
		return extractFile(e, src, dest)
	case 'd':
		return extractDirectory(e, dest)
	case 'l':
		return extractLink(e, dest)
	default:
		return fmt.Errorf("unknown type: %q", e.ty)
	}
}

func extract(entries []entry, swFile, manFile, outDir string) error {
	var fps = [...]*os.File{nil, nil}
	var files = [...]string{swFile, manFile}

	for i := 0; i < 2; i++ {
		println("EXTRACT: Opening file:",files[i],"\n");
		fp, err := os.Open(files[i])
		if err != nil {
			return err
		}
		fps[i] = fp
		defer fps[i].Close()
		isManFile := (files[i] == manFile)

		for _, e := range entries {
			if err := extractEntry(e, fps[i], outDir, isManFile); err != nil {
				return fmt.Errorf("%s: %v", e.path, err)
			}
		}
	}
	return nil
}
