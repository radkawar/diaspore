package interp

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"strconv"
	"strings"
	"sync"
)

type node = map[string]any

// VM is an embeddable Diaspore virtual machine executing one bundle.
type VM struct {
	main            string
	argv            []string
	bindings        map[string]map[string]NativeFunc
	caps            map[string]bool
	maxSteps        int64 // 0 = unlimited
	maxDepth        int
	steps           int64 // guarded by the gil / handoff ordering
	depth           int
	gil             sync.Mutex // the interpreter lock (spec 11.9)
	stdout          io.Writer
	stderr          io.Writer
	stdin           *bufio.Reader
	modules         map[string]Value
	modcode         map[string]execFn
	modCells        map[string]map[string]*cell
	modMeta         map[string]moduleMeta
	bundle          map[string]any
	policy          map[string]map[string][]string
	fsOverlay       map[string]string
	durable         bool
	determin        bool
	journal         []jEntry
	replayCursor    int
	replaying       bool
	snippetCompiler func(source, mode string) ([]any, error)
	execSeq         int
	excStack        []Value
	repring         map[Value]bool
	builtins        map[string]Value
	types           map[string]*BuiltinType
	natives         map[string]*NativeModule
	badBundle       error
	tracer          func(TraceEvent)
	trace           traceState
}

// Option configures a VM at construction time.
type Option func(*VM)

// WithBindings registers host functions importable as module `name`.
func WithBindings(name string, fns map[string]NativeFunc) Option {
	return func(vm *VM) {
		m := make(map[string]NativeFunc, len(fns))
		for k, v := range fns {
			m[k] = v
		}
		vm.bindings[name] = m
	}
}

// WithCaps grants capabilities ("net", "fs").
func WithCaps(caps ...string) Option {
	return func(vm *VM) {
		for _, c := range caps {
			vm.caps[c] = true
		}
	}
}

// WithMaxSteps sets the step budget (0 = unlimited).
func WithMaxSteps(n int64) Option { return func(vm *VM) { vm.maxSteps = n } }

// WithMaxDepth sets the maximum diaspore call depth (default 1000).
func WithMaxDepth(n int) Option { return func(vm *VM) { vm.maxDepth = n } }

// WithArgs sets sys.argv (program name first).
func WithArgs(args []string) Option {
	return func(vm *VM) { vm.argv = append([]string{}, args...) }
}

// WithStdout redirects the sandbox's stdout.
func WithStdout(w io.Writer) Option { return func(vm *VM) { vm.stdout = w } }

// WithStderr redirects the sandbox's stderr.
func WithStderr(w io.Writer) Option { return func(vm *VM) { vm.stderr = w } }

// WithStdin redirects the sandbox's stdin (used by input()).
func WithStdin(r io.Reader) Option { return func(vm *VM) { vm.stdin = bufio.NewReader(r) } }

// WithFSOverlay provides an in-memory read-only file overlay consulted by
// open() before the real filesystem - hosts without a filesystem (the wasm
// playground) use it to feed sources to the embedded compiler.
func WithFSOverlay(files map[string]string) Option {
	return func(vm *VM) { vm.fsOverlay = files }
}

// WithPolicy narrows granted capabilities to scoped allowlists (spec 13.1):
// {"net": {"listen": [...], "connect": [...]}, "fs": {"read": [...], "write": [...]}}.
func WithPolicy(policy map[string]map[string][]string) Option {
	return func(vm *VM) { vm.policy = policy }
}

// WithDurable enables durable-flow mode (spec 14): journaled effects and
// flow.suspend(); forbids capabilities.
func WithDurable() Option { return func(vm *VM) { vm.durable = true } }

// WithDeterministic enables deterministic mode (spec 13.1): logical clock,
// fixed-seed RNG; forbids capabilities.
func WithDeterministic() Option { return func(vm *VM) { vm.determin = true } }

// WithSnippetCompiler enables exec()/eval()/compile() by supplying a function
// that compiles diaspore-subset Python source to IR statement nodes. mode is
// "exec" (statements) or "eval" (a single expression, returned as one
// {"op":"expr",...} node). The host wires this to the embedded compiler.
func WithSnippetCompiler(fn func(source, mode string) ([]any, error)) Option {
	return func(vm *VM) { vm.snippetCompiler = fn }
}

// New builds a VM for a decoded bundle (the result of unmarshalling a
// .dsp.json file into map[string]any). The bundle is compiled to closures
// here, once; a malformed bundle is reported by Run / Call rather than here.
func New(bundle map[string]any, opts ...Option) *VM {
	vm := &VM{
		bindings: map[string]map[string]NativeFunc{},
		caps:     map[string]bool{},
		maxDepth: 1000,
		stdout:   os.Stdout,
		stderr:   os.Stderr,
		stdin:    bufio.NewReader(os.Stdin),
		modules:  map[string]Value{},
		modcode:  map[string]execFn{},
		modCells: map[string]map[string]*cell{},
		modMeta:  map[string]moduleMeta{},
		repring:  map[Value]bool{},
	}
	for _, o := range opts {
		o(vm)
	}
	if vm.durable && len(vm.caps) > 0 {
		vm.badBundle = errors.New("durable mode forbids capabilities; route I/O through host bindings")
		return vm
	}
	if vm.determin && len(vm.caps) > 0 {
		vm.badBundle = errors.New("deterministic mode forbids capabilities")
		return vm
	}
	vm.bundle = bundle
	ver, _ := bundle["diaspore"].(float64)
	main, _ := bundle["main"].(string)
	mods, _ := bundle["modules"].(map[string]any)
	if ver != 1 || main == "" || mods == nil {
		vm.badBundle = errors.New("not a diaspore v1 bundle")
		return vm
	}
	vm.main = main
	if vm.argv == nil {
		vm.argv = []string{main}
	}
	vm.builtins = vm.makeBuiltins()
	vm.natives = vm.makeNativeModules()
	if err := vm.addModules(mods); err != nil {
		vm.badBundle = err
	}
	return vm
}

