//go:build linux

package interp_test

import (
	"bytes"
	"fmt"
	"io"
	"net"
	"testing"
	"time"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestVendoredSelectRealSocket(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	source := fmt.Sprintf(`import select
import selectors
import socket

connection = socket.socket()
connection.connect(('127.0.0.1', %d))
fd = connection.fileno()
class FileObject:
    def fileno(self):
        return fd
fileobj = FileObject()
assert select.select([connection], [], [], 0) == ([], [], [])
readable, writable, exceptional = select.select([], [connection, fd, fileobj, connection], [], 0)
assert readable == exceptional == []
assert writable[0] is connection and writable[1] == fd
assert writable[2] is fileobj and writable[3] is connection
connection.sendall(b'?')
readable, writable, exceptional = select.select([fileobj, connection, fd, fileobj], [], [], 5)
assert readable[0] is fileobj and readable[1] is connection
assert readable[2] == fd and readable[3] is fileobj
assert writable == exceptional == []
with selectors.DefaultSelector() as selector:
    assert isinstance(selector, selectors.SelectSelector)
    selector.register(connection, selectors.EVENT_READ, 'payload')
    ready = selector.select(5)
    assert len(ready) == 1
    key, events = ready[0]
    assert key.fileobj is connection and key.data == 'payload'
    assert events == selectors.EVENT_READ
assert connection.recv(5) == b'ready'
connection.sendall(b'!')
assert select.select([connection], [], [], 5)[0] == [connection]
assert connection.recv(1) == b''
connection.close()
assert connection.fileno() == -1
print('ready')
`, listener.Addr().(*net.TCPAddr).Port)
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	serverErrors := make(chan error, 1)
	go func() {
		connection, err := listener.Accept()
		if err != nil {
			serverErrors <- err
			return
		}
		defer connection.Close()
		if err := connection.SetDeadline(time.Now().Add(15 * time.Second)); err != nil {
			serverErrors <- err
			return
		}
		var marker [1]byte
		if _, err := io.ReadFull(connection, marker[:]); err != nil {
			serverErrors <- err
			return
		}
		if _, err := connection.Write([]byte("ready")); err != nil {
			serverErrors <- err
			return
		}
		_, err = io.ReadFull(connection, marker[:])
		serverErrors <- err
	}()
	var stdout, stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithCaps("net"), interp.WithStdout(&stdout), interp.WithStderr(&stderr),
		interp.WithSnippetCompiler(dspc.CompileSnippet))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
	if err := <-serverErrors; err != nil {
		t.Fatal(err)
	}
	if stdout.String() != "ready\n" {
		t.Fatalf("stdout = %q", stdout.String())
	}
}
