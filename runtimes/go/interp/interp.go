package interp

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"math/bits"
	"os"
	"strconv"
	"strings"
	"sync"
)

type node = map[string]any

// VM is an embeddable Diaspore virtual machine executing one bundle.
type VM struct {
	metadata        metadataState
	closed          bool
	suspended       bool
	main            string
	argv            []string
	bindings        map[string]map[string]NativeFunc
	caps            map[string]bool
	maxSteps        int64 // 0 = unlimited
	maxDepth        int
	depthCap        int
	steps           int64 // guarded by the gil / handoff ordering
	depth           int
	gil             sync.Mutex // the interpreter lock (spec 11.9)
	taskID          int64
	nextTaskID      int64
	stdout          io.Writer
	stderr          io.Writer
	stdin           *bufio.Reader
	modules         map[string]Value
	moduleDict      *Dict
	modcode         map[string]execFn
	modCells        map[string]map[string]*cell
	modMeta         map[string]moduleMeta
	closeFDs        func()
	bundle          map[string]any
	stdlib          *stdlibStore
	stdlibID        string
	policy          map[string]map[string][]string
	fsOverlay       map[string]string
	overlayDirs     map[string]bool
	virtualFiles    map[string]string
	durable         bool
	determin        bool
	journal         []jEntry
	replayCursor    int
	replaying       bool
	snippetCompiler func(source, mode string) ([]any, error)
	execSeq         int
	excStack        []Value
	auditHooks      []Value
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

// WithFSOverlay replaces the filesystem with an in-memory, read-only file map.
// Hosts without a filesystem (the wasm playground) use it to feed sources to
// the embedded compiler. Paths are matched exactly; the fs policy still applies.
func WithFSOverlay(files map[string]string) Option {
	return func(vm *VM) { vm.fsOverlay = files }
}

// WithVirtualFiles adds in-memory, read-only files on top of the real
// filesystem. open() serves an exact path match from files and falls back to
// the real filesystem otherwise. The fs policy applies to both.
func WithVirtualFiles(files map[string]string) Option {
	return func(vm *VM) { vm.virtualFiles = files }
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
		bindings:   map[string]map[string]NativeFunc{},
		caps:       map[string]bool{},
		maxDepth:   1000,
		stdout:     os.Stdout,
		stderr:     os.Stderr,
		stdin:      bufio.NewReader(os.Stdin),
		modules:    map[string]Value{},
		moduleDict: NewDict(),
		modcode:    map[string]execFn{},
		modCells:   map[string]map[string]*cell{},
		modMeta:    map[string]moduleMeta{},
		repring:    map[Value]bool{},
	}
	for _, o := range opts {
		o(vm)
	}
	vm.depthCap = vm.maxDepth
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
	store, err := linkedStdlib(bundle)
	if err != nil {
		vm.badBundle = err
		return vm
	}
	vm.stdlib = store
	if store != nil {
		vm.stdlibID = store.id
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
	store, err := linkedStdlib(bundle)
	if err != nil {
		return err
	}
	if err := vm.addModules(mods); err != nil {
		return err
	}
	if store != nil {
		vm.stdlib, vm.stdlibID = store, store.id
	}
	return nil
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
		code, meta, compileErr := vm.compileModuleIR(name, m)
		if compileErr != nil {
			return compileErr
		}
		codes[name] = code
		metadata[name] = meta
	}
	for name, code := range codes {
		vm.modcode[name] = code
		vm.modMeta[name] = metadata[name]
	}
	return nil
}

