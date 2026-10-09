package interp

import (
	"runtime"
	"sync/atomic"
)

// Iterator is the iteration protocol shared by all diaspore iterables' iterators.
// Next returns (value, true, nil) for each element, (_, false, nil) on
// exhaustion, or an error.
type Iterator interface {
	// Next advances the iterator; the boolean is false at exhaustion.
	Next() (Value, bool, error)
	// KindName returns the Python iterator type name.
	KindName() string
}

// retValIterator is implemented by iterators that can carry a StopIteration
// value (generators, user __next__ iterators); yield-from consumes it.
type retValIterator interface {
	// RetVal returns the value carried by StopIteration.
	RetVal() Value
}

// getIter returns an iterator over v, or a TypeError.
func (vm *VM) getIter(v Value) (Iterator, error) {
	switch t := v.(type) {
	case *Generator:
		return t, nil
	case Iterator:
		return t, nil
	case *List:
		return &listIter{l: t}, nil
	case *Tuple:
		return &seqIter{items: t.Items}, nil
	case string:
		return &strIter{rs: []rune(t)}, nil
	case []byte:
		return &bytesIter{b: t}, nil
	case *Range:
		return &rangeIter{cur: t.Start, step: t.Step, left: t.Len()}, nil
	case *Dict:
		return &seqIter{items: t.Keys()}, nil
	case *Set:
		return &seqIter{items: t.Elems()}, nil
	case *NativeObject:
		if t.iterFn != nil {
			return t.iterFn(), nil
		}
		return nil, newErrorf("TypeError", "'%s' object is not iterable", t.Kind)
	case *Instance:
		_, hasIter := t.Class.Lookup("__iter__")
		_, hasNext := t.Class.Lookup("__next__")
		if !hasIter && !hasNext && t.Prim != nil {
			return vm.getIter(t.Prim)
		}
		if _, ok := t.Class.Lookup("__iter__"); ok {
			it, err := vm.callDunder(t, "__iter__", nil)
			if err != nil {
				return nil, err
			}
			switch r := it.(type) {
			case *Generator:
				return r, nil
			case Iterator:
				return r, nil
			case *Instance:
				return &instIter{vm: vm, obj: r, retval: None}, nil
			}
			return vm.getIter(it)
		}
		if _, ok := t.Class.Lookup("__next__"); ok {
			return &instIter{vm: vm, obj: t, retval: None}, nil
		}
		return nil, newErrorf("TypeError", "'%s' object is not iterable", t.Class.Name)
	}
	return nil, newErrorf("TypeError", "'%s' object is not iterable", typeName(v))
}

func (vm *VM) iterToSlice(v Value) ([]Value, error) {
	switch t := v.(type) {
	case *List:
		return append([]Value{}, t.Items...), nil
	case *Tuple:
		return append([]Value{}, t.Items...), nil
	}
	it, err := vm.getIter(v)
	if err != nil {
		return nil, err
	}
	var out []Value
	for {
		x, ok, err := it.Next()
		if err != nil {
			return nil, err
		}
		if !ok {
			return out, nil
		}
		out = append(out, x)
	}
}

type listIter struct {
	l *List
	i int
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *listIter) Next() (Value, bool, error) {
	if it.i >= len(it.l.Items) {
		return nil, false, nil
	}
	v := it.l.Items[it.i]
	it.i++
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *listIter) KindName() string { return "list_iterator" }

type seqIter struct {
	items []Value
	i     int
	kind  string
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *seqIter) Next() (Value, bool, error) {
	if it.i >= len(it.items) {
		return nil, false, nil
	}
	v := it.items[it.i]
	it.i++
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *seqIter) KindName() string {
	if it.kind != "" {
		return it.kind
	}
	return "iterator"
}

type strIter struct {
	rs []rune
	i  int
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *strIter) Next() (Value, bool, error) {
	if it.i >= len(it.rs) {
		return nil, false, nil
	}
	v := string(it.rs[it.i])
	it.i++
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *strIter) KindName() string { return "str_iterator" }

type bytesIter struct {
	b []byte
	i int
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *bytesIter) Next() (Value, bool, error) {
	if it.i >= len(it.b) {
		return nil, false, nil
	}
	v := mkInt(int64(it.b[it.i]))
	it.i++
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *bytesIter) KindName() string { return "bytes_iterator" }

