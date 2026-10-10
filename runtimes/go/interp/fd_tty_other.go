//go:build !linux

package interp

import (
	"os"
	"runtime"
)

func hostFileIsTTY(file *os.File) (bool, error) {
	info, err := file.Stat()
	if err != nil {
		return false, nil
	}
	if runtime.GOARCH == "wasm" || info.Mode()&os.ModeCharDevice == 0 {
		return false, nil
	}
	return false, newError("NotImplementedError", "terminal detection is unavailable on this host")
}

func hostFDExtraFlags() (nonblock, nofollow int, supported bool) {
	switch runtime.GOOS {
	case "darwin", "freebsd", "netbsd", "openbsd", "dragonfly":
		return 0x4, 0x100, true
	default:
		// These guest flags are also useful for overlay files. Hosts without
		// a matching open interface must reject them instead of dropping them.
		return 0x800, 0x20000, false
	}
}