// AddModules registers previously unknown modules from a decoded bundle without
// executing them. Existing modules and their state are left unchanged. Durable
// VMs reject additions because their bundle must remain stable across resumes.
func (vm *VM) AddModules(bundle map[string]any) error {
	vm.gil.Lock()
	defer vm.gil.Unlock()
	if vm.badBundle != nil {
		return vm.badBundle
	}
	if vm.durable {
		return errors.New("cannot add modules to a durable VM")
	}
	ver, _ := bundle["diaspore"].(float64)
	mods, _ := bundle["modules"].(map[string]any)
	if ver != 1 || mods == nil {
		return errors.New("not a diaspore v1 bundle")
	}
	return vm.addModules(mods)
}

func (vm *VM) addModules(mods map[string]any) (err error) {
	codes := make(map[string]execFn)
	metadata := make(map[string]moduleMeta)
	defer func() {
		if err != nil {
			for name := range codes {
				delete(vm.modCells, name)
			}
		}
	}()
	for name, m := range mods {
		if _, exists := vm.modcode[name]; exists {
			continue
		}
		if _, exists := vm.modules[name]; exists {
			continue
		}
		mn, ok := m.(map[string]any)
		if !ok {
			return fmt.Errorf("module %q is not an object", name)
		}
		body, ok := mn["body"].([]any)
		if !ok && mn["body"] != nil {
			return fmt.Errorf("module %q body is not an array", name)
		}
		code, compileErr := compileModule(vm, name, body)
		codes[name] = code
		if compileErr != nil {
			return compileErr
		}
		meta := moduleMeta{file: name}
		if f, ok := mn["file"].(string); ok && f != "" {
			meta.file = f
		}
		if raw, ok := mn["src"].([]any); ok {
			meta.src = make([]string, 0, len(raw))
			for _, line := range raw {
				ls, _ := line.(string)
				meta.src = append(meta.src, ls)
			}
		}
		metadata[name] = meta
	}
	for name, code := range codes {
		vm.modcode[name] = code
		vm.modMeta[name] = metadata[name]
	}
	return nil
}

// setArgv replaces sys.argv before the program runs (used by Resume, which
// restores the argv recorded in the snapshot).
func (vm *VM) setArgv(argv []string) {
	vm.argv = append([]string{}, argv...)
	if sysMod, ok := vm.natives["sys"]; ok {
		items := make([]Value, len(vm.argv))
		for i, a := range vm.argv {
			items[i] = a
		}
		sysMod.NS["argv"] = &List{Items: items}
	}
}

// moduleMeta carries per-module traceback metadata from the bundle.
type moduleMeta struct {
	file string
	src  []string
}

// Steps returns the number of fuel units consumed so far.
func (vm *VM) Steps() int64 {
	vm.gil.Lock()
	defer vm.gil.Unlock()
	return vm.steps
}

// Run executes the bundle's main module and returns the process exit code
// (0 normal, the SystemExit code, 1 for an uncaught exception with a
// traceback on stderr, or the trap code for resource exhaustion).
func (vm *VM) Run() int {
	if vm.badBundle != nil {
		fmt.Fprintf(vm.stderr, "diaspore: %v\n", vm.badBundle)
		return 2
	}
	defer vm.flushOut()
	vm.gil.Lock()
	_, err := vm.importModule(vm.main)
	code := 0
	if err != nil {
		code = vm.ReportError(err)
	}
	vm.gil.Unlock()
	return code
}

// flushOut flushes the stdout writer if it is buffered, so long-running
// programs (servers) log live and no output is lost on exit.
func (vm *VM) flushOut() {
	if f, ok := vm.stdout.(interface{ Flush() error }); ok {
		f.Flush()
	}
}

// ReportError writes an exception traceback or trap to stderr and returns its exit code.
func (vm *VM) ReportError(err error) int {
	var r *Raised
	if errors.As(err, &r) {
		if excIsA(r.Exc, "SystemExit") {
			args := excArgs(r.Exc.(*Instance))
			if len(args) == 0 {
				return 0
			}
			switch code := args[0].(type) {
			case NoneType:
				return 0
			case bool:
				if code {
					return 1
				}
				return 0
			case int64:
				return int(code)
			default:
				s, serr := vm.str(code)
				if serr != nil {
					s = reprNoVM(code)
				}
				fmt.Fprintln(vm.stderr, s)
				return 1
			}
		}
		vm.printTraceback(r)
		return 1
	}
	var t *Trap
	if errors.As(err, &t) {
		fmt.Fprintln(vm.stderr, t.Msg)
		return t.Code
	}
	fmt.Fprintf(vm.stderr, "diaspore: internal error: %v\n", err)
	return 70
}

func (vm *VM) printTraceback(r *Raised) {
	var b strings.Builder
	b.WriteString("Traceback (most recent call last):\n")
	for i := len(r.TB) - 1; i >= 0; i-- {
		e := r.TB[i]
		meta, ok := vm.modMeta[e.module]
		if !ok {
			meta.file = e.module
		}
		fmt.Fprintf(&b, "  File %q, line %d, in %s\n", meta.file, e.line, e.fname)
		if len(meta.src) > 0 && e.line >= 1 && e.line <= len(meta.src) {
			fmt.Fprintf(&b, "    %s\n", strings.TrimSpace(meta.src[e.line-1]))
		}
	}
	name := typeName(r.Exc)
	msg := ""
	if inst, ok := r.Exc.(*Instance); ok {
		name = inst.Class.Name
		if s, err := vm.excStr(inst); err == nil {
			msg = s
		}
	}
	if msg != "" {
		fmt.Fprintf(&b, "%s: %s\n", name, msg)
	} else {
		fmt.Fprintf(&b, "%s\n", name)
	}
	io.WriteString(vm.stderr, b.String())
}

