//go:build !linux

package interp

func newNetSelectPoller(entries []netSelectEntry, watcher *netSelectWatch) (netSelectPoller, error) {
	return nil, newError("NotImplementedError", "net.select() is not implemented on this platform")
}
