//go:build linux

package interp

import (
	"math"
	"strconv"
	"syscall"
	"time"
	"unsafe"
)

// The raw Linux pselect6 ABI accepts dynamically sized unsigned-long bitmaps;
// unlike libc fd_set, these impose no FD_SETSIZE limit on protected host FDs.
// Guest registry IDs never index these bitmaps.
type linuxNetSelect struct {
	entries []netSelectEntry
	raw     []syscall.RawConn
	fds     []int
	pipe    [2]int
	watcher *netSelectWatch
	ready   []uint8
}

func newNetSelectPoller(entries []netSelectEntry, watcher *netSelectWatch) (netSelectPoller, error) {
	poller := &linuxNetSelect{
		entries: entries,
		raw:     make([]syscall.RawConn, len(entries)),
		fds:     make([]int, len(entries)),
		pipe:    [2]int{-1, -1},
		watcher: watcher,
		ready:   make([]uint8, len(entries)),
	}
	for i, entry := range entries {
		socket, ok := entry.socket.(syscall.Conn)
		if !ok {
			return nil, newError("NotImplementedError", "net.select() requires native socket descriptors")
		}
		raw, err := socket.SyscallConn()
		if err != nil {
			return nil, err
		}
		poller.raw[i] = raw
	}
	if len(entries) != 0 {
		if err := syscall.Pipe2(poller.pipe[:], syscall.O_CLOEXEC|syscall.O_NONBLOCK); err != nil {
			return nil, err
		}
	}
	return poller, nil
}

func (poller *linuxNetSelect) wake() error {
	var signal = [1]byte{1}
	for {
		_, err := syscall.Write(poller.pipe[1], signal[:])
		if err == syscall.EINTR {
			continue
		}
		if err == syscall.EAGAIN {
			return nil // A queued notification already makes the pipe readable.
		}
		return err
	}
}

func (poller *linuxNetSelect) close() {
	if poller.pipe[0] >= 0 {
		_ = syscall.Close(poller.pipe[0])
		_ = syscall.Close(poller.pipe[1])
		poller.pipe = [2]int{-1, -1}
	}
}

func (poller *linuxNetSelect) wait(timeout time.Duration, infinite bool) ([]uint8, error) {
	var deadline time.Time
	if !infinite {
		deadline = time.Now().Add(timeout)
	}
	// Control retains each actual socket until the whole syscall completes.
	// Closing a guest handle wakes this wait before Go Close waits on those refs.
	var acquire func(int) error
	acquire = func(i int) error {
		if poller.watcher.closed.Load() {
			return syscall.EBADF
		}
		if i == len(poller.raw) {
			return poller.selectReady(deadline, infinite)
		}
		var inner error
		err := poller.raw[i].Control(func(fd uintptr) {
			if fd > math.MaxInt32 {
				inner = syscall.EBADF
				return
			}
			poller.fds[i] = int(fd)
			inner = acquire(i + 1)
		})
		if err != nil {
			return err
		}
		return inner
	}
	if err := acquire(0); err != nil {
		return nil, err
	}
	return poller.ready, nil
}

func netSelectSetBit(set []uintptr, fd int) {
	set[fd/strconv.IntSize] |= uintptr(1) << uint(fd%strconv.IntSize)
}

func netSelectHasBit(set []uintptr, fd int) bool {
	return len(set) != 0 && set[fd/strconv.IntSize]&(uintptr(1)<<uint(fd%strconv.IntSize)) != 0
}

func netSelectSetPointer(set []uintptr) *uintptr {
	if len(set) == 0 {
		return nil
	}
	return &set[0]
}

func (poller *linuxNetSelect) selectReady(deadline time.Time, infinite bool) error {
	maxFD := poller.pipe[0]
	var present [3]bool
	present[0] = maxFD >= 0
	for i, fd := range poller.fds {
		if fd > maxFD {
			maxFD = fd
		}
		for group := range present {
			present[group] = present[group] || poller.entries[i].events&(1<<group) != 0
		}
	}
	words := 0
	if maxFD >= 0 {
		words = maxFD/strconv.IntSize + 1
	}
	count := 0
	for _, needed := range present {
		if needed {
			count++
		}
	}
	storage := make([]uintptr, words*count)
	var sets [3][]uintptr
	offset := 0
	for group, needed := range present {
		if needed {
			sets[group] = storage[offset : offset+words]
			offset += words
		}
	}
	for {
		if poller.watcher.closed.Load() {
			return syscall.EBADF
		}
		clear(storage)
		for i, fd := range poller.fds {
			for group := range sets {
				if poller.entries[i].events&(1<<group) != 0 {
					netSelectSetBit(sets[group], fd)
				}
			}
		}
		if poller.pipe[0] >= 0 {
			netSelectSetBit(sets[0], poller.pipe[0])
		}
		var timeout syscall.Timespec
		var timeoutPointer *syscall.Timespec
		if !infinite {
			remaining := time.Until(deadline)
			if remaining < 0 {
				remaining = 0
			}
			// Also works on Linux targets whose legacy timespec has 32-bit
			// seconds. Long timeouts keep their original monotonic deadline.
			if remaining > 24*time.Hour {
				remaining = 24 * time.Hour
			}
			timeout = syscall.NsecToTimespec(remaining.Nanoseconds())
			timeoutPointer = &timeout
		}
		ready, _, errno := syscall.Syscall6(syscall.SYS_PSELECT6,
			uintptr(maxFD+1),
			uintptr(unsafe.Pointer(netSelectSetPointer(sets[0]))),
			uintptr(unsafe.Pointer(netSelectSetPointer(sets[1]))),
			uintptr(unsafe.Pointer(netSelectSetPointer(sets[2]))),
			uintptr(unsafe.Pointer(timeoutPointer)), 0)
		if errno == syscall.EINTR {
			continue
		}
		if errno != 0 {
			return errno
		}
		if poller.watcher.closed.Load() ||
			poller.pipe[0] >= 0 && netSelectHasBit(sets[0], poller.pipe[0]) {
			return syscall.EBADF
		}
		if ready == 0 && !infinite && time.Until(deadline) > 0 {
			continue
		}
		for i, fd := range poller.fds {
			for group := range sets {
				if poller.entries[i].events&(1<<group) != 0 && netSelectHasBit(sets[group], fd) {
					poller.ready[i] |= 1 << group
				}
			}
		}
		return nil
	}
}
