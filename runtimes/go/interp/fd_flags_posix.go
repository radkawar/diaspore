//go:build !windows

package interp

import "syscall"

// hostDirectoryFlag is the os.open flag that requires a directory.
const hostDirectoryFlag = syscall.O_DIRECTORY
