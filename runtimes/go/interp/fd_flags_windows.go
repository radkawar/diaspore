//go:build windows

package interp

// hostDirectoryFlag is the os.open flag that requires a directory. Windows has
// no O_DIRECTORY, so the sandbox reserves an unused bit and enforces the
// directory check itself.
const hostDirectoryFlag = 0x10000000
