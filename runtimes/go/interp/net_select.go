package interp

import (
	"errors"
	"io"
	"math"
	"math/big"
	"net"
	"sync/atomic"
	"syscall"
	"time"
)

type netSelectEntry struct {
	socket io.Closer
	events uint8
}

type netSelectPoller interface {
	wait(time.Duration, bool) ([]uint8, error)
	wake() error
	close()
}

type netSelectWatch struct {
	indices map[int64]int
	closed  atomic.Bool
	poller  netSelectPoller
}

// Registry membership is protected by the VM's GIL. A close must wake selectors
// before Go's socket Close waits for their RawConn.Control references to unwind.
func (st *netState) notifySocketClose(id int64) error {
	for watcher := range st.selectors {
		if _, watching := watcher.indices[id]; !watching {
			continue
		}
		watcher.closed.Store(true)
		if err := watcher.poller.wake(); err != nil {
			return fdError(err, None)
		}
	}
	return nil
}

func (vm *VM) netSelectIDs(value Value) ([]int64, error) {
	var values []Value
	switch sequence := value.(type) {
	case *List:
		values = sequence.Items
	case *Tuple:
		values = sequence.Items
	default:
		var err error
		values, err = vm.iterToSlice(value)
		if err != nil {
			return nil, err
		}
	}
	ids := make([]int64, len(values))
	for i, value := range values {
		value = rawPrim(value)
		if wide, ok := value.(*big.Int); ok {
			value = normBig(wide)
		}
		id, ok := asInt(value)
		if !ok {
			if _, integer := value.(*big.Int); integer {
				return nil, newError("OverflowError", "Python int too large to convert to C int")
			}
			return nil, newError("TypeError", "socket registry ids must be integers")
		}
		if id < 0 {
			return nil, newError("ValueError", "file descriptor cannot be a negative integer")
		}
		if id > math.MaxInt32 {
			return nil, newError("OverflowError", "Python int too large to convert to C int")
		}
		ids[i] = id
	}
	return ids, nil
}

func netSelectTimeout(value Value) (time.Duration, bool, error) {
	if _, infinite := value.(NoneType); infinite {
		return 0, true, nil
	}
	seconds, ok := asFloat(rawPrim(value))
	if !ok {
		return 0, false, newError("TypeError", "timeout must be a number or None")
	}
	if math.IsNaN(seconds) {
		return 0, false, newError("ValueError", "Invalid value NaN (not a number)")
	}
	limit := float64(math.MaxInt64) / float64(time.Second)
	if math.IsInf(seconds, 0) || seconds >= limit || seconds <= -limit {
		return 0, false, newError("OverflowError", "timestamp out of range for platform time_t")
	}
	if seconds < 0 {
		return 0, false, newError("ValueError", "timeout must be non-negative")
	}
	nanoseconds := math.Ceil(seconds * float64(time.Second))
	if nanoseconds >= float64(math.MaxInt64) {
		return 0, false, newError("OverflowError", "timestamp out of range for platform time_t")
	}
	return time.Duration(nanoseconds), false, nil
}

func netSelectError(err error) error {
	var raised *Raised
	if errors.As(err, &raised) {
		return err
	}
	if errors.Is(err, net.ErrClosed) {
		err = syscall.EBADF
	}
	return fdError(err, None)
}

func (st *netState) selectCall(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("select", kw); err != nil {
		return nil, err
	}
	if err := argCount("select", args, 3, 4); err != nil {
		return nil, err
	}
	var timeout Value = None
	if len(args) == 4 {
		timeout = args[3]
	}
	duration, infinite, err := netSelectTimeout(timeout)
	if err != nil {
		return nil, err
	}
	var requested [3][]int64
	for i := range requested {
		requested[i], err = vm.netSelectIDs(args[i])
		if err != nil {
			return nil, err
		}
	}
	indices := make(map[int64]int)
	var entries []netSelectEntry
	for group, ids := range requested {
		for _, id := range ids {
			index, found := indices[id]
			if !found {
				var socket io.Closer
				if listener, exists := st.listeners[id]; exists {
					socket = listener
				} else if conn, exists := st.conns[id]; exists {
					socket = conn
				} else {
					return nil, fdError(syscall.EBADF, None)
				}
				index = len(entries)
				indices[id] = index
				entries = append(entries, netSelectEntry{socket: socket})
			}
			entries[index].events |= 1 << group
		}
	}
	watcher := &netSelectWatch{indices: indices}
	poller, err := newNetSelectPoller(entries, watcher)
	if err != nil {
		return nil, netSelectError(err)
	}
	watcher.poller = poller
	if len(entries) != 0 {
		if st.selectors == nil {
			st.selectors = make(map[*netSelectWatch]struct{})
		}
		st.selectors[watcher] = struct{}{}
	}
	var ready []uint8
	vm.blocking(func() { ready, err = poller.wait(duration, infinite) })
	delete(st.selectors, watcher)
	// No notifier may touch a wake descriptor after it is closed and reusable.
	poller.close()
	if watcher.closed.Load() {
		return nil, fdError(syscall.EBADF, None)
	}
	if err != nil {
		return nil, netSelectError(err)
	}
	groups := make([]Value, 3)
	for group, ids := range requested {
		mask := uint8(1 << group)
		count := 0
		for _, id := range ids {
			if ready[indices[id]]&mask != 0 {
				count++
			}
		}
		items := make([]Value, 0, count)
		for _, id := range ids {
			if ready[indices[id]]&mask != 0 {
				items = append(items, mkInt(id))
			}
		}
		groups[group] = &List{Items: items}
	}
	return &Tuple{Items: groups}, nil
}