type rangeIter struct {
	cur  int64
	step int64
	left int64
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *rangeIter) Next() (Value, bool, error) {
	if it.left <= 0 {
		return nil, false, nil
	}
	v := mkInt(it.cur)
	it.cur += it.step
	it.left--
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *rangeIter) KindName() string { return "range_iterator" }

// instIter adapts a diaspore object implementing __next__.
type instIter struct {
	vm     *VM
	obj    *Instance
	retval Value
	done   bool
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *instIter) Next() (Value, bool, error) {
	if it.done {
		return nil, false, nil
	}
	v, err := it.vm.callDunder(it.obj, "__next__", nil)
	if err != nil {
		if r, ok := raisedAs(err, "StopIteration"); ok {
			it.done = true
			if args := excArgs(r.Exc.(*Instance)); len(args) > 0 {
				it.retval = args[0]
			}
			return nil, false, nil
		}
		return nil, false, err
	}
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *instIter) KindName() string { return "iterator" }

// RetVal returns the value carried by StopIteration.
func (it *instIter) RetVal() Value { return it.retval }

type mapIter struct {
	vm   *VM
	fn   Value
	its  []Iterator
	done bool
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *mapIter) Next() (Value, bool, error) {
	if it.done {
		return nil, false, nil
	}
	args := make([]Value, len(it.its))
	for i, sub := range it.its {
		x, ok, err := sub.Next()
		if err != nil {
			return nil, false, err
		}
		if !ok {
			it.done = true
			return nil, false, nil
		}
		args[i] = x
	}
	v, err := it.vm.call(it.fn, args, nil)
	if err != nil {
		return nil, false, err
	}
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *mapIter) KindName() string { return "map" }

type filterIter struct {
	vm   *VM
	fn   Value // nil means truthiness filter
	it   Iterator
	done bool
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *filterIter) Next() (Value, bool, error) {
	if it.done {
		return nil, false, nil
	}
	for {
		x, ok, err := it.it.Next()
		if err != nil {
			return nil, false, err
		}
		if !ok {
			it.done = true
			return nil, false, nil
		}
		keep := x
		if it.fn != nil {
			keep, err = it.vm.call(it.fn, []Value{x}, nil)
			if err != nil {
				return nil, false, err
			}
		}
		b, err := it.vm.truth(keep)
		if err != nil {
			return nil, false, err
		}
		if b {
			return x, true, nil
		}
	}
}

// KindName returns the Python iterator type name.
func (it *filterIter) KindName() string { return "filter" }

type zipIter struct {
	its  []Iterator
	done bool
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *zipIter) Next() (Value, bool, error) {
	if it.done || len(it.its) == 0 {
		it.done = true
		return nil, false, nil
	}
	row := make([]Value, len(it.its))
	for i, sub := range it.its {
		x, ok, err := sub.Next()
		if err != nil {
			return nil, false, err
		}
		if !ok {
			it.done = true
			return nil, false, nil
		}
		row[i] = x
	}
	return &Tuple{Items: row}, true, nil
}

// KindName returns the Python iterator type name.
func (it *zipIter) KindName() string { return "zip" }

type enumIter struct {
	it Iterator
	n  int64
}

// Next advances the iterator; the boolean is false at exhaustion.
func (it *enumIter) Next() (Value, bool, error) {
	x, ok, err := it.it.Next()
	if err != nil || !ok {
		return nil, false, err
	}
	v := &Tuple{Items: []Value{mkInt(it.n), x}}
	it.n++
	return v, true, nil
}

// KindName returns the Python iterator type name.
func (it *enumIter) KindName() string { return "enumerate" }

type genMsg struct {
	kind byte // 'y' yield, 'r' return, 'e' error
	v    Value
	err  error
}

// genToken is one resume command from the consumer: send a value, throw an
// exception into the generator, or (finalizer-only) unwind and exit.
type genToken struct {
	kind byte  // 's' send, 't' throw (incl. GeneratorExit from close), 'c' finalizer close
	v    Value // send value
	exc  error // *Raised for 't'
}

// genChannel is the generator goroutine's side of the handoff protocol. It
// deliberately does not reference the Generator: the running goroutine must
// not pin the object whose garbage collection is what tells the goroutine to
// shut down.
type genChannel struct {
	resume  chan genToken
	out     chan genMsg
	started atomic.Bool
	done    atomic.Bool
	closing bool // generator-goroutine only
}

