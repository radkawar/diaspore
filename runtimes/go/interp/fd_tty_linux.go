//go:build linux

package interp

import (
	"os"
	"syscall"
	"unsafe"
)

func hostFileIsTTY(file *os.File) (bool, error) {
	conn, err := file.SyscallConn()
	if err != nil {
		return false, nil
	}
	terminal := false
	err = conn.Control(func(fd uintptr) {
		var state syscall.Termios
		_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, uintptr(syscall.TCGETS), uintptr(unsafe.Pointer(&state)))
		terminal = errno == 0
	})
	return terminal && err == nil, nil
}

func hostFDExtraFlags() (nonblock, nofollow int, supported bool) {
	return syscall.O_NONBLOCK, syscall.O_NOFOLLOW, true
}