// Call invokes a function inside the sandbox from the host: module-level
// function `fn` in module `module` with the given diaspore values. It returns
// the diaspore result, or an error (a *Raised for diaspore exceptions).
func (vm *VM) Call(module, fn string, args ...Value) (Value, error) {
	if vm.badBundle != nil {
		return nil, vm.badBundle
	}
	defer vm.flushOut()
	vm.gil.Lock()
	defer vm.gil.Unlock()
	mod, err := vm.importModule(module)
	if err != nil {
		return nil, err
	}
	f, err := vm.getAttr(mod, fn)
	if err != nil {
		return nil, err
	}
	return vm.call(f, append([]Value{}, args...), nil)
}

// Str renders str(v) for a diaspore value - a host-side convenience for
// printing results returned by Call.
func (vm *VM) Str(v Value) (string, error) { return vm.str(v) }

// Repr renders repr(v) for a diaspore value.
func (vm *VM) Repr(v Value) (string, error) { return vm.repr(v) }

func (vm *VM) fuel() error {
	vm.steps++
	if vm.steps > vm.maxSteps && vm.maxSteps > 0 {
		return &Trap{Msg: fmt.Sprintf("diaspore: step budget exceeded (%d)", vm.maxSteps), Code: 3}
	}
	return nil
}

// fuelN charges for bulk work done inside host-speed loops (seq*n, sorted,
// list(range(n)), ...) so wide operations cannot sidestep the step budget.
func (vm *VM) fuelN(n int64) error {
	vm.steps += n
	if vm.steps > vm.maxSteps && vm.maxSteps > 0 {
		return &Trap{Msg: fmt.Sprintf("diaspore: step budget exceeded (%d)", vm.maxSteps), Code: 3}
	}
	return nil
}

// smallInts pre-boxes the hot int range so arithmetic results in [-5, 1024]
// do not allocate an interface box.
var smallInts = func() [1030]Value {
	var vs [1030]Value
	for i := range vs {
		vs[i] = int64(i - 5)
	}
	return vs
}()

func mkInt(n int64) Value {
	if n >= -5 && n < 1025 {
		return smallInts[n+5]
	}
	return n
}

func (vm *VM) importModule(name string) (Value, error) {
	if m, ok := vm.modules[name]; ok {
		return m, nil
	}
	if code, ok := vm.modcode[name]; ok {
		var parent *Module
		if dot := strings.LastIndexByte(name, '.'); dot >= 0 {
			// Packages: ancestors execute first (each once, cached).
			pv, err := vm.importModule(name[:dot])
			if err != nil {
				return nil, err
			}
			parent, _ = pv.(*Module)
		}
		cells := vm.modCells[name]
		fr := &frame{
			cells:      cells,
			fname:      "<module>",
			moduleName: name,
		}
		fr.globals = cells
		nameCell, ok := cells["__name__"]
		if !ok {
			nameCell = &cell{}
			cells["__name__"] = nameCell
		}
		if name == vm.main {
			nameCell.v = "__main__"
		} else {
			nameCell.v = name
		}
		mod := &Module{Name: name, frame: fr}
		vm.modules[name] = mod
		if err := code(fr); err != nil {
			var r *Raised
			if errors.As(err, &r) {
				r.TB = append(r.TB, tbEntry{name, "<module>", fr.line})
			}
			return nil, err
		}
		if parent != nil {
			// The submodule becomes an attribute of its parent package.
			leaf := name[strings.LastIndexByte(name, '.')+1:]
			if err := vm.setAttr(parent, leaf, mod); err != nil {
				return nil, err
			}
		}
		return mod, nil
	}
	if fns, ok := vm.bindings[name]; ok {
		ns := map[string]Value{}
		for fname, fn := range fns {
			f := fn
			ns[fname] = vm.journaled(name, fname, &Builtin{Name: fname, Fn: func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if len(kw) > 0 {
					return nil, newErrorf("TypeError", "%s() takes no keyword arguments", fname)
				}
				v, err := f(args)
				if err != nil {
					var r *Raised
					if errors.As(err, &r) {
						return nil, err
					}
					return nil, newError("RuntimeError", err.Error())
				}
				if v == nil {
					return None, nil
				}
				return v, nil
			}})
		}
		mod := &NativeModule{Name: name, NS: ns}
		vm.modules[name] = mod
		return mod, nil
	}
	if mod, ok := vm.natives[name]; ok {
		vm.modules[name] = mod
		return mod, nil
	}
	return nil, newErrorf("ModuleNotFoundError", "No module named '%s'", name)
}

// cell is one module-global binding: loads and stores resolve to the cell
// pointer at compile time, so module-scope variables cost a pointer chase
// instead of a map operation. v == nil means unbound.
type cell struct{ v Value }

// frame is one runtime scope. Function frames are slot-indexed (slots +
// layout); module frames are cell-based; class frames are map-based (vars).
type frame struct {
	slots      []Value
	vars       map[string]Value // class frames only
	varOrder   []string         // class frames: first-assignment order
	cells      map[string]*cell // module frames only
	layout     map[string]int
	parent     *frame
	globals    map[string]*cell // the defining module's cells
	gen        *genChannel
	ret        Value // return value in flight (errReturn)
	line       int
	fname      string
	moduleName string
	defclass   *Class
	self       Value
}

func nStr(n node, k string) string {
	s, _ := n[k].(string)
	return s
}

func nNode(n node, k string) node {
	m, _ := n[k].(map[string]any)
	return m
}

func nList(n node, k string) []any {
	l, _ := n[k].([]any)
	return l
}