// yield hands a value to the consumer and returns the value delivered by the
// next send() (None from next()). A thrown exception surfaces as a *Raised
// (catchable inside the generator); a finalizer close returns errGenClose
// (with the run-lock held) so pending finally blocks unwind.
func (ch *genChannel) yield(vm *VM, v Value) (Value, error) {
	ch.out <- genMsg{kind: 'y', v: v}
	tok := <-ch.resume
	switch tok.kind {
	case 'c':
		ch.closing = true
		vm.gil.Lock()
		return nil, errGenClose
	case 't':
		return nil, tok.exc
	}
	return tok.v, nil
}

// runGenBody is the generator goroutine. It runs under the consumer's
// interpreter lock (the consumer is parked during the handoff), except when
// unwinding after a close, where it takes the lock itself.
func runGenBody(vm *VM, body execFn, fr *frame, ch *genChannel) {
	if tok := <-ch.resume; tok.kind == 'c' {
		return
	}
	err := body(fr)
	if ch.closing {
		vm.gil.Unlock()
		return // abandoned: discard anything raised while unwinding
	}
	switch err {
	case nil:
		ch.out <- genMsg{kind: 'r', v: None}
	case errReturn:
		ch.out <- genMsg{kind: 'r', v: fr.ret}
	default:
		ch.out <- genMsg{kind: 'e', err: err}
	}
}

// closeGenChannel wakes an abandoned generator's parked goroutine (invoked
// by the Generator finalizer; must not reference the Generator).
func closeGenChannel(ch *genChannel) {
	if ch.started.Load() && !ch.done.Load() {
		select {
		case ch.resume <- genToken{kind: 'c'}:
		default:
		}
	}
}

// Generator is a running diaspore generator (goroutine + channel pair, strict
// ping-pong under the interpreter lock; abandoning one triggers a finalizer
// that unwinds and exits its goroutine).
type Generator struct {
	vm     *VM
	fn     *Function
	frame  *frame
	ch     *genChannel
	retval Value
}

func newGenerator(vm *VM, fn *Function, fr *frame) *Generator {
	ch := &genChannel{
		resume: make(chan genToken),
		out:    make(chan genMsg),
	}
	fr.gen = ch
	g := &Generator{vm: vm, fn: fn, frame: fr, ch: ch, retval: None}
	runtime.SetFinalizer(g, func(*Generator) { closeGenChannel(ch) })
	return g
}

// advance drives the generator with one resume token. done=true means the
// generator finished (g.retval holds its return value).
func (g *Generator) advance(tok genToken) (v Value, done bool, err error) {
	ch := g.ch
	if ch.done.Load() {
		return nil, true, nil
	}
	if !ch.started.Load() {
		ch.started.Store(true)
		go runGenBody(g.vm, g.fn.cf.body, g.frame, ch)
	}
	if g.vm.tracer != nil {
		g.vm.traceCall(g.frame)
	}
	ch.resume <- tok
	m := <-ch.out
	if g.vm.tracer != nil {
		if m.kind == 'y' {
			g.vm.traceEmit("yield", g.frame, traceRepr(m.v, 0))
			g.vm.trace.frames = g.vm.trace.frames[:len(g.vm.trace.frames)-1]
		} else {
			g.frame.ret = m.v
			end := m.err
			if m.kind == 'r' {
				end = errReturn
			}
			g.vm.traceReturn(g.frame, end)
		}
	}
	switch m.kind {
	case 'y':
		return m.v, false, nil
	case 'r':
		ch.done.Store(true)
		g.retval = m.v
		return nil, true, nil
	default:
		ch.done.Store(true)
		return nil, true, m.err
	}
}

// Next advances the generator (the iterator protocol).
func (g *Generator) Next() (Value, bool, error) {
	v, done, err := g.advance(genToken{kind: 's', v: None})
	if err != nil {
		return nil, false, err
	}
	if done {
		return nil, false, nil
	}
	return v, true, nil
}

// stopWithValue builds the StopIteration surfacing a generator's return
// value in args[0] (CPython carries it there and as .value).
func stopWithValue(retval Value) error {
	if _, isNone := retval.(NoneType); isNone || retval == nil {
		return newErrorArgs("StopIteration")
	}
	return newErrorArgs("StopIteration", retval)
}