func (vm *VM) compileModuleIR(name string, raw any) (execFn, moduleMeta, error) {
	mn, ok := raw.(map[string]any)
	if !ok {
		return nil, moduleMeta{}, fmt.Errorf("module %q is not an object", name)
	}
	body, ok := mn["body"].([]any)
	if !ok && mn["body"] != nil {
		return nil, moduleMeta{}, fmt.Errorf("module %q body is not an array", name)
	}
	code, err := compileModule(vm, name, body)
	if err != nil {
		delete(vm.modCells, name)
		return nil, moduleMeta{}, err
	}
	meta := moduleMeta{file: name, source: mn}
	if file, ok := mn["file"].(string); ok {
		meta.file = file
	}
	if raw, ok := mn["src"].([]any); ok {
		meta.src = make([]string, len(raw))
		for i, line := range raw {
			meta.src[i], _ = line.(string)
		}
	}
	return code, meta, nil
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
	file   string
	src    []string
	source node
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

// Close releases VM-owned file descriptors. Call it when the VM is no longer used.
func (vm *VM) Close() {
	vm.gil.Lock()
	defer vm.gil.Unlock()
	if vm.closed {
		return
	}
	vm.closed = true
	if !vm.suspended {
		for _, hook := range [][2]string{{"threading", "_shutdown"}, {"atexit", "_run_exitfuncs"}} {
			if module, imported := vm.moduleDict.GetStr(hook[0]); imported {
				if callback, err := vm.getAttr(module, hook[1]); err == nil {
					if _, err := vm.call(callback, nil, nil); err != nil {
						vm.ReportError(err)
					}
				}
			}
		}
	}
	if vm.closeFDs != nil {
		vm.closeFDs()
		vm.closeFDs = nil
	}
	vm.flushOut()
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
		name = vm.exceptionDisplayName(inst.Class)
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
	if m, ok := vm.moduleDict.GetStr(name); ok {
		if _, halted := m.(NoneType); halted {
			return nil, newErrorf("ModuleNotFoundError", "import of %s halted; None in sys.modules", name)
		}
		vm.modules[name] = m
		return m, nil
	}
	if _, compiled := vm.modcode[name]; !compiled && vm.stdlib != nil {
		if source, found := vm.stdlib.modules[name]; found {
			ir, err := source.load()
			if err != nil {
				return nil, fmt.Errorf("cannot load stdlib module %q: %w", name, err)
			}
			code, meta, err := vm.compileModuleIR(name, ir)
			if err != nil {
				return nil, err
			}
			vm.modcode[name], vm.modMeta[name] = code, meta
		}
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
		fileCell, ok := cells["__file__"]
		if !ok {
			fileCell = &cell{}
			cells["__file__"] = fileCell
		}
		fileCell.v = vm.modMeta[name].file
		docCell, ok := cells["__doc__"]
		if !ok {
			docCell = &cell{}
			cells["__doc__"] = docCell
		}
		docCell.v = None
		mod := &Module{Name: name, frame: fr}
		vm.modules[name] = mod
		vm.moduleDict.SetStr(name, mod)
		if name == vm.main && name != "__main__" {
			vm.modules["__main__"] = mod
			vm.moduleDict.SetStr("__main__", mod)
		}
		vm.enterMetadataFrame(fr)
		defer vm.leaveMetadataFrame(fr)
		if err := code(fr); err != nil {
			delete(vm.modules, name)
			vm.moduleDict.Delete(name)
			if name == vm.main && name != "__main__" {
				delete(vm.modules, "__main__")
				vm.moduleDict.Delete("__main__")
			}
			var r *Raised
			if errors.As(err, &r) {
				vm.recordMetadataTraceback(r, fr)
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
			ns[fname] = vm.journaled(name, fname, &Builtin{Name: fname, NoBind: true, Fn: func(vm *VM, args []Value, kw []KwArg) (Value, error) {
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
		vm.moduleDict.SetStr(name, mod)
		return mod, nil
	}
	if mod, ok := vm.natives[name]; ok {
		vm.modules[name] = mod
		vm.moduleDict.SetStr(name, mod)
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
	metadata      frameMetadata
	slots         []Value
	closureCells  []*lexicalCell   // reconstructed function closure bindings
	vars          map[string]Value // class frames only
	varOrder      []string         // class frames: first-assignment order
	namespace     Value            // custom metaclass-prepared mapping, nil for ordinary classes
	cells         map[string]*cell // module frames only
	layout        map[string]int
	parent        *frame
	globals       map[string]*cell // the defining module's cells
	globalMapping Value            // supplied FunctionType globals, when not cell-backed
	gen           *genChannel
	ret           Value // return value in flight (errReturn)
	line          int
	fname         string
	moduleName    string
	defclass      *Class
	self          Value
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
	fn := &Function{cf: cf, closure: closure, doc: cf.doc}
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
	case *jsonScanner:
		return t.call(vm, args, kwargs)
	case *jsonEncoder:
		return t.call(vm, args, kwargs)
	case *metadataDescriptor:
		return t.call(vm, args, kwargs)
	case *GenericAlias:
		return vm.callGenericAlias(t, args, kwargs)
	case *StaticMethod:
		if t.Fn == nil {
			return nil, newError("RuntimeError", "uninitialized staticmethod object")
		}
		return vm.call(t.Fn, args, kwargs)
	case *Function:
		return vm.callFunction(t, args, kwargs)
	case *BoundMethod:
		return vm.call(t.Fn, append([]Value{t.Recv}, args...), kwargs)
	case *Class:
		if t.Meta != nil {
			if hook, ok := t.Meta.Lookup("__call__"); ok {
				return vm.call(vm.bindAttr(hook, t, t.Meta), args, kwargs)
			}
		}
		return vm.instantiate(t, args, kwargs)
	case *BuiltinType:
		return t.ctor(vm, args, kwargs)
	case *Builtin:
		return t.Fn(vm, args, kwargs)
	case *Instance:
		if _, ok := t.Class.Lookup("__call__"); ok {
			return vm.callDunderKw(t, "__call__", args, kwargs)
		}
		if callableDescriptorKind(t) == "staticmethod" {
			return vm.call(t.Prim, args, kwargs)
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
	if fn.metadata != nil {
		fr.metadata.override = fn.metadata.code
	}
	fr.parent = fn.closure
	fr.defclass = fn.defclass
	if fn.closure != nil {
		fr.globals = fn.closure.globals
		fr.globalMapping = fn.closure.globalMapping
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
	vm.enterMetadataFrame(fr)
	defer vm.leaveMetadataFrame(fr)
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
		vm.recordMetadataTraceback(r, fr)
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
					if i < p.posonly {
						if p.kwargIdx >= 0 {
							extra = append(extra, kw)
							continue kwLoop
						}
						return newErrorf("TypeError", "%s() got some positional-only arguments passed as keyword arguments: '%s'", cf.name, n)
					}
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
	defaultStart := 0
	if nd > len(names) {
		defaultStart = nd - len(names)
		nd = len(names)
	}
	nreq := len(names) - nd
	var missing []string
	for i := range nreq {
		if slots[p.argIdx[i]] == nil {
			missing = append(missing, names[i])
		}
	}
	if len(missing) > 0 {
		return newErrorf("TypeError", "%s() missing %d required positional %s: %s",
			cf.name, len(missing), plural(len(missing), "argument"), nameList(missing))
	}
	for i := range nd {
		if slots[p.argIdx[nreq+i]] == nil {
			slots[p.argIdx[nreq+i]] = fn.defaults[defaultStart+i]
		}
	}
	var missKw []string
	for i, n := range p.kwonly {
		if slots[p.kwonlyIdx[i]] == nil {
			if d, ok := fn.metadataKeywordDefault(n); ok {
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
	if abstract, ok := cls.Lookup("__abstractmethods__"); ok {
		nonempty, err := vm.truth(abstract)
		if err != nil {
			return nil, err
		}
		if nonempty {
			return nil, newErrorf("TypeError", "Can't instantiate abstract class %s", cls.Name)
		}
	}
	if cls.Prim != nil && cls.Prim.Name == "type" {
		return vm.instantiateMeta(cls, args, kwargs)
	}
	if constructor, ok := cls.Lookup("__new__"); ok {
		if sm, ok := constructor.(*StaticMethod); ok {
			constructor = sm.Fn
		}
		value, err := vm.call(constructor, append([]Value{cls}, args...), kwargs)
		if err != nil {
			return nil, err
		}
		instance, ok := value.(*Instance)
		if !ok || !instance.Class.IsSub(cls) {
			return value, nil
		}
		if init, ok := instance.Class.Lookup("__init__"); ok {
			r, err := vm.call(vm.bindAttr(init, instance, instance.Class), args, kwargs)
			if err != nil {
				return nil, err
			}
			if _, ok := r.(NoneType); !ok {
				return nil, newError("TypeError", "__init__() should return None")
			}
		} else if instance.Class.Prim != nil && instance.Class.Prim.Name == "dict" {
			if err := dictInitialize(vm, rawPrim(instance), args, kwargs); err != nil {
				return nil, err
			}
		} else if instance.Class.Prim != nil && instance.Class.Prim.Name == "bytearray" {
			storage, ok := rawPrim(instance).(*ByteArray)
			if !ok {
				return nil, newError("TypeError", "bytearray.__init__ requires a bytearray instance")
			}
			if err := bytearrayInitialize(vm, storage, args, kwargs); err != nil {
				return nil, err
			}
		} else if _, property := propertyValue(instance); property {
			if err := vm.initProperty(instance, args, kwargs); err != nil {
				return nil, err
			}
		} else if callableDescriptorKind(instance) != "" {
			if err := vm.initCallableDescriptor(instance, args, kwargs); err != nil {
				return nil, err
			}
		}
		return instance, nil
	}
	inst := newInstance(cls, nil)
	if cls.Prim != nil {
		base := cls.Prim.Name
		switch base {
		case "str", "bytes", "int", "float", "tuple", "frozenset", "bool", "GenericAlias":
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
		if cls.Prim != nil && cls.Prim.Name == "bytearray" {
			return inst, bytearrayInitialize(vm, inst.Prim.(*ByteArray), args, kwargs)
		}
		if _, property := propertyValue(inst); property {
			return inst, vm.initProperty(inst, args, kwargs)
		}
		if callableDescriptorKind(inst) != "" {
			return inst, vm.initCallableDescriptor(inst, args, kwargs)
		}
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
	case "property":
		return &Property{}
	case "staticmethod":
		return &StaticMethod{}
	case "classmethod":
		return &ClassMethod{}
	case "dict":
		return NewDict()
	case "list":
		return &List{}
	case "set":
		return NewSet()
	case "frozenset":
		return &FrozenSet{set: NewSet()}
	case "tuple":
		return &Tuple{}
	case "str":
		return ""
	case "bytes":
		return []byte{}
	case "bytearray":
		return &ByteArray{}
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
	case *metadataDescriptor:
		if t.kind == "classmethod_descriptor" {
			return t.bind(cls)
		}
		return t.bind(inst)
	case *Function:
		return &BoundMethod{Fn: t, Recv: inst}
	case *StaticMethod:
		return t.Fn
	case *ClassMethod:
		return &BoundMethod{Fn: t.Fn, Recv: cls}
	case *Builtin:
		if t.NoBind {
			return t
		}
		return &BoundMethod{Fn: t, Recv: inst}
	}
	return v
}

func (vm *VM) getAttr(o Value, n string) (Value, error) {
	if _, instance := o.(*Instance); !instance {
		if value, handled, err := vm.metadataAttr(o, n); handled {
			return value, err
		}
	}
	if n == "__class__" {
		if _, instance := o.(*Instance); !instance {
			return ctorType(vm, []Value{o}, nil)
		}
	}
	switch o.(type) {
	case *Instance, *Class, *BuiltinType:
		// User instances and types perform their own MRO lookup below.
	default:
		if owner := vm.types[typeName(o)]; owner != nil {
			if n == "__new__" {
				return vm.primitiveNew(owner), nil
			}
			if descriptor := vm.builtinMetadataDescriptor(owner, n); descriptor != nil && !descriptor.getter {
				if descriptor.kind == "classmethod_descriptor" {
					return descriptor.bind(owner), nil
				}
				return descriptor.bind(o), nil
			}
		}
	}
	switch t := o.(type) {
	case *hashEngine:
		if value, ok := t.attr(n); ok {
			return value, nil
		}
		return nil, newErrorf("AttributeError", "'_diaspore_hash._HashEngine' object has no attribute '%s'", n)
	case *jsonScanner:
		if value, ok := t.attr(n); ok {
			return value, nil
		}
		return nil, newErrorf("AttributeError", "'_json.Scanner' object has no attribute '%s'", n)
	case *jsonEncoder:
		if value, ok := t.attr(n); ok {
			return value, nil
		}
		return nil, newErrorf("AttributeError", "'_json.Encoder' object has no attribute '%s'", n)
	case *GenericAlias, *UnionType:
		return vm.genericAttr(t, n)
	case *Range:
		return vm.rangeAttr(t, n)
	case *rangeIter, *longRangeIter:
		return vm.rangeIteratorAttr(t, n)
	case *ByteArray, *MemoryView:
		return vm.bufferAttr(t, n)
	case *Instance:
		if hook, ok := t.Class.Lookup("__getattribute__"); ok {
			value, err := vm.call(vm.bindAttr(hook, t, t.Class), []Value{n}, nil)
			if _, missing := raisedAs(err, "AttributeError"); missing {
				if fallback, found := t.Class.Lookup("__getattr__"); found {
					return vm.call(vm.bindAttr(fallback, t, t.Class), []Value{n}, nil)
				}
			}
			return value, err
		}
		value, err := vm.baseGetAttr(t, n)
		if _, missing := raisedAs(err, "AttributeError"); missing {
			if fallback, found := t.Class.Lookup("__getattr__"); found {
				return vm.call(vm.bindAttr(fallback, t, t.Class), []Value{n}, nil)
			}
		}
		return value, err
	case *Class:
		if n == "__class__" {
			if t.Meta != nil {
				return t.Meta, nil
			}
			return vm.types["type"], nil
		}
		v, ok := t.Lookup(n)
		if builtin, found := v.(*Builtin); found && objectClass.NS[n] == builtin {
			if descriptor := vm.objectMetadataDescriptor(n); descriptor != nil {
				return descriptor, nil
			}
		}
		if !ok {
			if t.Meta != nil {
				if value, found := t.Meta.Lookup(n); found {
					if _, property := propertyValue(value); property {
						return vm.propertyGet(value, t, t.Meta)
					}
					if callableDescriptorKind(value) != "" {
						return vm.callableDescriptorGet(value, t, t.Meta)
					}
					if descriptor, ok := value.(*Instance); ok {
						if _, has := descriptor.Class.Lookup("__get__"); has {
							return vm.callDunder(descriptor, "__get__", []Value{t, t.Meta})
						}
					}
					return vm.bindAttr(value, t, t.Meta), nil
				}
			}
			switch n {
			case "__new__":
				if t.Prim != nil {
					return vm.primitiveNew(t.Prim), nil
				}
				return vm.objectNewBuiltin(), nil
			case "__subclasshook__":
				return bi(n, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
					if err := argCount(n, args, 1, 1); err != nil {
						return nil, err
					}
					return NotImplemented, noKw(n, kw)
				}), nil
			case "__subclasses__":
				return bi(n, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
					if err := noArgs(n, args, kw); err != nil {
						return nil, err
					}
					items := make([]Value, len(t.subclasses))
					for i, child := range t.subclasses {
						items[i] = child
					}
					return &List{Items: items}, nil
				}), nil
			case "__name__", "__qualname__":
				return t.Name, nil
			case "__mro__", "__bases__", "__base__":
				value, _, err := vm.typeMetadataGet(t, n)
				return value, err
			case "__dict__":
				return &classDict{cls: t}, nil
			}
			if t.Prim != nil {
				if value, err := vm.getAttr(t.Prim, n); err == nil {
					return value, nil
				}
			}
			if descriptor := vm.objectMetadataDescriptor(n); descriptor != nil {
				if descriptor.kind == "classmethod_descriptor" {
					return descriptor.bind(t), nil
				}
				return descriptor, nil
			}
			return nil, newErrorf("AttributeError", "type object '%s' has no attribute '%s'",
				t.Name, n)
		}
		if _, property := propertyValue(v); property {
			return vm.propertyGet(v, None, t)
		}
		if callableDescriptorKind(v) != "" {
			return vm.callableDescriptorGet(v, None, t)
		}
		switch w := v.(type) {
		case *Instance:
			if _, has := w.Class.Lookup("__get__"); has {
				return vm.callDunder(w, "__get__", []Value{None, t})
			}
		}
		return v, nil
	case *Super:
		var mro []*Class
		switch obj := t.Obj.(type) {
		case *Instance:
			mro = obj.Class.MRO
		case *Class:
			if obj.IsSub(t.Cls) {
				mro = obj.MRO
			} else if obj.Meta != nil {
				mro = obj.Meta.MRO
			}
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
				if _, property := propertyValue(v); property {
					return vm.propertyGet(v, t.Obj, c)
				}
				if callableDescriptorKind(v) != "" {
					return vm.callableDescriptorGet(v, t.Obj, c)
				}
				if descriptor, ok := v.(*metadataDescriptor); ok && descriptor.storage != slotNone {
					return descriptor.storageGet(vm, t.Obj)
				}
				if descriptor, ok := v.(*Instance); ok {
					if _, has := descriptor.Class.Lookup("__get__"); has {
						owner, err := ctorType(vm, []Value{t.Obj}, nil)
						if err != nil {
							return nil, err
						}
						return vm.callDunder(descriptor, "__get__", []Value{t.Obj, owner})
					}
				}
				return vm.bindAttr(v, t.Obj, c), nil
			}
		}
		if t.Cls.Prim != nil {
			if value, err := vm.getAttr(t.Cls.Prim, n); err == nil {
				if n == "__new__" {
					return value, nil
				}
				return &BoundMethod{Fn: value, Recv: t.Obj}, nil
			}
		}
		if n == "__new__" {
			return vm.objectNewBuiltin(), nil
		}
		if descriptor := vm.objectMetadataDescriptor(n); descriptor != nil {
			if descriptor.kind == "classmethod_descriptor" {
				if instance, ok := t.Obj.(*Instance); ok {
					return descriptor.bind(instance.Class), nil
				}
			}
			return descriptor.bind(t.Obj), nil
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
		if !strings.HasPrefix(n, ".") {
			if value, ok := t.attrs[n]; ok {
				return value, nil
			}
		}
		if !strings.HasPrefix(n, ".") {
			if m, ok := t.methods[n]; ok {
				return m, nil
			}
		}
		return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", t.Kind, n)
	case *Property:
		return vm.propertyAttr(t, n)
	case *StaticMethod, *ClassMethod:
		return vm.callableDescriptorAttr(t, n)
	case *Function:
		if v, ok := t.attrs[n]; ok {
			return v, nil
		}
		switch n {
		case "__name__":
			return funcName(t), nil
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
		if t.Name == "property" {
			if value := vm.propertyTypeAttr(n); value != nil {
				return value, nil
			}
		}
		if t.Name == "staticmethod" || t.Name == "classmethod" {
			if value := vm.callableDescriptorTypeAttr(t.Name, n); value != nil {
				return value, nil
			}
		}
		switch n {
		case "__module__":
			switch t.Name {
			case "Scanner", "Encoder":
				return "_json", nil
			case "_abc_data":
				return "_abc", nil
			case "_HashEngine":
				return "_diaspore_hash", nil
			case "GenericAlias", "UnionType":
				return "types", nil
			default:
				return "builtins", nil
			}
		case "__qualname__":
			return t.Name, nil
		case "__new__":
			return vm.primitiveNew(t), nil
		case "__mro__", "__bases__", "__base__":
			value, _, err := vm.typeMetadataGet(t, n)
			return value, err
		case "__class__":
			return vm.types["type"], nil
		case "__prepare__":
			if t.Name == "type" {
				return bi(n, func(vm *VM, a []Value, kw []KwArg) (Value, error) { return NewDict(), nil }), nil
			}
		case "__init__":
			if t.Name == "type" {
				return bi(n, func(vm *VM, a []Value, kw []KwArg) (Value, error) {
					if err := argCount("type.__init__", a, 2, 4); err != nil {
						return nil, err
					}
					return None, nil
				}), nil
			}
		}
		if t.Name == "int" && n == "from_bytes" {
			return bi("from_bytes", intFromBytes), nil
		}
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
	if (n == "__index__" || n == "__int__") && isIntKind(o) {
		return bi(n, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs(n, args, kw); err != nil {
				return nil, err
			}
			if value, ok := asInt(o); ok {
				return value, nil
			}
			return o, nil
		})
	}
	if kind := unaryMethodKind(n); kind != "" && isNumber(o) && (kind != "invert" || isIntKind(o)) {
		return bi(n, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs(n, args, kw); err != nil {
				return nil, err
			}
			return vm.unary(kind, o)
		})
	}
	if (n == "__abs__" || n == "__hash__") && isNumber(o) {
		return bi(n, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs(n, args, kw); err != nil {
				return nil, err
			}
			if n == "__hash__" {
				return hashValue(o)
			}
			return biAbs(vm, []Value{o}, nil)
		})
	}
	if n == "to_bytes" && isIntKind(o) {
		return bi(n, func(vm *VM, args []Value, kw []KwArg) (Value, error) { return intToBytes(vm, o, args, kw) })
	}
	isInt := isIntKind(o)
	fv, isFloat := o.(float64)
	if !isInt && !isFloat {
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
	case "bit_length", "bit_count":
		if isFloat {
			return nil
		}
		return bi(n, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs(n, args, kw); err != nil {
				return nil, err
			}
			if value, ok := o.(*big.Int); ok {
				if n == "bit_length" {
					return int64(value.BitLen()), nil
				}
				count := 0
				for _, word := range value.Bits() {
					count += bits.OnesCount(uint(word))
				}
				return int64(count), nil
			}
			value, _ := asInt(o)
			magnitude := uint64(value)
			if value < 0 {
				magnitude = -magnitude
			}
			if n == "bit_count" {
				return int64(bits.OnesCount64(magnitude)), nil
			}
			return int64(bits.Len64(magnitude)), nil
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
	if _, instance := o.(*Instance); !instance {
		if handled, err := vm.propertySetAttr(o, n, v, false); handled {
			return err
		}
		if handled, err := vm.callableDescriptorSetAttr(o, n, v, false); handled {
			return err
		}
	}
	if handled, err := vm.metadataSetAttr(o, n, v); handled {
		return err
	}
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
		t.Class.attributeHooks()
		if hook := t.Class.setattrHook; hook != nil {
			_, err := vm.call(vm.bindAttr(hook, t, t.Class), []Value{n, v}, nil)
			return err
		}
		return vm.baseSetAttr(t, n, v)
	case *Class:
		if t.nativeImmutable || t == objectClass || excClasses[t.Name] == t {
			return newErrorf("TypeError", "cannot set '%s' attribute of immutable type '%s'", n, t.diagnosticName())
		}
		if _, ok := t.NS[n]; !ok {
			t.dictOrder = append(t.dictOrder, n)
		}
		t.NS[n] = v
		if n == "__setattr__" || n == "__delattr__" {
			t.invalidateAttributeHooks()
		}
		return nil
	case *NativeModule:
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

func (vm *VM) baseSetAttr(t *Instance, n string, v Value) error {
	if handled, err := vm.propertySetAttr(t, n, v, false); handled {
		return err
	}
	if handled, err := vm.callableDescriptorSetAttr(t, n, v, false); handled {
		return err
	}
	if descriptor, ok := t.Class.Lookup(n); ok {
		if _, property := propertyValue(descriptor); property {
			return vm.propertySet(descriptor, t, v)
		}
		switch p := descriptor.(type) {
		case *metadataDescriptor:
			if p.storage != slotNone {
				return p.storageSet(vm, t, v, false)
			}
		case *Instance:
			if _, ok := p.Class.Lookup("__set__"); ok {
				_, err := vm.callDunder(p, "__set__", []Value{t, v})
				return err
			}
			if _, ok := p.Class.Lookup("__delete__"); ok {
				return newError("AttributeError", "__set__")
			}
		}
	}
	if n == "__dict__" && instanceHasDict(t.Class) {
		return vm.replaceInstanceDictionary(t, v)
	}
	return vm.instanceStore(t, n, v)
}

func (vm *VM) baseDelAttr(t *Instance, n string) error {
	if handled, err := vm.propertySetAttr(t, n, nil, true); handled {
		return err
	}
	if handled, err := vm.callableDescriptorSetAttr(t, n, nil, true); handled {
		return err
	}
	if descriptor, ok := t.Class.Lookup(n); ok {
		if _, property := propertyValue(descriptor); property {
			return vm.propertyDelete(descriptor, t)
		}
		switch p := descriptor.(type) {
		case *metadataDescriptor:
			if p.storage != slotNone {
				return p.storageSet(vm, t, nil, true)
			}
		case *Instance:
			if _, ok := p.Class.Lookup("__delete__"); ok {
				_, err := vm.callDunder(p, "__delete__", []Value{t})
				return err
			}
			if _, ok := p.Class.Lookup("__set__"); ok {
				return newError("AttributeError", "__delete__")
			}
		}
	}
	if n == "__dict__" && instanceHasDict(t.Class) {
		t.dictionary = nil
		return nil
	}
	return vm.instanceDelete(t, n)
}

func (vm *VM) delAttr(o Value, n string) error {
	if _, instance := o.(*Instance); !instance {
		if handled, err := vm.propertySetAttr(o, n, nil, true); handled {
			return err
		}
		if handled, err := vm.callableDescriptorSetAttr(o, n, nil, true); handled {
			return err
		}
	}
	if handled, err := vm.metadataDelAttr(o, n); handled {
		return err
	}
	switch t := o.(type) {
	case *Instance:
		t.Class.attributeHooks()
		if hook := t.Class.delattrHook; hook != nil {
			_, err := vm.call(vm.bindAttr(hook, t, t.Class), []Value{n}, nil)
			return err
		}
		return vm.baseDelAttr(t, n)
	case *Class:
		if t.nativeImmutable || t == objectClass || excClasses[t.Name] == t {
			return newErrorf("TypeError", "cannot delete '%s' attribute of immutable type '%s'", n, t.diagnosticName())
		}
		if _, ok := t.NS[n]; ok {
			delete(t.NS, n)
			for i, name := range t.dictOrder {
				if name == n {
					t.dictOrder = append(t.dictOrder[:i], t.dictOrder[i+1:]...)
					break
				}
			}
			if n == "__setattr__" || n == "__delattr__" {
				t.invalidateAttributeHooks()
			}
			return nil
		}
	case *Function:
		if _, ok := t.attrs[n]; ok {
			delete(t.attrs, n)
			return nil
		}
	case *NativeModule:
		if _, present := t.NS[n]; present {
			delete(t.NS, n)
			return nil
		}
	case *Module:
		if c, ok := t.frame.cells[n]; ok && c.v != nil {
			c.v = nil
			return nil
		}
	}
	return newErrorf("AttributeError", "'%s' object has no attribute '%s'", typeName(o), n)
}

// buildClass is CPython-shaped class creation (reference: vm.py). An
// explicit callable metaclass (not `type`, not a hook-less plain class) is
// called as metaclass(name, bases_tuple, ns_dict, **kwargs); otherwise class
// kwargs dispatch to the nearest base's __init_subclass__.
func (vm *VM) buildClass(name string, bases []*Class, ns map[string]Value,
	nsOrder []string, metaclass Value, kwargs []KwArg, primBase *BuiltinType, primIndex int, rawMeta ...*Class) (Value, error) {
	plainMeta := metaclass == nil
	if bt, ok := metaclass.(*BuiltinType); ok && bt.Name == "type" {
		plainMeta = true
	}
	if mc, ok := metaclass.(*Class); ok && mc.Prim != nil && mc.Prim.Name == "type" {
		plainMeta = false
	}
	if !plainMeta {
		baseVals := baseValues(bases, primBase, primIndex)
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
	if _, definesEq := ns["__eq__"]; definesEq {
		if _, definesHash := ns["__hash__"]; !definesHash {
			ns["__hash__"] = None
			nsOrder = append(nsOrder, "__hash__")
		}
	}
	if constructor, ok := ns["__new__"].(*Function); ok {
		wrapped, err := ctorStaticMethod(vm, []Value{constructor}, nil)
		if err != nil {
			return nil, err
		}
		ns["__new__"] = wrapped
	}
	for _, method := range []string{"__init_subclass__", "__class_getitem__"} {
		if function, ok := ns[method].(*Function); ok {
			wrapped, err := ctorClassMethod(vm, []Value{function}, nil)
			if err != nil {
				return nil, err
			}
			ns[method] = wrapped
		}
	}
	cls := &Class{Name: name, Bases: bases, NS: ns, Prim: primBase, vm: vm,
		directPrim: primBase, primIndex: primIndex}
	if len(rawMeta) != 0 {
		cls.Meta = rawMeta[0]
	}
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
	cls.dictOrder = nsOrder
	if err := vm.initializeSlots(cls); err != nil {
		return nil, err
	}
	if _, documented := ns["__doc__"]; !documented {
		ns["__doc__"] = None
		cls.dictOrder = append(cls.dictOrder, "__doc__")
	}
	for _, base := range bases {
		if cls.abcFlags&96 == 0 {
			cls.abcFlags |= base.abcFlags & 96
		}
		if base != objectClass && excClasses[base.Name] != base {
			base.subclasses = append(base.subclasses, cls)
		}
	}
	for _, v := range cls.NS {
		switch t := rawPrim(v).(type) {
		case *Function:
			if t.defclass == nil {
				t.defclass = cls
			}
		case *Property:
			for _, accessor := range []Value{t.Fget, t.Fset, t.Fdel} {
				if fn, ok := accessor.(*Function); ok && fn.defclass == nil {
					fn.defclass = cls
				}
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
	var named []KwArg
	for _, key := range nsOrder {
		value := ns[key]
		_, hasHook := propertyValue(value)
		if descriptor, ok := value.(*Instance); ok {
			_, hasHook = descriptor.Class.Lookup("__set_name__")
			if _, property := propertyValue(value); property {
				hasHook = true
			}
		}
		if hasHook {
			// Hooks may delete, replace or append class attributes. Capture
			// both names and values before invoking the first callback.
			named = make([]KwArg, len(nsOrder))
			for index, name := range nsOrder {
				named[index] = KwArg{Name: name, Val: ns[name]}
			}
			break
		}
	}
	for _, entry := range named {
		key, value := entry.Name, entry.Val
		if _, property := propertyValue(value); property {
			method, err := vm.getAttr(value, "__set_name__")
			if err != nil {
				return nil, err
			}
			if _, err := vm.call(method, []Value{cls, key}, nil); err != nil {
				return nil, err
			}
			continue
		}
		if descriptor, ok := value.(*Instance); ok {
			if _, has := descriptor.Class.Lookup("__set_name__"); has {
				if _, err := vm.callDunder(descriptor, "__set_name__", []Value{cls, key}); err != nil {
					return nil, err
				}
			}
		}
	}
	{
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
		if len(kwargs) > 0 {
			return nil, newErrorf("TypeError", "%s.__init_subclass__() takes no keyword arguments", name)
		}
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
		if tup, ok := inst.dictValue("exceptions").(*Tuple); ok {
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
		msg, _ := inst.dictValue("message").(string)
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

func (vm *VM) baseGetAttr(t *Instance, n string) (Value, error) {
	v, found := t.Class.Lookup(n)
	if !found && n == "__class__" {
		return t.Class, nil
	}
	if descriptor, ok := v.(*metadataDescriptor); ok && descriptor.storage != slotNone {
		return descriptor.storageGet(vm, t)
	}
	if _, property := propertyValue(v); property {
		return vm.propertyGet(v, t, t.Class)
	}
	if descriptor, ok := v.(*Instance); ok {
		_, sets := descriptor.Class.Lookup("__set__")
		_, deletes := descriptor.Class.Lookup("__delete__")
		if sets || deletes {
			if _, gets := descriptor.Class.Lookup("__get__"); gets {
				return vm.callDunder(descriptor, "__get__", []Value{t, t.Class})
			}
		}
	}
	if !found {
		switch n {
		case "fget", "fset", "fdel", "__isabstractmethod__":
			if _, property := propertyValue(t); property {
				return vm.propertyAttr(t, n)
			}
		}
		switch n {
		case "__func__", "__wrapped__", "__isabstractmethod__":
			if callableDescriptorKind(t) != "" {
				return vm.callableDescriptorAttr(t, n)
			}
		}
	}
	if value, found, err := vm.instanceLookup(t, n); err != nil || found {
		return value, err
	}
	if builtin, native := v.(*Builtin); native && t.Class.Prim != nil && objectClass.NS[n] == builtin {
		if value, handled, err := vm.metadataAttr(t, n); handled {
			return value, err
		}
	}
	if found {
		if callableDescriptorKind(v) != "" {
			return vm.callableDescriptorGet(v, t, t.Class)
		}
		if descriptor, ok := v.(*Instance); ok {
			if _, has := descriptor.Class.Lookup("__get__"); has {
				return vm.callDunder(descriptor, "__get__", []Value{t, t.Class})
			}
		}
		return vm.bindAttr(v, t, t.Class), nil
	}
	if n == "__dict__" {
		return vm.instanceDictionary(t)
	}
	if value, handled, err := vm.metadataAttr(t, n); handled {
		return value, err
	}
	if t.Prim != nil {
		if _, property := propertyValue(t); property {
			return vm.propertyAttr(t, n)
		}
		if callableDescriptorKind(t) != "" {
			return vm.callableDescriptorAttr(t, n)
		}
		if m, err := vm.getAttr(t.Prim, n); err == nil {
			return m, nil
		}
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'",
		t.Class.diagnosticName(), n)
}