func nStrs(n node, k string) []string {
	raw := nList(n, k)
	out := make([]string, 0, len(raw))
	for _, x := range raw {
		if s, ok := x.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func strSet(items []string) map[string]bool {
	m := make(map[string]bool, len(items))
	for _, s := range items {
		m[s] = true
	}
	return m
}

// makeFunction instantiates a compiled function at its definition site:
// defaults are evaluated now, in the defining scope.
func (vm *VM) makeFunction(cf *compiledFunc, fr *frame) (*Function, error) {
	closure := fr
	for i := 0; i < cf.classHops; i++ {
		closure = closure.parent
	}
	fn := &Function{cf: cf, closure: closure}
	if len(cf.defaults) > 0 {
		fn.defaults = make([]Value, len(cf.defaults))
		for i, dFn := range cf.defaults {
			v, err := dFn(fr)
			if err != nil {
				return nil, err
			}
			fn.defaults[i] = v
		}
	}
	if len(cf.params.kwonly) > 0 {
		fn.kwdefaults = make(map[string]Value, len(cf.params.kwonly))
		for i, dFn := range cf.kwdefaults {
			if dFn != nil {
				v, err := dFn(fr)
				if err != nil {
					return nil, err
				}
				fn.kwdefaults[cf.params.kwonly[i]] = v
			}
		}
	}
	return fn, nil
}

func (vm *VM) call(f Value, args []Value, kwargs []KwArg) (Value, error) {
	if err := vm.fuel(); err != nil {
		return nil, err
	}
	switch t := f.(type) {
	case *Function:
		return vm.callFunction(t, args, kwargs)
	case *BoundMethod:
		return vm.call(t.Fn, append([]Value{t.Recv}, args...), kwargs)
	case *Class:
		return vm.instantiate(t, args, kwargs)
	case *BuiltinType:
		return t.ctor(vm, args, kwargs)
	case *Builtin:
		return t.Fn(vm, args, kwargs)
	case *Instance:
		if _, ok := t.Class.Lookup("__call__"); ok {
			return vm.callDunderKw(t, "__call__", args, kwargs)
		}
		return nil, newErrorf("TypeError", "'%s' object is not callable", t.Class.Name)
	case *superMarkerT:
		return nil, newError("RuntimeError", "super(): no arguments and no enclosing method")
	}
	return nil, newErrorf("TypeError", "'%s' object is not callable", typeName(f))
}

func (vm *VM) callFunction(fn *Function, args []Value, kwargs []KwArg) (Value, error) {
	cf := fn.cf
	fr := cf.getFrame()
	fr.parent = fn.closure
	fr.defclass = fn.defclass
	if fn.closure != nil {
		fr.globals = fn.closure.globals
	}
	if err := vm.bindArgs(fn, args, kwargs, fr.slots); err != nil {
		cf.putFrame(fr)
		return nil, err
	}
	if len(cf.params.args) > 0 {
		fr.self = fr.slots[cf.params.argIdx[0]]
	} else {
		fr.self = nil
	}
	if cf.gen {
		return newGenerator(vm, fn, fr), nil
	}
	return vm.runFrame(cf, fr)
}

// runFrame executes a compiled body in a bound frame and translates the
// control-flow result (shared by the generic and fast call paths).
func (vm *VM) runFrame(cf *compiledFunc, fr *frame) (Value, error) {
	if vm.depth >= vm.maxDepth {
		cf.putFrame(fr)
		return nil, newError("RecursionError", "maximum recursion depth exceeded")
	}
	vm.depth++
	if vm.tracer != nil {
		vm.traceCall(fr)
	}
	err := cf.body(fr)
	if vm.tracer != nil {
		vm.traceReturn(fr, err)
	}
	vm.depth--
	if err == nil {
		cf.putFrame(fr)
		return None, nil
	}
	if err == errReturn {
		ret := fr.ret
		cf.putFrame(fr)
		return ret, nil
	}
	var r *Raised
	if errors.As(err, &r) {
		r.TB = append(r.TB, tbEntry{fr.moduleName, cf.name, fr.line})
	}
	cf.putFrame(fr)
	return nil, err
}

func plural(n int, s string) string {
	if n == 1 {
		return s
	}
	return s + "s"
}

func nameList(names []string) string {
	quoted := make([]string, len(names))
	for i, n := range names {
		quoted[i] = "'" + n + "'"
	}
	switch len(quoted) {
	case 1:
		return quoted[0]
	case 2:
		return quoted[0] + " and " + quoted[1]
	}
	return strings.Join(quoted[:len(quoted)-1], ", ") + ", and " + quoted[len(quoted)-1]
}

// bindArgs fills a fresh slot array from the call's arguments (Python
// binding semantics; slots start nil = unbound).
func (vm *VM) bindArgs(fn *Function, args []Value, kwargs []KwArg, slots []Value) error {
	cf := fn.cf
	p := cf.params
	names := p.args
	pos := args
	if len(args) > len(names) {
		if p.varargIdx >= 0 {
			slots[p.varargIdx] = &Tuple{Items: append([]Value{}, args[len(names):]...)}
			pos = args[:len(names)]
		} else {
			n := len(names)
			haveDefaults := len(fn.defaults) > 0
			var took string
			if haveDefaults {
				took = fmt.Sprintf("from %d to %d", n-len(fn.defaults), n)
			} else {
				took = strconv.Itoa(n)
			}
			sfx := "s"
			if n == 1 && !haveDefaults {
				sfx = ""
			}
			verb := "were"
			if len(args) == 1 {
				verb = "was"
			}
			return newErrorf("TypeError", "%s() takes %s positional argument%s but %d %s given",
				cf.name, took, sfx, len(args), verb)
		}
	} else if p.varargIdx >= 0 {
		slots[p.varargIdx] = &Tuple{}
	}
	for i, v := range pos {
		slots[p.argIdx[i]] = v
	}
	if len(kwargs) > 0 {
		var extra []KwArg
	kwLoop:
		for _, kw := range kwargs {
			for i, n := range names {
				if n == kw.Name {
					if slots[p.argIdx[i]] != nil {
						return newErrorf("TypeError", "%s() got multiple values for argument '%s'",
							cf.name, kw.Name)
					}
					slots[p.argIdx[i]] = kw.Val
					continue kwLoop
				}
			}
			for i, n := range p.kwonly {
				if n == kw.Name {
					slots[p.kwonlyIdx[i]] = kw.Val
					continue kwLoop
				}
			}
			if p.kwargIdx >= 0 {
				extra = append(extra, kw)
				continue
			}
			return newErrorf("TypeError", "%s() got an unexpected keyword argument '%s'",
				cf.name, kw.Name)
		}
		if p.kwargIdx >= 0 {
			d := NewDict()
			for _, kw := range extra {
				d.SetStr(kw.Name, kw.Val)
			}
			slots[p.kwargIdx] = d
		}
	} else if p.kwargIdx >= 0 {
		slots[p.kwargIdx] = NewDict()
	}
	nd := len(fn.defaults)
	nreq := len(names) - nd
	var missing []string
	for i := 0; i < nreq; i++ {
		if slots[p.argIdx[i]] == nil {
			missing = append(missing, names[i])
		}
	}
	if len(missing) > 0 {
		return newErrorf("TypeError", "%s() missing %d required positional %s: %s",
			cf.name, len(missing), plural(len(missing), "argument"), nameList(missing))
	}
	for i := 0; i < nd; i++ {
		if slots[p.argIdx[nreq+i]] == nil {
			slots[p.argIdx[nreq+i]] = fn.defaults[i]
		}
	}
	var missKw []string
	for i, n := range p.kwonly {
		if slots[p.kwonlyIdx[i]] == nil {
			if d, ok := fn.kwdefaults[n]; ok {
				slots[p.kwonlyIdx[i]] = d
			} else {
				missKw = append(missKw, n)
			}
		}
	}
	if len(missKw) > 0 {
		return newErrorf("TypeError", "%s() missing %d required keyword-only %s: %s",
			cf.name, len(missKw), plural(len(missKw), "argument"), nameList(missKw))
	}
	return nil
}

func (vm *VM) instantiate(cls *Class, args []Value, kwargs []KwArg) (Value, error) {
	inst := &Instance{Class: cls, D: map[string]Value{}}
	if cls.Prim != nil {
		base := cls.Prim.Name
		switch base {
		case "str", "bytes", "int", "float", "tuple", "bool":
			// Immutable primitives take their value from the constructor args.
			if len(args) > 0 || len(kwargs) > 0 {
				pv, err := cls.Prim.ctor(vm, args, kwargs)
				if err != nil {
					return nil, err
				}
				inst.Prim = pv
			} else {
				inst.Prim = primEmpty(base)
			}
		default:
			inst.Prim = primEmpty(base)
		}
	}
	init, ok := cls.Lookup("__init__")
	if !ok {
		if len(args) > 0 || len(kwargs) > 0 {
			if cls.Prim != nil && (cls.Prim.Name == "dict" || cls.Prim.Name == "list" || cls.Prim.Name == "set") {
				pv, err := cls.Prim.ctor(vm, args, kwargs)
				if err != nil {
					return nil, err
				}
				inst.Prim = pv
				return inst, nil
			}
			if cls.Prim == nil {
				return nil, newErrorf("TypeError", "%s() takes no arguments", cls.Name)
			}
		}
		return inst, nil
	}
	r, err := vm.call(vm.bindAttr(init, inst, cls), args, kwargs)
	if err != nil {
		return nil, err
	}
	if _, isNone := r.(NoneType); !isNone {
		return nil, newErrorf("TypeError", "__init__() should return None, not '%s'", typeName(r))
	}
	return inst, nil
}

// primEmpty returns an empty primitive value of the given builtin base name.
func primEmpty(base string) Value {
	switch base {
	case "dict":
		return NewDict()
	case "list":
		return &List{}
	case "set":
		return NewSet()
	case "tuple":
		return &Tuple{}
	case "str":
		return ""
	case "bytes":
		return []byte{}
	case "int", "bool":
		return int64(0)
	case "float":
		return float64(0)
	}
	return None
}

func (vm *VM) callDunder(obj *Instance, name string, args []Value) (Value, error) {
	return vm.callDunderKw(obj, name, args, nil)
}

func (vm *VM) callDunderKw(obj *Instance, name string, args []Value, kwargs []KwArg) (Value, error) {
	fn, ok := obj.Class.Lookup(name)
	if !ok {
		return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'",
			obj.Class.Name, name)
	}
	return vm.call(vm.bindAttr(fn, obj, obj.Class), args, kwargs)
}

func (vm *VM) callDunderOrErr(obj *Instance, name string, args []Value, errmsg string) (Value, error) {
	if _, ok := obj.Class.Lookup(name); !ok {
		return nil, newError("TypeError", errmsg)
	}
	return vm.callDunder(obj, name, args)
}

func (vm *VM) makeSuper(args []Value, fr *frame) (Value, error) {
	if len(args) == 0 {
		if fr.defclass == nil || fr.self == nil {
			return nil, newError("RuntimeError", "super(): __class__ cell not found")
		}
		return &Super{Cls: fr.defclass, Obj: fr.self}, nil
	}
	if len(args) == 2 {
		if cls, ok := args[0].(*Class); ok {
			return &Super{Cls: cls, Obj: args[1]}, nil
		}
	}
	return nil, newError("TypeError", "super() argument error")
}

func (vm *VM) bindAttr(v Value, inst Value, cls *Class) Value {
	switch t := v.(type) {
	case *Function:
		return &BoundMethod{Fn: t, Recv: inst}
	case *StaticMethod:
		return t.Fn
	case *ClassMethod:
		return &BoundMethod{Fn: t.Fn, Recv: cls}
	case *Builtin:
		return &BoundMethod{Fn: t, Recv: inst}
	}
	return v
}

func (vm *VM) getAttr(o Value, n string) (Value, error) {
	switch t := o.(type) {
	case *Instance:
		if n == "__class__" {
			return t.Class, nil
		}
		if v, ok := t.D[n]; ok {
			return v, nil
		}
		v, found := t.Class.Lookup(n)
		if prop, ok := v.(*Property); ok {
			if prop.Fget == nil {
				return nil, newErrorf("AttributeError",
					"property '%s' of '%s' object has no getter", n, t.Class.Name)
			}
			return vm.call(vm.bindAttr(prop.Fget, t, t.Class), nil, nil)
		}
		if found {
			return vm.bindAttr(v, t, t.Class), nil
		}
		if t.Prim != nil {
			if m, err := vm.builtinMethod(t.Prim, n); err == nil {
				return m, nil
			}
		}
		if ga, ok := t.Class.Lookup("__getattr__"); ok {
			return vm.call(vm.bindAttr(ga, t, t.Class), []Value{n}, nil)
		}
		return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'",
			t.Class.Name, n)
	case *Class:
		v, ok := t.Lookup(n)
		if !ok {
			switch n {
			case "__name__", "__qualname__":
				return t.Name, nil
			case "__mro__":
				items := make([]Value, len(t.MRO))
				for i, c := range t.MRO {
					items[i] = c
				}
				return &Tuple{Items: items}, nil
			case "__bases__":
				if len(t.Bases) == 0 {
					return &Tuple{Items: []Value{objectClass}}, nil
				}
				items := make([]Value, len(t.Bases))
				for i, c := range t.Bases {
					items[i] = c
				}
				return &Tuple{Items: items}, nil
			case "__dict__":
				d := NewDict()
				for k, val := range t.NS {
					d.SetStr(k, val)
				}
				return d, nil
			}
			return nil, newErrorf("AttributeError", "type object '%s' has no attribute '%s'",
				t.Name, n)
		}
		switch w := v.(type) {
		case *ClassMethod:
			return &BoundMethod{Fn: w.Fn, Recv: t}, nil
		case *StaticMethod:
			return w.Fn, nil
		}
		return v, nil
	case *Super:
		var mro []*Class
		switch obj := t.Obj.(type) {
		case *Instance:
			mro = obj.Class.MRO
		case *Class:
			mro = obj.MRO
		}
		start := 0
		for i, c := range mro {
			if c == t.Cls {
				start = i + 1
				break
			}
		}
		for _, c := range mro[start:] {
			if v, ok := c.NS[n]; ok {
				return vm.bindAttr(v, t.Obj, c), nil
			}
		}
		return nil, newErrorf("AttributeError", "'super' object has no attribute '%s'", n)
	case *Module:
		if c, ok := t.frame.cells[n]; ok && c.v != nil {
			return c.v, nil
		}
		return nil, newErrorf("AttributeError", "module '%s' has no attribute '%s'", t.Name, n)
	case *NativeModule:
		if v, ok := t.NS[n]; ok {
			return v, nil
		}
		return nil, newErrorf("AttributeError", "module '%s' has no attribute '%s'", t.Name, n)
	case *Generator:
		switch n {
		case "send":
			return bi("send", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := argCount("send", args, 1, 1); err != nil {
					return nil, err
				}
				return t.Send(args[0])
			}), nil
		case "throw":
			return bi("throw", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := argCount("throw", args, 1, 1); err != nil {
					return nil, err
				}
				exc := args[0]
				if cls, ok := exc.(*Class); ok {
					if !cls.IsSub(baseExc) {
						return nil, newError("TypeError",
							"exceptions must derive from BaseException")
					}
					inst, err := vm.instantiate(cls, nil, nil)
					if err != nil {
						return nil, err
					}
					exc = inst
				}
				if inst, ok := exc.(*Instance); !ok || !inst.Class.IsSub(baseExc) {
					return nil, newError("TypeError",
						"exceptions must derive from BaseException")
				}
				return t.Throw(exc)
			}), nil
		case "close":
			return bi("close", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := argCount("close", args, 0, 0); err != nil {
					return nil, err
				}
				if err := t.Close(); err != nil {
					return nil, err
				}
				return None, nil
			}), nil
		}
		return nil, newErrorf("AttributeError", "'generator' object has no attribute '%s'", n)
	case *File:
		return t.method(n)
	case *NativeObject:
		if !strings.HasPrefix(n, ".") && !strings.HasPrefix(n, "__") {
			if m, ok := t.methods[n]; ok {
				return m, nil
			}
		}
		return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", t.Kind, n)
	case *Property:
		switch n {
		case "setter":
			return bi("setter", func(vm *VM, a []Value, kw []KwArg) (Value, error) {
				return &Property{Fget: t.Fget, Fset: a[0], Fdel: t.Fdel, Doc: t.Doc}, nil
			}), nil
		case "getter":
			return bi("getter", func(vm *VM, a []Value, kw []KwArg) (Value, error) {
				return &Property{Fget: a[0], Fset: t.Fset, Fdel: t.Fdel, Doc: t.Doc}, nil
			}), nil
		case "deleter":
			return bi("deleter", func(vm *VM, a []Value, kw []KwArg) (Value, error) {
				return &Property{Fget: t.Fget, Fset: t.Fset, Fdel: a[0], Doc: t.Doc}, nil
			}), nil
		case "fget":
			return orNone(t.Fget), nil
		case "fset":
			return orNone(t.Fset), nil
		case "fdel":
			return orNone(t.Fdel), nil
		}
		return nil, newErrorf("AttributeError", "'property' object has no attribute '%s'", n)
	case *Function:
		if v, ok := t.attrs[n]; ok {
			return v, nil
		}
		switch n {
		case "__name__":
			return funcName(t), nil
		case "__doc__":
			return orNone(t.attrs["__doc__"]), nil
		case "__dict__":
			d := NewDict()
			for k, val := range t.attrs {
				d.SetStr(k, val)
			}
			return d, nil
		case "__qualname__":
			return funcName(t), nil
		}
		return nil, newErrorf("AttributeError", "'function' object has no attribute '%s'", n)
	case *BoundMethod:
		switch n {
		case "__name__":
			return callableName(t.Fn), nil
		case "__self__":
			return t.Recv, nil
		case "__func__":
			return t.Fn, nil
		}
		return vm.getAttr(t.Fn, n)
	case *Slice:
		switch n {
		case "start":
			return t.Lower, nil
		case "stop":
			return t.Upper, nil
		case "step":
			return t.Step, nil
		case "indices":
			return bi("indices", func(vm *VM, a []Value, kw []KwArg) (Value, error) {
				if err := argCount("indices", a, 1, 1); err != nil {
					return nil, err
				}
				ln, ok := asInt(a[0])
				if !ok {
					return nil, newError("TypeError", "slice indices must be integers")
				}
				start, stop, step, err := sliceIndices(t, int(ln))
				if err != nil {
					return nil, err
				}
				return &Tuple{Items: []Value{mkInt(int64(start)), mkInt(int64(stop)), mkInt(int64(step))}}, nil
			}), nil
		}
		return nil, newErrorf("AttributeError", "'slice' object has no attribute '%s'", n)
	case complex128:
		switch n {
		case "real":
			return real(t), nil
		case "imag":
			return imag(t), nil
		case "conjugate":
			return bi("conjugate", func(vm *VM, a []Value, kw []KwArg) (Value, error) {
				return complex(real(t), -imag(t)), nil
			}), nil
		}
		return nil, newErrorf("AttributeError", "'complex' object has no attribute '%s'", n)
	case *BuiltinType:
		if n == "__name__" {
			return t.Name, nil
		}
		if u := vm.builtinUnbound(t.Name, n); u != nil {
			return u, nil
		}
		return nil, newErrorf("AttributeError", "type object '%s' has no attribute '%s'", t.Name, n)
	}
	if u := vm.numericAttr(o, n); u != nil {
		return u, nil
	}
	return vm.builtinMethod(o, n)
}

