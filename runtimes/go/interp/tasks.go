package interp

import "sync"

// The tasks module (spec 11.9): concurrency with GIL semantics. One logical
// interpreter lock (vm.gil) is held whenever diaspore code runs; it is released
// while blocked in channel ops, lock acquire, join, time.sleep, and net
// calls. Tasks are goroutines that acquire the lock to run.

// blocking releases the interpreter lock around a host-blocking operation.
func (vm *VM) blocking(f func()) {
	vm.gil.Unlock()
	f()
	vm.gil.Lock()
}

type diasporeTask struct {
	finished chan struct{}
	// value/err/done are written under the gil before finished closes.
	value Value
	err   error
	done  bool
}

type diasporeChan struct {
	mu       sync.Mutex
	notEmpty sync.Cond
	notFull  sync.Cond
	items    []Value
	capacity int64 // 0 = unbounded
	closed   bool
}

func newDiasporeChan(capacity int64) *diasporeChan {
	c := &diasporeChan{capacity: capacity}
	c.notEmpty.L = &c.mu
	c.notFull.L = &c.mu
	return c
}

// send appends v, blocking (gil released) while a bounded channel is full.
func (c *diasporeChan) send(vm *VM, v Value) error {
	var closed bool
	vm.blocking(func() {
		c.mu.Lock()
		for c.capacity > 0 && int64(len(c.items)) >= c.capacity && !c.closed {
			c.notFull.Wait()
		}
		closed = c.closed
		if !closed {
			c.items = append(c.items, v)
			c.notEmpty.Signal()
		}
		c.mu.Unlock()
	})
	if closed {
		return newError("RuntimeError", "send on closed channel")
	}
	return nil
}

// recv pops the next value, blocking (gil released) while empty; drained
// reports a closed-and-empty channel.
func (c *diasporeChan) recv(vm *VM) (v Value, drained bool) {
	vm.blocking(func() {
		c.mu.Lock()
		for len(c.items) == 0 && !c.closed {
			c.notEmpty.Wait()
		}
		if len(c.items) > 0 {
			v = c.items[0]
			c.items = c.items[1:]
			c.notFull.Signal()
		} else {
			drained = true
		}
		c.mu.Unlock()
	})
	return v, drained
}

func (c *diasporeChan) close() {
	c.mu.Lock()
	c.closed = true
	c.notEmpty.Broadcast()
	c.notFull.Broadcast()
	c.mu.Unlock()
}

func (c *diasporeChan) length() int64 {
	c.mu.Lock()
	n := int64(len(c.items))
	c.mu.Unlock()
	return n
}

type chanIter struct {
	vm *VM
	c  *diasporeChan
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *chanIter) Next() (Value, bool, error) {
	v, drained := it.c.recv(it.vm)
	if drained {
		return nil, false, nil
	}
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *chanIter) KindName() string { return "channel_iterator" }

func noArgs(name string, args []Value, kw []KwArg) error {
	if len(args) > 0 {
		return newErrorf("TypeError", "%s() takes no arguments", name)
	}
	return noKw(name, kw)
}

func (vm *VM) makeTasksModule() *NativeModule {
	spawn := bi("spawn", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := vm.requireCap("spawn"); err != nil {
			return nil, err
		}
		if err := noKw("spawn", kw); err != nil {
			return nil, err
		}
		if len(args) < 1 {
			return nil, newError("TypeError", "spawn() requires a function argument")
		}
		fn := args[0]
		fnArgs := append([]Value{}, args[1:]...)
		t := &diasporeTask{finished: make(chan struct{})}
		go func() {
			vm.gil.Lock()
			v, err := vm.call(fn, fnArgs, nil)
			t.value, t.err = v, err
			t.done = true
			vm.gil.Unlock()
			close(t.finished)
		}()
		return &NativeObject{Kind: "tasks.Task", methods: map[string]*Builtin{
			"join": bi("join", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noArgs("join", args, kw); err != nil {
					return nil, err
				}
				vm.blocking(func() { <-t.finished })
				if t.err != nil {
					return nil, t.err
				}
				return t.value, nil
			}),
			"done": bi("done", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noArgs("done", args, kw); err != nil {
					return nil, err
				}
				select {
				case <-t.finished:
					return t.done, nil
				default:
					return false, nil
				}
			}),
		}}, nil
	})

	channel := bi("channel", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := vm.requireCap("spawn"); err != nil {
			return nil, err
		}
		if err := argCount("channel", args, 0, 1); err != nil {
			return nil, err
		}
		capacity := int64(0)
		if len(args) == 1 {
			n, ok := asInt(args[0])
			if !ok || n < 0 {
				return nil, newError("ValueError", "channel capacity must be >= 0")
			}
			capacity = n
		}
		c := newDiasporeChan(capacity)
		obj := &NativeObject{Kind: "tasks.Channel", methods: map[string]*Builtin{
			"send": bi("send", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := needArgs("send", args, 1, 1); err != nil {
					return nil, err
				}
				if err := c.send(vm, args[0]); err != nil {
					return nil, err
				}
				return None, nil
			}),
			"recv": bi("recv", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noArgs("recv", args, kw); err != nil {
					return nil, err
				}
				v, drained := c.recv(vm)
				if drained {
					return nil, newError("RuntimeError", "recv on closed channel")
				}
				return v, nil
			}),
			"close": bi("close", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noArgs("close", args, kw); err != nil {
					return nil, err
				}
				c.close()
				return None, nil
			}),
			"len": bi("len", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noArgs("len", args, kw); err != nil {
					return nil, err
				}
				return mkInt(c.length()), nil
			}),
		}}
		obj.iterFn = func() Iterator { return &chanIter{vm: vm, c: c} }
		return obj, nil
	})

	lock := bi("lock", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := vm.requireCap("spawn"); err != nil {
			return nil, err
		}
		if err := noArgs("lock", args, kw); err != nil {
			return nil, err
		}
		sem := make(chan struct{}, 1)
		acquire := bi("acquire", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			vm.blocking(func() { sem <- struct{}{} })
			return None, nil
		})
		release := func(name string, exitRet Value) *Builtin {
			return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				select {
				case <-sem:
					return exitRet, nil
				default:
					return nil, newError("RuntimeError", "release unlocked lock")
				}
			})
		}
		return &NativeObject{Kind: "tasks.Lock", methods: map[string]*Builtin{
			"acquire":   acquire,
			"release":   release("release", None),
			"__enter__": acquire,
			"__exit__":  release("__exit__", false),
		}}, nil
	})

	return &NativeModule{Name: "tasks", NS: map[string]Value{
		"spawn": spawn, "channel": channel, "lock": lock,
	}}
}
