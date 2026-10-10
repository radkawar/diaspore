package interp

import (
	"errors"
	"fmt"
)

// Raised is a diaspore-level exception in flight. Exc is the exception instance
// (*Instance whose class derives from BaseException).
type Raised struct {
	metadataFrame *frame
	// Exc is an exception instance whose class derives from BaseException.
	Exc Value
	// TB holds traceback entries, from the raising frame outward.
	TB []tbEntry
}

type tbEntry struct {
	module string
	fname  string
	line   int
}

// Error returns the exception type and message without invoking user methods.
func (r *Raised) Error() string {
	if inst, ok := r.Exc.(*Instance); ok {
		msg := plainExcStr(inst)
		if msg == "" {
			return inst.Class.Name
		}
		return inst.Class.Name + ": " + msg
	}
	return fmt.Sprintf("%v", r.Exc)
}

// plainExcStr renders an exception message without dunder dispatch; it is
// only used for host-facing error text (Raised.Error). The VM proper uses
// (*VM).excStr.
func plainExcStr(inst *Instance) string {
	args := excArgs(inst)
	if len(args) == 0 {
		return ""
	}
	if len(args) == 1 {
		if s, ok := args[0].(string); ok {
			return s
		}
		return reprNoVM(args[0])
	}
	return reprNoVM(&Tuple{Items: args})
}

func excArgs(inst *Instance) []Value {
	if a, ok := inst.dictValue("args").(*Tuple); ok {
		return a.Items
	}
	return nil
}

// Trap is uncatchable by sandboxed code (e.g. fuel exhaustion).
type Trap struct {
	// Msg describes the resource limit or runtime failure.
	Msg string
	// Code is the process exit code reported for this trap.
	Code int
}

// Error returns the trap message.
func (t *Trap) Error() string { return t.Msg }

// Control-flow sentinels (never visible to sandboxed code). Return values
// travel in the frame's ret field to keep returns allocation-free.
var (
	errBreak    = errors.New("diaspore: break outside loop")
	errContinue = errors.New("diaspore: continue outside loop")
	errReturn   = errors.New("diaspore: return outside function")
	// errGenClose unwinds an abandoned generator's frame so its pending
	// finally blocks run; except handlers never match it.
	errGenClose = errors.New("diaspore: generator close")
)

var excTree = [][2]string{
	{"BaseException", ""},
	{"SystemExit", "BaseException"},
	{"KeyboardInterrupt", "BaseException"},
	{"GeneratorExit", "BaseException"},
	{"BaseExceptionGroup", "BaseException"},
	{"Exception", "BaseException"},
	{"Warning", "Exception"},
	{"UserWarning", "Warning"},
	{"DeprecationWarning", "Warning"},
	{"PendingDeprecationWarning", "Warning"},
	{"SyntaxWarning", "Warning"},
	{"RuntimeWarning", "Warning"},
	{"FutureWarning", "Warning"},
	{"ImportWarning", "Warning"},
	{"UnicodeWarning", "Warning"},
	{"BytesWarning", "Warning"},
	{"ResourceWarning", "Warning"},
	{"EncodingWarning", "Warning"},
	{"ArithmeticError", "Exception"},
	{"ZeroDivisionError", "ArithmeticError"},
	{"OverflowError", "ArithmeticError"},
	{"AssertionError", "Exception"},
	{"AttributeError", "Exception"},
	{"EOFError", "Exception"},
	{"ImportError", "Exception"},
	{"ModuleNotFoundError", "ImportError"},
	{"LookupError", "Exception"},
	{"IndexError", "LookupError"},
	{"KeyError", "LookupError"},
	{"NameError", "Exception"},
	{"UnboundLocalError", "NameError"},
	{"OSError", "Exception"},
	{"PermissionError", "OSError"},
	{"RuntimeError", "Exception"},
	{"NotImplementedError", "RuntimeError"},
	{"RecursionError", "RuntimeError"},
	{"StopIteration", "Exception"},
	{"StopAsyncIteration", "Exception"},
	{"TypeError", "Exception"},
	{"ValueError", "Exception"},
	{"SyntaxError", "Exception"},
	{"IndentationError", "SyntaxError"},
	{"TabError", "IndentationError"},
	{"SystemError", "Exception"},
	{"ReferenceError", "Exception"},
	{"BufferError", "Exception"},
	{"MemoryError", "Exception"},
	{"FloatingPointError", "ArithmeticError"},
	{"UnicodeError", "ValueError"},
	{"UnicodeDecodeError", "UnicodeError"},
	{"UnicodeEncodeError", "UnicodeError"},
	{"UnicodeTranslateError", "UnicodeError"},
	{"BlockingIOError", "OSError"},
	{"ChildProcessError", "OSError"},
	{"ConnectionError", "OSError"},
	{"BrokenPipeError", "ConnectionError"},
	{"ConnectionResetError", "ConnectionError"},
	{"ConnectionAbortedError", "ConnectionError"},
	{"ConnectionRefusedError", "ConnectionError"},
	{"FileExistsError", "OSError"},
	{"FileNotFoundError", "OSError"},
	{"InterruptedError", "OSError"},
	{"IsADirectoryError", "OSError"},
	{"NotADirectoryError", "OSError"},
	{"ProcessLookupError", "OSError"},
	{"TimeoutError", "OSError"},
}