func orNone(v Value) Value {
	if v == nil {
		return None
	}
	return v
}

func funcName(f *Function) string {
	if f.name != "" {
		return f.name
	}
	return f.cf.name
}

// numericAttr handles int/float attribute access (real/imag/conjugate/...).
func (vm *VM) numericAttr(o Value, n string) Value {
	_, isInt := o.(int64)
	_, isBool := o.(bool)
	fv, isFloat := o.(float64)
	if !isInt && !isBool && !isFloat {
		return nil
	}
	switch n {
	case "real":
		return o
	case "imag":
		if isFloat {
			return float64(0)
		}
		return mkInt(0)
	case "conjugate":
		return bi("conjugate", func(vm *VM, a []Value, kw []KwArg) (Value, error) { return o, nil })
	case "numerator":
		if isFloat {
			return nil
		}
		return o
	case "denominator":
		if isFloat {
			return nil
		}
		return mkInt(1)
	case "bit_length":
		if isFloat {
			return nil
		}
		iv, _ := asInt(o)
		return bi("bit_length", func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			x := iv
			if x < 0 {
				x = -x
			}
			n := int64(0)
			for x > 0 {
				n++
				x >>= 1
			}
			return mkInt(n), nil
		})
	case "is_integer":
		if !isFloat {
			return nil
		}
		return bi("is_integer", func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			return !math.IsInf(fv, 0) && !math.IsNaN(fv) && fv == math.Trunc(fv), nil
		})
	}
	return nil
}