// Send resumes the generator, delivering v as the value of the paused yield
// expression. Exhaustion raises StopIteration carrying the return value.
func (g *Generator) Send(v Value) (Value, error) {
	if !g.ch.started.Load() {
		if _, isNone := v.(NoneType); !isNone {
			return nil, newError("TypeError",
				"can't send non-None value to a just-started generator")
		}
	}
	if g.ch.done.Load() {
		return nil, newErrorArgs("StopIteration")
	}
	y, done, err := g.advance(genToken{kind: 's', v: v})
	if err != nil {
		return nil, err
	}
	if done {
		return nil, stopWithValue(g.retval)
	}
	return y, nil
}

// Throw raises exc (an exception instance) at the paused yield expression.
func (g *Generator) Throw(exc Value) (Value, error) {
	if g.ch.done.Load() {
		return nil, &Raised{Exc: exc}
	}
	y, done, err := g.advance(genToken{kind: 't', exc: &Raised{Exc: exc}})
	if err != nil {
		return nil, err
	}
	if done {
		return nil, stopWithValue(g.retval)
	}
	return y, nil
}

// retvalOf extracts the StopIteration value of an exhausted iterator.
func retvalOf(it Iterator) Value {
	if rv, ok := it.(retValIterator); ok {
		return rv.RetVal()
	}
	return None
}

// delegateYieldFrom implements `yield from` / await (PEP 380): values pass
// through, send() is forwarded into a subgenerator, throw()/close() forward
// too, and the subiterator's return value becomes the expression's value.
func (vm *VM) delegateYieldFrom(fr *frame, it Iterator) (Value, error) {
	sub, delegating := it.(*Generator)
	y, ok, err := it.Next()
	if err != nil {
		return nil, err
	}
	if !ok {
		return retvalOf(it), nil
	}
	for {
		sent, yerr := fr.gen.yield(vm, y)
		if yerr != nil {
			r, isRaised := yerr.(*Raised)
			if !isRaised || !delegating {
				return nil, yerr
			}
			// Forward throw/close into the subgenerator.
			if excIsA(r.Exc, "GeneratorExit") {
				if cerr := sub.Close(); cerr != nil {
					return nil, cerr
				}
				return nil, yerr
			}
			y2, terr := sub.Throw(r.Exc)
			if terr != nil {
				if sr, ok := raisedAs(terr, "StopIteration"); ok {
					return stopValueOf(sr), nil
				}
				return nil, terr
			}
			y = y2
			continue
		}
		var next Value
		var done bool
		if delegating {
			next, err = sub.Send(sent)
			if err != nil {
				if sr, ok := raisedAs(err, "StopIteration"); ok {
					return stopValueOf(sr), nil
				}
				return nil, err
			}
			y = next
			continue
		}
		next, done, err = itNext(it)
		if err != nil {
			return nil, err
		}
		if done {
			return retvalOf(it), nil
		}
		y = next
	}
}

func itNext(it Iterator) (Value, bool, error) {
	v, ok, err := it.Next()
	return v, !ok, err
}

// stopValueOf reads the value a StopIteration carries in args[0].
func stopValueOf(r *Raised) Value {
	if args := excArgs(r.Exc.(*Instance)); len(args) > 0 {
		return args[0]
	}
	return None
}

// Close throws GeneratorExit into the generator; a generator that yields
// anyway is an error, one that returns or lets GeneratorExit escape is fine.
func (g *Generator) Close() error {
	ch := g.ch
	if ch.done.Load() || !ch.started.Load() {
		ch.done.Store(true)
		return nil
	}
	ge := makeExc("GeneratorExit")
	_, done, err := g.advance(genToken{kind: 't', exc: &Raised{Exc: ge}})
	if err != nil {
		if r, ok := err.(*Raised); ok {
			if r.Exc == Value(ge) || excIsA(r.Exc, "GeneratorExit") {
				return nil
			}
		}
		return err
	}
	if done {
		return nil
	}
	return newError("RuntimeError", "generator ignored GeneratorExit")
}

// KindName implements Iterator.
func (g *Generator) KindName() string { return "generator" }

// RetVal returns the generator's StopIteration value.
func (g *Generator) RetVal() Value { return g.retval }