// objectClass is the universal base class: every class's MRO ends with it.
var objectClass = func() *Class {
	c := &Class{Name: "object", NS: map[string]Value{}}
	c.MRO = []*Class{c}
	return c
}()

// excClasses is the shared builtin exception hierarchy. The class objects are
// immutable from the host side; sandboxed set_attr on them is confined to a
// single process anyway, matching the Python VM's module-global classes.
var excClasses = buildExcClasses()

var baseExc = excClasses["BaseException"]

func buildExcClasses() map[string]*Class {
	m := map[string]*Class{}
	for _, pair := range excTree {
		name, parent := pair[0], pair[1]
		var bases []*Class
		if parent != "" {
			bases = []*Class{m[parent]}
		} else {
			bases = []*Class{objectClass}
		}
		c := &Class{Name: name, Bases: bases, NS: map[string]Value{
			"__module__": "builtins", "__qualname__": name,
		}}
		if parent != "" {
			c.MRO = append([]*Class{c}, m[parent].MRO...)
		} else {
			c.MRO = []*Class{c, objectClass}
		}
		m[name] = c
	}
	m["BaseException"].NS["__init__"] = &Builtin{
		Name: "__init__",
		Fn: func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			self := args[0].(*Instance)
			self.dictSet("args", &Tuple{Items: append([]Value{}, args[1:]...)})
			if (self.Class.Name == "UnicodeEncodeError" || self.Class.Name == "UnicodeDecodeError") && len(args) == 6 {
				for i, name := range []string{"encoding", "object", "start", "end", "reason"} {
					self.dictSet(name, args[i+1])
				}
			}
			if self.Class.Name == "UnicodeTranslateError" && len(args) == 5 {
				for i, name := range []string{"object", "start", "end", "reason"} {
					self.dictSet(name, args[i+1])
				}
			}
			if self.Class.IsSub(m["OSError"]) && len(args) >= 3 {
				self.dictSet("errno", args[1])
				self.dictSet("strerror", args[2])
				if len(args) >= 4 {
					self.dictSet("filename", args[3])
				}
			}
			if self.Class.IsSub(m["StopIteration"]) {
				self.dictSet("value", None)
				if len(args) > 1 {
					self.dictSet("value", args[1])
				}
			}
			return None, nil
		},
	}
	return m
}

func init() {
	objectSetattr = bi("__setattr__", biObjectSetattr)
	objectDelattr = bi("__delattr__", biObjectDelattr)
	objectClass.NS["__setattr__"] = objectSetattr
	objectClass.NS["__delattr__"] = objectDelattr
	objectHash = bi("__hash__", biObjectHash)
	objectClass.NS["__hash__"] = objectHash
	m := excClasses
	// ExceptionGroup inherits from BaseExceptionGroup and Exception (C3).
	eg := &Class{Name: "ExceptionGroup",
		Bases: []*Class{m["BaseExceptionGroup"], m["Exception"]},
		NS:    map[string]Value{"__module__": "builtins", "__qualname__": "ExceptionGroup"}}
	eg.MRO = []*Class{eg, m["BaseExceptionGroup"], m["Exception"], m["BaseException"], objectClass}
	m["ExceptionGroup"] = eg
	groupInit := &Builtin{Name: "__init__", Fn: groupInitFn}
	m["BaseExceptionGroup"].NS["__init__"] = groupInit
	eg.NS["__init__"] = groupInit
	initializeSyntaxErrorClasses(m)
}