func (vm *VM) setAttr(o Value, n string, v Value) error {
	switch t := o.(type) {
	case *Function:
		if n == "__name__" {
			s, ok := v.(string)
			if !ok {
				return newError("TypeError", "__name__ must be set to a string")
			}
			t.name = s
			return nil
		}
		if t.attrs == nil {
			t.attrs = map[string]Value{}
		}
		t.attrs[n] = v
		return nil
	case *Instance:
		if prop, ok := t.Class.Lookup(n); ok {
			if p, isProp := prop.(*Property); isProp {
				if p.Fset == nil {
					return newErrorf("AttributeError",
						"property '%s' of '%s' object has no setter", n, t.Class.Name)
				}
				_, err := vm.call(vm.bindAttr(p.Fset, t, t.Class), []Value{v}, nil)
				return err
			}
		}
		t.D[n] = v
		return nil
	case *Class:
		t.NS[n] = v
		return nil
	case *Module:
		c, ok := t.frame.cells[n]
		if !ok {
			c = &cell{}
			t.frame.cells[n] = c
		}
		c.v = v
		return nil
	}
	return newErrorf("AttributeError", "'%s' object has no attribute '%s'", typeName(o), n)
}

// buildClass is CPython-shaped class creation (reference: vm.py). An
// explicit callable metaclass (not `type`, not a hook-less plain class) is
// called as metaclass(name, bases_tuple, ns_dict, **kwargs); otherwise class
// kwargs dispatch to the nearest base's __init_subclass__.
func (vm *VM) buildClass(name string, bases []*Class, ns map[string]Value,
	nsOrder []string, metaclass Value, kwargs []KwArg, primBase *BuiltinType) (Value, error) {
	plainMeta := metaclass == nil
	if bt, ok := metaclass.(*BuiltinType); ok && bt.Name == "type" {
		plainMeta = true
	}
	if mc, ok := metaclass.(*Class); ok {
		_, hasNew := mc.Lookup("__new__")
		_, hasCall := mc.Lookup("__call__")
		_, hasInit := mc.Lookup("__init__")
		if !hasNew && !hasCall && !hasInit {
			plainMeta = true
		}
	}
	if !plainMeta {
		baseVals := make([]Value, len(bases))
		for i, b := range bases {
			baseVals[i] = b
		}
		nsDict := NewDict()
		for _, k := range nsOrder {
			nsDict.SetStr(k, ns[k])
		}
		result, err := vm.call(metaclass,
			[]Value{name, &Tuple{Items: baseVals}, nsDict}, kwargs)
		if err != nil {
			return nil, err
		}
		if cls, ok := result.(*Class); ok {
			return cls, nil
		}
		return nil, newErrorf("TypeError", "metaclass returned %s, not a class",
			typeName(result))
	}
	cls := &Class{Name: name, Bases: bases, NS: ns, Prim: primBase}
	if cls.Prim == nil {
		for _, b := range bases {
			if b.Prim != nil {
				cls.Prim = b.Prim
				break
			}
		}
	}
	mro, err := c3MRO(cls)
	if err != nil {
		return nil, err
	}
	hasObject := false
	for _, c := range mro {
		if c == objectClass {
			hasObject = true
			break
		}
	}
	if !hasObject {
		mro = append(mro, objectClass)
	}
	cls.MRO = mro
	for _, v := range cls.NS {
		switch t := v.(type) {
		case *Function:
			if t.defclass == nil {
				t.defclass = cls
			}
		case *StaticMethod:
			if fn, ok := t.Fn.(*Function); ok && fn.defclass == nil {
				fn.defclass = cls
			}
		case *ClassMethod:
			if fn, ok := t.Fn.(*Function); ok && fn.defclass == nil {
				fn.defclass = cls
			}
		}
	}
	if len(kwargs) > 0 {
		for _, base := range bases {
			initSub, ok := base.Lookup("__init_subclass__")
			if !ok {
				continue
			}
			if cm, isCM := initSub.(*ClassMethod); isCM {
				initSub = cm.Fn
			}
			if _, err := vm.call(&BoundMethod{Fn: initSub, Recv: cls}, nil, kwargs); err != nil {
				return nil, err
			}
			return cls, nil
		}
		return nil, newErrorf("TypeError",
			"%s.__init_subclass__() takes no keyword arguments", name)
	}
	return cls, nil
}

