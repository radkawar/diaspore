//go:build linux

package interp

import (
	"errors"
	"io"
	"math"
	"net"
	"syscall"
	"testing"
	"time"
)

const netSelectTestID int64 = 100000007

func netSelectTestPair(t *testing.T) (*net.TCPConn, *net.TCPConn) {
	t.Helper()
	listener, err := net.ListenTCP("tcp4", &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	peer, err := net.DialTCP("tcp4", nil, listener.Addr().(*net.TCPAddr))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = peer.Close() })
	socket, err := listener.AcceptTCP()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = socket.Close() })
	return socket, peer
}

func netSelectTestArgs(ids [3][]int64, timeout Value) []Value {
	args := make([]Value, 4)
	for group, values := range ids {
		items := make([]Value, len(values))
		for i, id := range values {
			items[i] = id
		}
		args[group] = &List{Items: items}
	}
	args[3] = timeout
	return args
}

func netSelectTestCall(vm *VM, state *netState, ids [3][]int64, timeout Value) (Value, error) {
	vm.gil.Lock()
	defer vm.gil.Unlock()
	return state.selectCall(vm, netSelectTestArgs(ids, timeout), nil)
}

func netSelectTestResult(t *testing.T, value Value, err error, want [3][]int64) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
	result, ok := value.(*Tuple)
	if !ok || len(result.Items) != 3 {
		t.Fatalf("invalid result: %#v", value)
	}
	for group, expected := range want {
		list, ok := result.Items[group].(*List)
		if !ok || len(list.Items) != len(expected) {
			t.Fatalf("group %d: got %#v, want %v", group, result.Items[group], expected)
		}
		for i, id := range expected {
			if list.Items[i] != id {
				t.Fatalf("group %d position %d: got %v, want %d", group, i, list.Items[i], id)
			}
		}
	}
}

func netSelectTestErrno(t *testing.T, err error, number syscall.Errno) {
	t.Helper()
	raised, ok := raisedAs(err, "OSError")
	if !ok {
		t.Fatalf("expected OSError(%d), got %v", number, err)
	}
	instance := raised.Exc.(*Instance)
	if instance.dictValue("errno") != int64(number) {
		t.Fatalf("errno: %#v", instance.dictValue("errno"))
	}
}

func TestNativeNetSelectReadWriteEOFAndDuplicates(t *testing.T) {
	first, peer := netSelectTestPair(t)
	second, peer2 := netSelectTestPair(t)
	state := &netState{conns: map[int64]net.Conn{netSelectTestID: first, netSelectTestID + 1: second}}
	vm := &VM{}
	for _, source := range []*net.TCPConn{peer, peer2} {
		if _, err := source.Write([]byte("x")); err != nil {
			t.Fatal(err)
		}
	}
	for _, id := range []int64{netSelectTestID, netSelectTestID + 1} {
		value, err := netSelectTestCall(vm, state, [3][]int64{{id}, nil, nil}, float64(1))
		netSelectTestResult(t, value, err, [3][]int64{{id}, nil, nil})
	}
	reads := []int64{netSelectTestID + 1, netSelectTestID, netSelectTestID + 1}
	writes := []int64{netSelectTestID, netSelectTestID + 1, netSelectTestID}
	value, err := netSelectTestCall(vm, state, [3][]int64{reads, writes, reads}, float64(1))
	netSelectTestResult(t, value, err, [3][]int64{reads, writes, nil})
	if err := first.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	var data [1]byte
	if n, err := first.Read(data[:]); err != nil || n != 1 || data[0] != 'x' {
		t.Fatalf("select consumed data: %d %q %v", n, data, err)
	}
	if err := peer.Close(); err != nil {
		t.Fatal(err)
	}
	value, err = netSelectTestCall(vm, state, [3][]int64{{netSelectTestID}, nil, nil}, float64(1))
	netSelectTestResult(t, value, err, [3][]int64{{netSelectTestID}, nil, nil})
	if n, err := first.Read(data[:]); n != 0 || err != io.EOF {
		t.Fatalf("EOF readiness: %d %v", n, err)
	}
}