// groupInitFn validates and populates an (Base)ExceptionGroup instance.
func groupInitFn(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) != 3 {
		return nil, newError("TypeError", "BaseExceptionGroup() takes exactly 2 arguments")
	}
	self := args[0].(*Instance)
	msg, ok := args[1].(string)
	if !ok {
		return nil, newErrorf("TypeError", "argument 1 must be str, not %s", typeName(args[1]))
	}
	items, err := vm.iterToSlice(args[2])
	if err != nil {
		return nil, newError("TypeError", "second argument (exceptions) must be a sequence")
	}
	if len(items) == 0 {
		return nil, newError("ValueError",
			"second argument (exceptions) must be a non-empty sequence")
	}
	for _, e := range items {
		if inst, ok := e.(*Instance); !ok || !inst.Class.IsSub(baseExc) {
			return nil, newError("ValueError",
				"second argument (exceptions) must be a sequence of exceptions")
		}
	}
	self.dictSet("args", &Tuple{Items: []Value{msg, &List{Items: items}}})
	self.dictSet("message", msg)
	self.dictSet("exceptions", &Tuple{Items: items})
	return None, nil
}

// makeGroup builds an (Base)ExceptionGroup instance directly (used by
// except* subgroup splitting).
func makeGroup(message string, excs []Value, base bool) *Instance {
	name := "ExceptionGroup"
	if base {
		for _, e := range excs {
			if inst, ok := e.(*Instance); ok && !inst.Class.IsSub(excClasses["Exception"]) {
				name = "BaseExceptionGroup"
				break
			}
		}
	}
	items := append([]Value{}, excs...)
	instance := newInstance(excClasses[name], nil)
	instance.dictSet("args", &Tuple{Items: []Value{message, &List{Items: items}}})
	instance.dictSet("message", message)
	instance.dictSet("exceptions", &Tuple{Items: items})
	return instance
}

// makeExc builds an exception instance of the named builtin class.
func makeExc(name string, args ...Value) *Instance {
	cls, ok := excClasses[name]
	if !ok {
		cls = excClasses["RuntimeError"]
	}
	instance := newInstance(cls, nil)
	instance.dictSet("args", &Tuple{Items: args})
	if syntaxErrorClass != nil && cls.IsSub(syntaxErrorClass) && len(args) != 0 {
		setSyntaxErrorField(instance, 0, args[0])
	}
	if name == "StopIteration" {
		instance.dictSet("value", None)
		if len(args) != 0 {
			instance.dictSet("value", args[0])
		}
	}
	return instance
}

// newError builds a Raised carrying a builtin exception with a str message.
func newError(name, msg string) *Raised {
	return &Raised{Exc: makeExc(name, msg)}
}

// newErrorf is newError with fmt.Sprintf formatting.
func newErrorf(name, format string, a ...any) *Raised {
	return newError(name, fmt.Sprintf(format, a...))
}

// newErrorArgs builds a Raised whose exception carries arbitrary args.
func newErrorArgs(name string, args ...Value) *Raised {
	return &Raised{Exc: makeExc(name, args...)}
}

func newUnicodeError(name, encoding string, object Value, start, end int64, reason string) *Raised {
	exc := makeExc(name, encoding, object, start, end, reason)
	exc.dictSet("encoding", encoding)
	exc.dictSet("object", object)
	exc.dictSet("start", start)
	exc.dictSet("end", end)
	exc.dictSet("reason", reason)
	return &Raised{Exc: exc}
}

// excIsA reports whether v is an instance of the named builtin exception.
func excIsA(v Value, name string) bool {
	inst, ok := v.(*Instance)
	if !ok {
		return false
	}
	cls, ok := excClasses[name]
	return ok && inst.Class.IsSub(cls)
}

// raisedAs returns the *Raised if err is one whose exception is an instance
// of the named class.
func raisedAs(err error, name string) (*Raised, bool) {
	var r *Raised
	if errors.As(err, &r) && excIsA(r.Exc, name) {
		return r, true
	}
	return nil, false
}