// runStarHandlers implements PEP 654 except*: each clause matches the
// subgroup of its exception type; unmatched exceptions propagate as a
// residual group.
func (vm *VM) runStarHandlers(fr *frame, r *Raised, handlers []compiledHandler, elseFn execFn) error {
	pending := r.Exc
	for i := range handlers {
		h := &handlers[i]
		var t Value
		if h.matchFn != nil {
			tv, err := h.matchFn(fr)
			if err != nil {
				return err
			}
			t = tv
		}
		match, rest, err := vm.splitGroup(pending, t)
		if err != nil {
			return err
		}
		if match == nil {
			continue
		}
		if h.bind != nil {
			if berr := h.bind(fr, match); berr != nil {
				return berr
			}
		}
		vm.excStack = append(vm.excStack, match)
		herr := h.body(fr)
		vm.excStack = vm.excStack[:len(vm.excStack)-1]
		if h.unbind != nil {
			if uerr := h.unbind(fr); uerr != nil {
				return uerr
			}
		}
		if herr != nil {
			return herr
		}
		pending = rest
		if pending == nil {
			break
		}
	}
	if pending != nil {
		return &Raised{Exc: pending}
	}
	return nil
}

// splitGroup returns (matching_subgroup, residual) for handler type t against
// exc, treating a non-group exception as a singleton group.
func (vm *VM) splitGroup(exc Value, t Value) (match Value, rest Value, err error) {
	var classes []Value
	if tup, ok := t.(*Tuple); ok {
		classes = tup.Items
	} else {
		classes = []Value{t}
	}
	matches := func(e Value) bool {
		inst, ok := e.(*Instance)
		if !ok {
			return false
		}
		for _, cv := range classes {
			if cls, ok := cv.(*Class); ok && inst.Class.IsSub(cls) {
				return true
			}
		}
		return false
	}
	inst, ok := exc.(*Instance)
	if ok && inst.Class.IsSub(excClasses["BaseExceptionGroup"]) {
		var members []Value
		if tup, ok := inst.D["exceptions"].(*Tuple); ok {
			members = tup.Items
		}
		var matched, unmatched []Value
		for _, e := range members {
			if matches(e) {
				matched = append(matched, e)
			} else {
				unmatched = append(unmatched, e)
			}
		}
		if len(matched) == 0 {
			return nil, exc, nil
		}
		msg, _ := inst.D["message"].(string)
		mg := makeGroup(msg, matched, false)
		if len(unmatched) > 0 {
			return mg, makeGroup(msg, unmatched, false), nil
		}
		return mg, nil, nil
	}
	if matches(exc) {
		return makeGroup("", []Value{exc}, false), nil, nil
	}
	return nil, exc, nil
}

// mainFrame returns a frame over the main module's namespace (for indirect
// exec/eval where no calling frame is available).
func (vm *VM) mainFrame() *frame {
	cells := vm.modCells[vm.main]
	if cells == nil {
		cells = map[string]*cell{}
	}
	return &frame{cells: cells, globals: cells, moduleName: vm.main, fname: "<module>"}
}

func (vm *VM) requireCap(cap string) error {
	if !vm.caps[cap] {
		return newErrorf("PermissionError",
			"diaspore: capability '%s' not granted (run with --allow-%s)", cap, cap)
	}
	return nil
}
