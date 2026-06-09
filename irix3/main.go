package main

import (
	"errors"
	"os"
	"fmt"
	"github.com/sirupsen/logrus"
)

func mainE() error {
	args := os.Args
	if len(args) < 2 || len(args) > 5 {
		return errors.New("usage: sgix <file.idb> [<file.sw> [<file.man>] [<output dir>]]")
	}

	var idbFile string
	var swFile string
	var manFile string
	var outDir string

	for argNum := 1; argNum < len(args); argNum++ {
		arg := args[argNum]
		argLen := len(args[argNum])
		if arg[argLen-4:] == ".idb" {
			idbFile = args[argNum]
		} else if arg[argLen-3:] == ".sw" {
			swFile = args[argNum]
		} else if arg[argLen-4:] == ".man" {
			manFile = args[argNum]
		} else {
			outDir = args[argNum]
		}
	}

	fmt.Println("INFO: idb = ", idbFile, "\nsw = ", swFile, "\nman = ", manFile, "\noutput = ", outDir)
	//os.Exit(1)

	if idbFile == "" {
		return errors.New("Missing .idb file")
	}

	ents, err := readIDB(idbFile)
	if err != nil {
		return err
	}

	if outDir == "" {
		outDir = "./out"
	}

	fmt.Println("RUNNING EXTRACT",swFile,manFile,outDir,"\n");
	return extract(ents, swFile, manFile, outDir)
}

func main() {
	if err := mainE(); err != nil {
		logrus.Errorln("ERROR:", err)
		os.Exit(1)
	}
}