func TestNativeNetSelectListener(t *testing.T) {
	listener, err := net.ListenTCP("tcp4", &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	state := &netState{listeners: map[int64]net.Listener{netSelectTestID: listener}}
	vm := &VM{}
	value, err := netSelectTestCall(vm, state, [3][]int64{{netSelectTestID}, {netSelectTestID}, nil}, int64(0))
	netSelectTestResult(t, value, err, [3][]int64{})
	peer, err := net.DialTCP("tcp4", nil, listener.Addr().(*net.TCPAddr))
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()
	value, err = netSelectTestCall(vm, state, [3][]int64{{netSelectTestID, netSelectTestID}, nil, nil}, float64(1))
	netSelectTestResult(t, value, err, [3][]int64{{netSelectTestID, netSelectTestID}, nil, nil})
	if err := listener.SetDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	accepted, err := listener.AcceptTCP()
	if err != nil {
		t.Fatalf("select consumed pending accept: %v", err)
	}
	accepted.Close()
}

func TestNativeNetSelectTimeoutAndExceptionalReadiness(t *testing.T) {
	vm := &VM{}
	state := &netState{}
	start := time.Now()
	value, err := netSelectTestCall(vm, state, [3][]int64{}, 0.02)
	netSelectTestResult(t, value, err, [3][]int64{})
	if time.Since(start) < 20*time.Millisecond {
		t.Fatal("select returned before its timeout")
	}
	socket, peer := netSelectTestPair(t)
	state.conns = map[int64]net.Conn{netSelectTestID: socket}
	raw, err := peer.SyscallConn()
	if err != nil {
		t.Fatal(err)
	}
	var sendErr error
	if err := raw.Control(func(fd uintptr) { sendErr = syscall.Sendto(int(fd), []byte("!"), syscall.MSG_OOB, nil) }); err != nil {
		t.Fatal(err)
	}
	if sendErr != nil {
		t.Fatal(sendErr)
	}
	value, err = netSelectTestCall(vm, state, [3][]int64{nil, nil, {netSelectTestID}}, float64(1))
	netSelectTestResult(t, value, err, [3][]int64{nil, nil, {netSelectTestID}})
	var receiveErr error
	raw, err = socket.SyscallConn()
	if err != nil {
		t.Fatal(err)
	}
	if err := raw.Control(func(fd uintptr) {
		var data [1]byte
		_, _, receiveErr = syscall.Recvfrom(int(fd), data[:], syscall.MSG_OOB)
	}); err != nil {
		t.Fatal(err)
	}
	if receiveErr != nil {
		t.Fatal(receiveErr)
	}
	peer.Close()
	// HUP is readable EOF, not exceptional readiness; it must not cause a
	// premature empty result or a spin loop in an exceptional-only wait.
	start = time.Now()
	value, err = netSelectTestCall(vm, state, [3][]int64{nil, nil, {netSelectTestID}}, 0.02)
	netSelectTestResult(t, value, err, [3][]int64{})
	if time.Since(start) < 20*time.Millisecond {
		t.Fatal("unrequested EOF ended exceptional-set timeout")
	}
}

func TestNativeNetSelectBlockedWriter(t *testing.T) {
	socket, peer := netSelectTestPair(t)
	for _, option := range []struct {
		conn *net.TCPConn
		name int
	}{{socket, syscall.SO_SNDBUF}, {peer, syscall.SO_RCVBUF}} {
		raw, err := option.conn.SyscallConn()
		if err != nil {
			t.Fatal(err)
		}
		var optionErr error
		if err := raw.Control(func(fd uintptr) { optionErr = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, option.name, 4096) }); err != nil {
			t.Fatal(err)
		}
		if optionErr != nil {
			t.Fatal(optionErr)
		}
	}
	if err := socket.SetWriteDeadline(time.Now().Add(50 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	_, err := socket.Write(make([]byte, 2<<20))
	var timeout net.Error
	if !errors.As(err, &timeout) || !timeout.Timeout() {
		t.Fatalf("failed to saturate real socket buffer: %v", err)
	}
	if err := socket.SetWriteDeadline(time.Time{}); err != nil {
		t.Fatal(err)
	}
	state := &netState{conns: map[int64]net.Conn{netSelectTestID: socket}}
	value, err := netSelectTestCall(&VM{}, state, [3][]int64{nil, {netSelectTestID}, nil}, int64(0))
	netSelectTestResult(t, value, err, [3][]int64{})
}

func TestNativeNetSelectGILHandoffAndClose(t *testing.T) {
	t.Run("handoff", func(t *testing.T) {
		socket, peer := netSelectTestPair(t)
		state := &netState{conns: map[int64]net.Conn{netSelectTestID: socket}}
		vm := &VM{}
		done := make(chan error, 1)
		vm.gil.Lock()
		go func() {
			vm.gil.Lock()
			_, err := peer.Write([]byte("x"))
			vm.gil.Unlock()
			done <- err
		}()
		value, err := state.selectCall(vm, netSelectTestArgs([3][]int64{{netSelectTestID}, nil, nil}, float64(1)), nil)
		vm.gil.Unlock()
		netSelectTestResult(t, value, err, [3][]int64{{netSelectTestID}, nil, nil})
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	})
	t.Run("close_wakes_protected_descriptor", func(t *testing.T) {
		socket, _ := netSelectTestPair(t)
		state := &netState{conns: map[int64]net.Conn{netSelectTestID: socket}}
		vm := &VM{}
		done := make(chan error, 1)
		vm.gil.Lock()
		go func() {
			time.Sleep(20 * time.Millisecond)
			vm.gil.Lock()
			err := state.notifySocketClose(netSelectTestID)
			if err == nil {
				err = socket.Close()
				delete(state.conns, netSelectTestID)
			}
			vm.gil.Unlock()
			done <- err
		}()
		start := time.Now()
		_, err := state.selectCall(vm, netSelectTestArgs([3][]int64{{netSelectTestID}, nil, nil}, float64(2)), nil)
		vm.gil.Unlock()
		netSelectTestErrno(t, err, syscall.EBADF)
		if time.Since(start) >= time.Second {
			t.Fatal("close did not promptly wake select")
		}
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	})
	t.Run("unrelated_close", func(t *testing.T) {
		socket, peer := netSelectTestPair(t)
		other, _ := netSelectTestPair(t)
		state := &netState{conns: map[int64]net.Conn{netSelectTestID: socket, netSelectTestID + 1: other}}
		vm := &VM{}
		done := make(chan error, 1)
		vm.gil.Lock()
		go func() {
			vm.gil.Lock()
			err := state.notifySocketClose(netSelectTestID + 1)
			if err == nil {
				err = other.Close()
				delete(state.conns, netSelectTestID+1)
			}
			if err == nil {
				_, err = peer.Write([]byte("x"))
			}
			vm.gil.Unlock()
			done <- err
		}()
		value, err := state.selectCall(vm, netSelectTestArgs([3][]int64{{netSelectTestID}, nil, nil}, None), nil)
		vm.gil.Unlock()
		netSelectTestResult(t, value, err, [3][]int64{{netSelectTestID}, nil, nil})
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	})
}

func TestNativeNetSelectValidationAndCapabilities(t *testing.T) {
	vm := &VM{}
	state := &netState{}
	_, err := netSelectTestCall(vm, state, [3][]int64{{netSelectTestID}, nil, nil}, int64(0))
	netSelectTestErrno(t, err, syscall.EBADF)
	socket, _ := netSelectTestPair(t)
	state.conns = map[int64]net.Conn{netSelectTestID: socket}
	raw, err := socket.SyscallConn()
	if err != nil {
		t.Fatal(err)
	}
	var hostFD int64
	if err := raw.Control(func(fd uintptr) { hostFD = int64(fd) }); err != nil {
		t.Fatal(err)
	}
	_, err = netSelectTestCall(vm, state, [3][]int64{{hostFD}, nil, nil}, int64(0))
	netSelectTestErrno(t, err, syscall.EBADF)
	for _, test := range []struct {
		id      Value
		timeout Value
		kind    string
	}{
		{float64(1), int64(0), "TypeError"},
		{int64(-1), int64(0), "ValueError"},
		{int64(1 << 40), int64(0), "OverflowError"},
		{netSelectTestID, -0.1, "ValueError"},
		{netSelectTestID, math.NaN(), "ValueError"},
		{netSelectTestID, math.Inf(1), "OverflowError"},
		{netSelectTestID, "0", "TypeError"},
	} {
		args := netSelectTestArgs([3][]int64{}, test.timeout)
		args[0] = &List{Items: []Value{test.id}}
		vm.gil.Lock()
		_, err := state.selectCall(vm, args, nil)
		vm.gil.Unlock()
		if _, ok := raisedAs(err, test.kind); !ok {
			t.Fatalf("id=%v timeout=%v: expected %s, got %v", test.id, test.timeout, test.kind, err)
		}
	}
	module := vm.makeNetModule()
	vm.gil.Lock()
	_, err = module.NS["select"].(*Builtin).Fn(vm, netSelectTestArgs([3][]int64{}, int64(0)), nil)
	vm.gil.Unlock()
	if _, ok := raisedAs(err, "PermissionError"); !ok {
		t.Fatalf("missing net capability: %v", err)
	}
}
