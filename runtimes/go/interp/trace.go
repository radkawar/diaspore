package interp

import (
	"errors"
	"math/big"
	"sort"
	"strings"
	"unicode/utf8"
)

// TraceFrame identifies an active Python frame.
type TraceFrame struct {
	Module   string `json:"module"`
	Function string `json:"function"`
	Line     int    `json:"line"`
}

// TraceEvent records a statement start, call, return, yield, or raised exception.
// Locals contains at most 32 names, with representations bounded to 160 bytes.
// Stack and Locals are independent snapshots and may be retained by the caller.
// Depth is zero at module scope. Return and yield events precede frame removal.
type TraceEvent struct {
	Kind            string            `json:"kind"`
	Module          string            `json:"module"`
	Function        string            `json:"function"`
	Line            int               `json:"line"`
	Depth           int               `json:"depth"`
	Locals          map[string]string `json:"locals"`
	LocalsTruncated bool              `json:"localsTruncated,omitempty"`
	Stack           []TraceFrame      `json:"stack"`
	Value           string            `json:"value,omitempty"`
}

type traceState struct {
	frames []*frame
	raised *Raised
}

// WithTracer records execution synchronously while the VM holds its interpreter
// lock. The callback must not call back into the VM. Representations never invoke
// Python methods. A nil callback disables tracing; untraced statements have no
// tracing overhead because their closures are selected during compilation.
func WithTracer(fn func(TraceEvent)) Option {
	return func(vm *VM) { vm.tracer = fn }
}

func traceClip(s string) string {
	if len(s) <= 160 {
		return s
	}
	n := 157
	for !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n] + "..."
}

func traceRepr(v Value, depth int) string {
	if depth >= 3 {
		return "..."
	}
	seq := func(items []Value, left, right string) string {
		parts := make([]string, 0, 7)
		for i, item := range items {
			if i == 6 {
				parts = append(parts, "...")
				break
			}
			parts = append(parts, traceRepr(item, depth+1))
		}
		return traceClip(left + strings.Join(parts, ", ") + right)
	}
	switch x := v.(type) {
	case string:
		return traceClip(reprString(traceClip(x)))
	case []byte:
		if len(x) > 48 {
			return reprBytes(x[:48]) + "..."
		}
		return reprBytes(x)
	case *big.Int:
		if x.BitLen() > 512 {
			return "<int: more than 512 bits>"
		}
	case *List:
		return seq(x.Items, "[", "]")
	case *Tuple:
		right := ")"
		if len(x.Items) == 1 {
			right = ",)"
		}
		return seq(x.Items, "(", right)
	case *Dict:
		parts := make([]string, 0, 7)
		for _, entry := range x.entries {
			if entry.key == nil {
				continue
			}
			if len(parts) == 6 {
				parts = append(parts, "...")
				break
			}
			parts = append(parts, traceRepr(entry.key, depth+1)+": "+traceRepr(entry.val, depth+1))
		}
		return traceClip("{" + strings.Join(parts, ", ") + "}")
	case map[string]Value:
		names := make([]string, 0, len(x))
		for name := range x {
			names = append(names, name)
		}
		sort.Strings(names)
		parts := make([]string, 0, 7)
		for i, name := range names {
			if i == 6 {
				parts = append(parts, "...")
				break
			}
			parts = append(parts, traceRepr(name, depth+1)+": "+traceRepr(x[name], depth+1))
		}
		return traceClip("{" + strings.Join(parts, ", ") + "}")
	case *Instance:
		return traceClip("<" + x.Class.Name + " " + traceRepr(x.D, depth+1) + ">")
	case *Function:
		return traceClip("<function " + x.cf.name + ">")
	}
	return traceClip(reprNoVM(v))
}

func traceLocals(fr *frame) (map[string]string, bool) {
	names := make([]string, 0, len(fr.layout)+len(fr.vars)+len(fr.cells))
	values := map[string]Value{}
	add := func(name string, v Value) {
		if v != nil && !strings.HasPrefix(name, ".") {
			if _, exists := values[name]; !exists {
				names = append(names, name)
			}
			values[name] = v
		}
	}
	for name, idx := range fr.layout {
		add(name, fr.slots[idx])
	}
	for name, v := range fr.vars {
		add(name, v)
	}
	for name, c := range fr.cells {
		add(name, c.v)
	}
	sort.Strings(names)
	truncated := len(names) > 32
	if truncated {
		names = names[:32]
	}
	locals := make(map[string]string, len(names))
	for _, name := range names {
		locals[name] = traceRepr(values[name], 0)
	}
	return locals, truncated
}

func (vm *VM) traceEmit(kind string, fr *frame, value string) {
	stack := make([]TraceFrame, 0, len(vm.trace.frames)+1)
	for _, f := range vm.trace.frames {
		stack = append(stack, TraceFrame{f.moduleName, f.fname, f.line})
	}
	if len(vm.trace.frames) == 0 || vm.trace.frames[len(vm.trace.frames)-1] != fr {
		stack = append(stack, TraceFrame{fr.moduleName, fr.fname, fr.line})
	}
	locals, truncated := traceLocals(fr)
	vm.tracer(TraceEvent{Kind: kind, Module: fr.moduleName, Function: fr.fname,
		Line: fr.line, Depth: len(stack) - 1, Locals: locals, LocalsTruncated: truncated,
		Stack: stack, Value: value})
}

func (vm *VM) traceCall(fr *frame) {
	vm.trace.frames = append(vm.trace.frames, fr)
	vm.traceEmit("call", fr, "")
}

func (vm *VM) traceReturn(fr *frame, err error) {
	vm.traceException(fr, err)
	value := "None"
	if err == errReturn {
		value = traceRepr(fr.ret, 0)
	}
	vm.traceEmit("return", fr, value)
	vm.trace.frames = vm.trace.frames[:len(vm.trace.frames)-1]
}

func (vm *VM) traceException(fr *frame, err error) {
	var raised *Raised
	if errors.As(err, &raised) && raised != vm.trace.raised {
		vm.trace.raised = raised
		vm.traceEmit("exception", fr, traceClip(reprNoVM(raised.Exc)))
	}
}

func (vm *VM) traceStatement(fn execFn, line int) execFn {
	return func(fr *frame) error {
		fr.line = line
		entered := len(vm.trace.frames) == 0 || vm.trace.frames[len(vm.trace.frames)-1] != fr
		if entered {
			vm.trace.frames = append(vm.trace.frames, fr)
		}
		vm.traceEmit("statement", fr, "")
		err := fn(fr)
		vm.traceException(fr, err)
		if entered {
			vm.trace.frames = vm.trace.frames[:len(vm.trace.frames)-1]
		}
		return err
	}
}
