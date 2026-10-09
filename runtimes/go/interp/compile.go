package interp

import (
	"encoding/base64"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
)

// The compile pass turns the decoded JSON IR into closures once at load time:
// field lookups, type assertions, op dispatch, constant parsing, and name
// resolution all happen here instead of per execution step.
//
// Functions get slot-indexed frames: every name in the IR's `locals` list maps
// to a fixed slot resolved at compile time. Module and class bodies keep
// map-based frames (their namespaces are dynamic by design). Hidden
// compiler-generated names that are not in `locals` (dot-names from match
// desugaring) get "soft" slots: assigned like locals but read with the
// free-variable fallback chain, matching the reference VM's dict semantics.

type evalFn func(fr *frame) (Value, error)
type execFn func(fr *frame) error
type assignFn func(fr *frame, v Value) error

type compiledFunc struct {
	name       string
	gen        bool
	moduleName string
	nSlots     int
	layout     map[string]int
	params     *paramSpec
	defaults   []evalFn
	kwdefaults []evalFn // aligned with kwonly; nil entry = required
	body       execFn
	classHops  int // frames to ascend from the def-site frame for the closure
	poolable   bool
	freelist   []*frame // reused frames; access is gil-serialized
	fastCall   bool     // positional-only, no vararg/kwonly/kwarg, not a generator
}

// getFrame returns an initialized frame for one invocation of cf.
func (cf *compiledFunc) getFrame() *frame {
	var fr *frame
	if n := len(cf.freelist); cf.poolable && n > 0 {
		fr = cf.freelist[n-1]
		cf.freelist = cf.freelist[:n-1]
		clear(fr.slots)
		fr.gen = nil
		fr.ret = nil
		fr.self = nil
	} else {
		fr = &frame{slots: make([]Value, cf.nSlots), layout: cf.layout,
			fname: cf.name, moduleName: cf.moduleName}
	}
	fr.line = 0
	return fr
}

func (cf *compiledFunc) putFrame(fr *frame) {
	if cf.poolable && len(cf.freelist) < 64 {
		cf.freelist = append(cf.freelist, fr)
	}
}

// scope is the compile-time mirror of the runtime frame chain.
type scope struct {
	parent  *scope
	isClass bool
	strict  bool // function scope: slot-indexed
	layout  map[string]int
	nHard   int // slots 0..nHard-1 are declared locals; the rest are soft
	nSlots  int
	gdecl   map[string]bool
	ndecl   map[string]bool
}

type compiler struct {
	vm          *VM
	moduleName  string
	sc          *scope
	cells       map[string]*cell // the module's global cells
	err         error
	interactive bool
}

// cellFor resolves (or creates) the module-global cell for a name at
// compile time.
func (c *compiler) cellFor(name string) *cell {
	if cl, ok := c.cells[name]; ok {
		return cl
	}
	cl := &cell{}
	c.cells[name] = cl
	return cl
}

func (c *compiler) fail(format string, a ...any) {
	if c.err == nil {
		c.err = fmt.Errorf(format, a...)
	}
}

func execNop(fr *frame) error { return nil }

func compileModule(vm *VM, name string, body []any) (execFn, error) {
	cells := map[string]*cell{}
	vm.modCells[name] = cells
	c := &compiler{vm: vm, moduleName: name, sc: &scope{}, cells: cells}
	fn := c.compileBlock(body)
	if vm.tracer != nil {
		body := fn
		fn = func(fr *frame) error {
			vm.traceCall(fr)
			err := body(fr)
			vm.traceReturn(fr, err)
			return err
		}
	}
	return fn, c.err
}

// compileSnippet compiles exec/eval body nodes against a supplied cell table
// (the namespace), so names resolve to and mutate that namespace.
func compileSnippet(vm *VM, name string, body []any, cells map[string]*cell, interactive bool) (execFn, error) {
	vm.modCells[name] = cells
	c := &compiler{vm: vm, moduleName: name, sc: &scope{}, cells: cells, interactive: interactive}
	fn := c.compileBlock(body)
	return fn, c.err
}

// compileSnippetExpr compiles a single expression node against a cell table.
func compileSnippetExpr(vm *VM, name string, expr node, cells map[string]*cell) (evalFn, error) {
	vm.modCells[name] = cells
	c := &compiler{vm: vm, moduleName: name, sc: &scope{}, cells: cells}
	fn := c.compileExpr(expr)
	return fn, c.err
}

func (c *compiler) compileFunction(n node) *compiledFunc {
	p := nNode(n, "params")
	spec := &paramSpec{
		args:   nStrs(p, "args"),
		vararg: nStr(p, "vararg"),
		kwonly: nStrs(p, "kwonly"),
		kwarg:  nStr(p, "kwarg"),
	}
	locals := nStrs(n, "locals")
	layout := make(map[string]int, len(locals))
	for i, name := range locals {
		layout[name] = i
	}
	sc := &scope{
		parent: c.sc,
		strict: true,
		layout: layout,
		nHard:  len(locals),
		nSlots: len(locals),
		gdecl:  strSet(nStrs(n, "gdecl")),
		ndecl:  strSet(nStrs(n, "ndecl")),
	}
	collectSoftSlots(nList(n, "body"), sc)

	cf := &compiledFunc{
		name:       nStr(n, "name"),
		gen:        n["gen"] == true,
		moduleName: c.moduleName,
		params:     spec,
	}
	spec.argIdx = make([]int, len(spec.args))
	for i, a := range spec.args {
		spec.argIdx[i] = layout[a]
	}
	spec.kwonlyIdx = make([]int, len(spec.kwonly))
	for i, a := range spec.kwonly {
		spec.kwonlyIdx[i] = layout[a]
	}
	spec.varargIdx, spec.kwargIdx = -1, -1
	if spec.vararg != "" {
		spec.varargIdx = layout[spec.vararg]
	}
	if spec.kwarg != "" {
		spec.kwargIdx = layout[spec.kwarg]
	}

	// Defaults are evaluated at definition time in the defining scope.
	for _, d := range nList(p, "defaults") {
		cf.defaults = append(cf.defaults, c.compileExpr(d.(node)))
	}
	spec.ndefault = len(cf.defaults)
	kwdefs := nList(p, "kwdefaults")
	cf.kwdefaults = make([]evalFn, len(spec.kwonly))
	for i := range spec.kwonly {
		if i < len(kwdefs) && kwdefs[i] != nil {
			cf.kwdefaults[i] = c.compileExpr(kwdefs[i].(node))
		}
	}

	// The runtime closure skips any class frames enclosing the def site.
	hops := 0
	for s := c.sc; s != nil && s.isClass; s = s.parent {
		hops++
	}
	cf.classHops = hops

	inner := &compiler{vm: c.vm, moduleName: c.moduleName, sc: sc, cells: c.cells}
	cf.body = inner.compileBlock(nList(n, "body"))
	if inner.err != nil {
		c.fail("%v", inner.err)
	}
	cf.nSlots = sc.nSlots
	cf.layout = sc.layout
	cf.poolable = !cf.gen && !bodyCapturesFrame(nList(n, "body"))
	cf.fastCall = !cf.gen && spec.varargIdx < 0 && spec.kwargIdx < 0 && len(spec.kwonly) == 0
	return cf
}

// collectSoftSlots extends a function scope's layout with assignment targets
// the IR did not declare in `locals` (hidden dot-names).
func collectSoftSlots(body []any, sc *scope) {
	var walkTarget func(t node)
	var walkStmts func(stmts []any)
	var walkExpr func(e node)
	bind := func(id string) {
		if id == "" {
			return
		}
		if _, ok := sc.layout[id]; ok {
			return
		}
		if sc.gdecl[id] || sc.ndecl[id] {
			return
		}
		sc.layout[id] = sc.nSlots
		sc.nSlots++
	}
	walkTarget = func(t node) {
		switch nStr(t, "op") {
		case "name":
			bind(nStr(t, "id"))
		case "tuple", "list":
			for _, e := range nList(t, "elts") {
				walkTarget(e.(node))
			}
		case "starred":
			walkTarget(nNode(t, "v"))
		}
	}
	walkExpr = func(e node) {
		if e == nil {
			return
		}
		switch nStr(e, "op") {
		case "namedexpr":
			bind(nStr(nNode(e, "target"), "id"))
			walkExpr(nNode(e, "v"))
			return
		case "func", "funcexpr":
			// Separate scope; only defaults evaluate here.
			p := nNode(e, "params")
			for _, d := range nList(p, "defaults") {
				walkExpr(d.(node))
			}
			for _, d := range nList(p, "kwdefaults") {
				if d != nil {
					walkExpr(d.(node))
				}
			}
			return
		}
		for _, v := range e {
			switch t := v.(type) {
			case map[string]any:
				walkExpr(t)
			case []any:
				for _, x := range t {
					if sub, ok := x.(map[string]any); ok {
						walkExpr(sub)
					}
				}
			}
		}
	}
	walkStmts = func(stmts []any) {
		for _, raw := range stmts {
			s, _ := raw.(node)
			if s == nil {
				continue
			}
			switch nStr(s, "op") {
			case "assign":
				for _, t := range nList(s, "targets") {
					walkTarget(t.(node))
				}
				walkExpr(nNode(s, "v"))
			case "augassign":
				walkTarget(nNode(s, "target"))
				walkExpr(nNode(s, "v"))
			case "for":
				walkTarget(nNode(s, "target"))
				walkExpr(nNode(s, "iter"))
				walkStmts(nList(s, "body"))
				walkStmts(nList(s, "orelse"))
			case "while":
				walkExpr(nNode(s, "test"))
				walkStmts(nList(s, "body"))
				walkStmts(nList(s, "orelse"))
			case "if":
				walkExpr(nNode(s, "test"))
				walkStmts(nList(s, "then"))
				walkStmts(nList(s, "orelse"))
			case "try":
				walkStmts(nList(s, "body"))
				for _, raw := range nList(s, "handlers") {
					h := raw.(node)
					bind(nStr(h, "name"))
					if t := nNode(h, "t"); t != nil {
						walkExpr(t)
					}
					walkStmts(nList(h, "body"))
				}
				walkStmts(nList(s, "orelse"))
				walkStmts(nList(s, "finalbody"))
			case "with":
				for _, raw := range nList(s, "items") {
					item := raw.(node)
					walkExpr(nNode(item, "ctx"))
					if t := nNode(item, "name"); t != nil {
						walkTarget(t)
					}
				}
				walkStmts(nList(s, "body"))
			case "func":
				bind(nStr(s, "name"))
				p := nNode(s, "params")
				for _, d := range nList(p, "defaults") {
					walkExpr(d.(node))
				}
			case "class":
				bind(nStr(s, "name"))
				for _, b := range nList(s, "bases") {
					walkExpr(b.(node))
				}
				// Class bodies bind in their own namespace.
			case "import":
				bind(nStr(s, "alias"))
			case "importfrom":
				for _, raw := range nList(s, "names") {
					bind(nStr(raw.(node), "alias"))
				}
			case "expr":
				walkExpr(nNode(s, "v"))
			case "return":
				if v := nNode(s, "v"); v != nil {
					walkExpr(v)
				}
			case "raise":
				if v := nNode(s, "exc"); v != nil {
					walkExpr(v)
				}
				if v := nNode(s, "cause"); v != nil {
					walkExpr(v)
				}
			case "del":
				// deletion binds nothing
			}
		}
	}
	walkStmts(body)
}

// bodyCapturesFrame reports whether executing the body can make the frame
// outlive the call (nested functions and classes capture it lexically).
func bodyCapturesFrame(body []any) bool {
	found := false
	var walk func(v any)
	walk = func(v any) {
		if found {
			return
		}
		switch t := v.(type) {
		case map[string]any:
			switch nStr(t, "op") {
			case "func", "funcexpr", "class":
				found = true
				return
			}
			for _, sub := range t {
				walk(sub)
			}
		case []any:
			for _, sub := range t {
				walk(sub)
			}
		}
	}
	walk(body)
	return found
}

func (c *compiler) compileBlock(stmts []any) execFn {
	if len(stmts) == 0 {
		return execNop
	}
	vm := c.vm
	fns := make([]execFn, len(stmts))
	for i, raw := range stmts {
		s, ok := raw.(map[string]any)
		if !ok {
			c.fail("diaspore: statement is not an object")
			return execNop
		}
		fns[i] = c.compileStmt(s)
	}
	if len(fns) == 1 {
		single := fns[0]
		return func(fr *frame) error {
			if err := vm.fuel(); err != nil {
				return err
			}
			return single(fr)
		}
	}
	return func(fr *frame) error {
		for _, f := range fns {
			if err := vm.fuel(); err != nil {
				return err
			}
			if err := f(fr); err != nil {
				return err
			}
		}
		return nil
	}
}

func (c *compiler) compileStmt(s node) execFn {
	fn := c.compileStmtInner(s)
	if c.vm.tracer != nil {
		line, _ := s["ln"].(float64)
		return c.vm.traceStatement(fn, int(line))
	}
	if ln, ok := s["ln"].(float64); ok {
		line := int(ln)
		inner := fn
		return func(fr *frame) error {
			fr.line = line
			return inner(fr)
		}
	}
	return fn
}

func (c *compiler) compileStmtInner(s node) execFn {
	vm := c.vm
	switch nStr(s, "op") {
	case "expr":
		vFn := c.compileExpr(nNode(s, "v"))
		if c.interactive {
			return func(fr *frame) error {
				v, err := vFn(fr)
				if err != nil {
					return err
				}
				if _, none := v.(NoneType); !none {
					text, err := vm.repr(v)
					if err != nil {
						return err
					}
					fmt.Fprintln(vm.stdout, text)
					vm.builtins["_"] = v
				}
				return nil
			}
		}
		return func(fr *frame) error {
			_, err := vFn(fr)
			return err
		}
	case "assign":
		vFn := c.compileExpr(nNode(s, "v"))
		targets := nList(s, "targets")
		if len(targets) == 1 {
			tFn := c.compileAssign(targets[0].(node))
			return func(fr *frame) error {
				v, err := vFn(fr)
				if err != nil {
					return err
				}
				return tFn(fr, v)
			}
		}
		tFns := make([]assignFn, len(targets))
		for i, t := range targets {
			tFns[i] = c.compileAssign(t.(node))
		}
		return func(fr *frame) error {
			v, err := vFn(fr)
			if err != nil {
				return err
			}
			for _, tFn := range tFns {
				if err := tFn(fr, v); err != nil {
					return err
				}
			}
			return nil
		}
	case "augassign":
		return c.compileAugAssign(s)
	case "if":
		testFn := c.compileTest(nNode(s, "test"))
		thenFn := c.compileBlock(nList(s, "then"))
		elseFn := c.compileBlock(nList(s, "orelse"))
		return func(fr *frame) error {
			b, err := testFn(fr)
			if err != nil {
				return err
			}
			if b {
				return thenFn(fr)
			}
			return elseFn(fr)
		}
	case "while":
		testFn := c.compileTest(nNode(s, "test"))
		bodyFn := c.compileBlock(nList(s, "body"))
		elseFn := c.compileBlock(nList(s, "orelse"))
		return func(fr *frame) error {
			for {
				b, err := testFn(fr)
				if err != nil {
					return err
				}
				if !b {
					return elseFn(fr)
				}
				if err := vm.fuel(); err != nil {
					return err
				}
				switch err := bodyFn(fr); err {
				case nil, errContinue:
				case errBreak:
					return nil
				default:
					return err
				}
			}
		}
	case "for":
		return c.compileFor(s)
	case "break":
		return func(fr *frame) error { return errBreak }
	case "continue":
		return func(fr *frame) error { return errContinue }
	case "pass":
		return execNop
	case "return":
		if vNode := nNode(s, "v"); vNode != nil {
			vFn := c.compileExpr(vNode)
			return func(fr *frame) error {
				v, err := vFn(fr)
				if err != nil {
					return err
				}
				fr.ret = v
				return errReturn
			}
		}
		return func(fr *frame) error {
			fr.ret = None
			return errReturn
		}
	case "func":
		cf := c.compileFunction(s)
		store := c.compileStore(nStr(s, "name"))
		return func(fr *frame) error {
			fn, err := vm.makeFunction(cf, fr)
			if err != nil {
				return err
			}
			return store(fr, fn)
		}
	case "class":
		return c.compileClass(s)
	case "raise":
		return c.compileRaise(s)
	case "try":
		return c.compileTry(s)
	case "with":
		items := nList(s, "items")
		inner := c.compileBlock(nList(s, "body"))
		for i := len(items) - 1; i >= 0; i-- {
			inner = c.compileWithItem(items[i].(node), inner)
		}
		return inner
	case "import":
		modName := nStr(s, "module")
		bind := nStr(s, "bind")
		alias := nStr(s, "alias")
		if bind == "none" || alias == "" {
			return func(fr *frame) error {
				_, err := vm.importModule(modName)
				return err
			}
		}
		store := c.compileStore(alias)
		if bind == "root" {
			root := modName
			if dot := strings.IndexByte(modName, '.'); dot >= 0 {
				root = modName[:dot]
			}
			return func(fr *frame) error {
				if _, err := vm.importModule(modName); err != nil {
					return err
				}
				return store(fr, vm.modules[root])
			}
		}
		return func(fr *frame) error {
			mod, err := vm.importModule(modName)
			if err != nil {
				return err
			}
			return store(fr, mod)
		}
	case "importfrom":
		modName := nStr(s, "module")
		type fromName struct {
			n     string
			store assignFn
		}
		var names []fromName
		for _, raw := range nList(s, "names") {
			spec := raw.(node)
			names = append(names, fromName{nStr(spec, "n"), c.compileStore(nStr(spec, "alias"))})
		}
		return func(fr *frame) error {
			mod, err := vm.importModule(modName)
			if err != nil {
				return err
			}
			for _, spec := range names {
				v, err := vm.getAttr(mod, spec.n)
				if err != nil {
					return newErrorf("ImportError", "cannot import name '%s' from '%s'",
						spec.n, modName)
				}
				if err := spec.store(fr, v); err != nil {
					return err
				}
			}
			return nil
		}
	case "del":
		targets := nList(s, "targets")
		fns := make([]execFn, len(targets))
		for i, raw := range targets {
			fns[i] = c.compileDelete(raw.(node))
		}
		return func(fr *frame) error {
			for _, f := range fns {
				if err := f(fr); err != nil {
					return err
				}
			}
			return nil
		}
	}
	c.fail("diaspore: unknown statement op %q", nStr(s, "op"))
	return execNop
}

func (c *compiler) compileFor(s node) execFn {
	vm := c.vm
	iterFn := c.compileExpr(nNode(s, "iter"))
	target := c.compileAssign(nNode(s, "target"))
	bodyFn := c.compileBlock(nList(s, "body"))
	elseFn := c.compileBlock(nList(s, "orelse"))
	runBody := func(fr *frame, x Value) (done bool, err error) {
		if err := target(fr, x); err != nil {
			return true, err
		}
		switch err := bodyFn(fr); err {
		case nil, errContinue:
			return false, nil
		case errBreak:
			return true, nil
		default:
			return true, err
		}
	}
	return func(fr *frame) error {
		iterable, err := iterFn(fr)
		if err != nil {
			return err
		}
		switch t := iterable.(type) {
		case *Range:
			cur, left := t.Start, t.Len()
			for ; left > 0; left-- {
				if err := vm.fuel(); err != nil {
					return err
				}
				done, err := runBody(fr, mkInt(cur))
				if err != nil || done {
					return err
				}
				cur += t.Step
			}
			return elseFn(fr)
		case *List:
			for i := 0; i < len(t.Items); i++ {
				if err := vm.fuel(); err != nil {
					return err
				}
				done, err := runBody(fr, t.Items[i])
				if err != nil || done {
					return err
				}
			}
			return elseFn(fr)
		}
		it, err := vm.getIter(iterable)
		if err != nil {
			return err
		}
		for {
			if err := vm.fuel(); err != nil {
				return err
			}
			x, ok, err := it.Next()
			if err != nil {
				return err
			}
			if !ok {
				return elseFn(fr)
			}
			done, err := runBody(fr, x)
			if err != nil || done {
				return err
			}
		}
	}
}

func (c *compiler) compileAugAssign(s node) execFn {
	vm := c.vm
	kind := nStr(s, "kind")
	rhsFn := c.compileExpr(nNode(s, "v"))
	fastII := intFastOp(vm, kind)
	combine := func(cur, rhs Value) (Value, error) {
		if a, ok := cur.(int64); ok {
			if b, ok := rhs.(int64); ok {
				return fastII(a, b)
			}
		}
		return vm.binopWith(kind, cur, rhs, true)
	}
	// The reference VM evaluates the rhs first, then reads the target; the
	// target object/index of an attr/index target is evaluated once.
	t := nNode(s, "target")
	switch nStr(t, "op") {
	case "name":
		load := c.compileLoad(nStr(t, "id"))
		store := c.compileStore(nStr(t, "id"))
		return func(fr *frame) error {
			rhs, err := rhsFn(fr)
			if err != nil {
				return err
			}
			cur, err := load(fr)
			if err != nil {
				return err
			}
			nv, err := combine(cur, rhs)
			if err != nil {
				return err
			}
			return store(fr, nv)
		}
	case "attr":
		oFn := c.compileExpr(nNode(t, "o"))
		name := nStr(t, "n")
		return func(fr *frame) error {
			rhs, err := rhsFn(fr)
			if err != nil {
				return err
			}
			o, err := oFn(fr)
			if err != nil {
				return err
			}
			cur, err := vm.getAttr(o, name)
			if err != nil {
				return err
			}
			nv, err := combine(cur, rhs)
			if err != nil {
				return err
			}
			return vm.setAttr(o, name, nv)
		}
	case "index":
		oFn := c.compileExpr(nNode(t, "o"))
		kFn := c.compileKey(nNode(t, "k"))
		return func(fr *frame) error {
			rhs, err := rhsFn(fr)
			if err != nil {
				return err
			}
			o, err := oFn(fr)
			if err != nil {
				return err
			}
			k, err := kFn(fr)
			if err != nil {
				return err
			}
			cur, err := vm.getIndex(o, k)
			if err != nil {
				return err
			}
			nv, err := combine(cur, rhs)
			if err != nil {
				return err
			}
			return vm.setIndex(o, k, nv)
		}
	}
	c.fail("diaspore: illegal augmented assignment target")
	return execNop
}

func (c *compiler) compileClass(s node) execFn {
	vm := c.vm
	name := nStr(s, "name")
	type baseSpec struct {
		fn      evalFn
		starred bool
	}
	var baseFns []baseSpec
	for _, raw := range nList(s, "bases") {
		b := raw.(node)
		if nStr(b, "op") == "starred" {
			baseFns = append(baseFns, baseSpec{c.compileExpr(nNode(b, "v")), true})
		} else {
			baseFns = append(baseFns, baseSpec{c.compileExpr(b), false})
		}
	}
	type kwSpec struct {
		name string // "" = ** expansion
		fn   evalFn
	}
	var kwFns []kwSpec
	for _, raw := range nList(s, "kwargs") {
		kw := raw.(node)
		nm, _ := kw["n"].(string)
		kwFns = append(kwFns, kwSpec{nm, c.compileExpr(nNode(kw, "v"))})
	}
	classScope := &scope{parent: c.sc, isClass: true}
	inner := &compiler{vm: vm, moduleName: c.moduleName, sc: classScope, cells: c.cells}
	bodyFn := inner.compileBlock(nList(s, "body"))
	if inner.err != nil {
		c.fail("%v", inner.err)
	}
	store := c.compileStore(name)
	return func(fr *frame) error {
		var bases []*Class
		var primBase *BuiltinType
		primIndex := 0
		checkBase := func(bv Value) error {
			switch cls := bv.(type) {
			case *Class:
				bases = append(bases, cls)
				return nil
			case *BuiltinType:
				if subclassablePrim(cls.Name) {
					primBase = cls
					primIndex = len(bases)
					return nil
				}
			}
			r, rerr := vm.repr(bv)
			if rerr != nil {
				r = typeName(bv)
			}
			return newErrorf("TypeError", "cannot subclass %s in diaspore", r)
		}
		for _, bs := range baseFns {
			bv, err := bs.fn(fr)
			if err != nil {
				return err
			}
			if bs.starred {
				items, err := vm.iterToSlice(bv)
				if err != nil {
					return err
				}
				for _, x := range items {
					if err := checkBase(x); err != nil {
						return err
					}
				}
			} else if err := checkBase(bv); err != nil {
				return err
			}
		}
		var metaclass Value
		var kwargs []KwArg
		for _, spec := range kwFns {
			v, err := spec.fn(fr)
			if err != nil {
				return err
			}
			if spec.name == "" {
				d, ok := v.(*Dict)
				if !ok {
					return newError("TypeError", "argument after ** must be a mapping")
				}
				for _, e := range d.entries {
					k, ok := e.key.(string)
					if !ok {
						return newError("TypeError", "keywords must be strings")
					}
					if k == "metaclass" {
						metaclass = e.val
					} else {
						kwargs = append(kwargs, KwArg{k, e.val})
					}
				}
			} else if spec.name == "metaclass" {
				metaclass = v
			} else {
				kwargs = append(kwargs, KwArg{spec.name, v})
			}
		}
		cf := &frame{
			vars:       map[string]Value{},
			parent:     fr,
			globals:    fr.globals,
			fname:      name,
			moduleName: fr.moduleName,
		}
		if err := bodyFn(cf); err != nil {
			return err
		}
		cls, err := vm.buildClass(name, bases, cf.vars, cf.varOrder, metaclass, kwargs, primBase, primIndex)
		if err != nil {
			return err
		}
		return store(fr, cls)
	}
}

func c3MRO(cls *Class) ([]*Class, error) {
	seqs := [][]*Class{{cls}}
	for _, b := range cls.Bases {
		seqs = append(seqs, append([]*Class{}, b.MRO...))
	}
	if len(cls.Bases) > 0 {
		seqs = append(seqs, append([]*Class{}, cls.Bases...))
	}
	var out []*Class
	for {
		nonEmpty := seqs[:0]
		for _, s := range seqs {
			if len(s) > 0 {
				nonEmpty = append(nonEmpty, s)
			}
		}
		seqs = nonEmpty
		if len(seqs) == 0 {
			return out, nil
		}
		var head *Class
		for _, s := range seqs {
			cand := s[0]
			inTail := false
			for _, s2 := range seqs {
				for _, x := range s2[1:] {
					if x == cand {
						inTail = true
						break
					}
				}
				if inTail {
					break
				}
			}
			if !inTail {
				head = cand
				break
			}
		}
		if head == nil {
			return nil, newError("TypeError", "cannot create a consistent MRO")
		}
		out = append(out, head)
		for i, s := range seqs {
			if len(s) > 0 && s[0] == head {
				seqs[i] = s[1:]
			}
		}
	}
}

func (c *compiler) compileRaise(s node) execFn {
	vm := c.vm
	excNode := nNode(s, "exc")
	if excNode == nil {
		return func(fr *frame) error {
			if len(vm.excStack) == 0 {
				return newError("RuntimeError", "No active exception to re-raise")
			}
			return &Raised{Exc: vm.excStack[len(vm.excStack)-1]}
		}
	}
	excFn := c.compileExpr(excNode)
	var causeFn evalFn
	if cause := nNode(s, "cause"); cause != nil {
		causeFn = c.compileExpr(cause)
	}
	return func(fr *frame) error {
		v, err := excFn(fr)
		if err != nil {
			return err
		}
		if causeFn != nil {
			if _, err := causeFn(fr); err != nil {
				return err
			}
		}
		if cls, ok := v.(*Class); ok {
			if !cls.IsSub(baseExc) {
				return newError("TypeError", "exceptions must derive from BaseException")
			}
			inst, err := vm.instantiate(cls, nil, nil)
			if err != nil {
				return err
			}
			v = inst
		}
		inst, ok := v.(*Instance)
		if !ok || !inst.Class.IsSub(baseExc) {
			return newError("TypeError", "exceptions must derive from BaseException")
		}
		return &Raised{Exc: inst}
	}
}

type compiledHandler struct {
	matchFn evalFn // nil = bare except
	bind    assignFn
	unbind  execFn
	body    execFn
}

func (c *compiler) compileTry(s node) execFn {
	vm := c.vm
	bodyFn := c.compileBlock(nList(s, "body"))
	elseFn := c.compileBlock(nList(s, "orelse"))
	finalFn := c.compileBlock(nList(s, "finalbody"))
	var handlers []compiledHandler
	for _, raw := range nList(s, "handlers") {
		h := raw.(node)
		var ch compiledHandler
		if t := nNode(h, "t"); t != nil {
			ch.matchFn = c.compileExpr(t)
		}
		if name := nStr(h, "name"); name != "" {
			ch.bind = c.compileStore(name)
			ch.unbind = c.compileUnbind(name)
		}
		ch.body = c.compileBlock(nList(h, "body"))
		handlers = append(handlers, ch)
	}
	star := s["star"] == true
	tryBody := func(fr *frame) error {
		err := bodyFn(fr)
		if err == nil {
			return elseFn(fr)
		}
		r, ok := err.(*Raised)
		if !ok {
			return err
		}
		if star {
			return vm.runStarHandlers(fr, r, handlers, elseFn)
		}
		inst := r.Exc.(*Instance)
		for i := range handlers {
			h := &handlers[i]
			if h.matchFn != nil {
				t, merr := h.matchFn(fr)
				if merr != nil {
					return merr
				}
				match, merr := handlerClassesMatch(t, inst)
				if merr != nil {
					return merr
				}
				if !match {
					continue
				}
			}
			if h.bind != nil {
				if berr := h.bind(fr, r.Exc); berr != nil {
					return berr
				}
			}
			vm.excStack = append(vm.excStack, r.Exc)
			herr := h.body(fr)
			vm.excStack = vm.excStack[:len(vm.excStack)-1]
			if h.unbind != nil {
				if uerr := h.unbind(fr); uerr != nil {
					return uerr
				}
			}
			return herr
		}
		return err
	}
	if len(nList(s, "finalbody")) == 0 {
		return tryBody
	}
	return func(fr *frame) error {
		err := tryBody(fr)
		if isTrapOrSuspend(err) {
			return err // traps and suspends abandon execution: no finally
		}
		if ferr := finalFn(fr); ferr != nil {
			return ferr
		}
		return err
	}
}

// isTrapOrSuspend reports errors that abandon the run outright - fuel traps
// and flow suspends skip finally blocks and __exit__ (spec 13.2, 14).
func isTrapOrSuspend(err error) bool {
	switch err.(type) {
	case *Trap, *Suspend:
		return true
	}
	return false
}

func handlerClassesMatch(t Value, inst *Instance) (bool, error) {
	var classes []Value
	if tup, ok := t.(*Tuple); ok {
		classes = tup.Items
	} else {
		classes = []Value{t}
	}
	for _, cv := range classes {
		cls, ok := cv.(*Class)
		if !ok || !cls.IsSub(baseExc) {
			return false, newError("TypeError",
				"catching classes that do not inherit from BaseException is not allowed")
		}
		if inst.Class.IsSub(cls) {
			return true, nil
		}
	}
	return false, nil
}

func (c *compiler) compileWithItem(item node, inner execFn) execFn {
	vm := c.vm
	ctxFn := c.compileExpr(nNode(item, "ctx"))
	var target assignFn
	if t := nNode(item, "name"); t != nil {
		target = c.compileAssign(t)
	}
	return func(fr *frame) error {
		ctx, err := ctxFn(fr)
		if err != nil {
			return err
		}
		var enter func() (Value, error)
		var exit func(a, b, c Value) (Value, error)
		switch t := ctx.(type) {
		case *File:
			enter = func() (Value, error) { return t, nil }
			exit = func(a, b, c Value) (Value, error) { t.close(); return false, nil }
		case *NativeObject:
			enterB, hasEnter := t.methods["__enter__"]
			exitB, hasExit := t.methods["__exit__"]
			if !hasEnter || !hasExit {
				return newErrorf("TypeError",
					"'%s' object does not support the context manager protocol", t.Kind)
			}
			enter = func() (Value, error) { return vm.call(enterB, nil, nil) }
			exit = func(a, b, c Value) (Value, error) {
				return vm.call(exitB, []Value{a, b, c}, nil)
			}
		case *Instance:
			_, hasEnter := t.Class.Lookup("__enter__")
			_, hasExit := t.Class.Lookup("__exit__")
			if !hasEnter || !hasExit {
				return newErrorf("TypeError",
					"'%s' object does not support the context manager protocol", t.Class.Name)
			}
			enter = func() (Value, error) { return vm.callDunder(t, "__enter__", nil) }
			exit = func(a, b, c Value) (Value, error) {
				return vm.callDunder(t, "__exit__", []Value{a, b, c})
			}
		default:
			return newErrorf("TypeError",
				"'%s' object does not support the context manager protocol", typeName(ctx))
		}
		entered, err := enter()
		if err != nil {
			return err
		}
		if target != nil {
			if err := target(fr, entered); err != nil {
				return err
			}
		}
		err = inner(fr)
		if r, ok := err.(*Raised); ok {
			inst := r.Exc.(*Instance)
			suppress, xerr := exit(inst.Class, inst, None)
			if xerr != nil {
				return xerr
			}
			b, terr := vm.truth(suppress)
			if terr != nil {
				return terr
			}
			if b {
				return nil
			}
			return err
		}
		if isTrapOrSuspend(err) {
			return err // abandoned execution: __exit__ does not run
		}
		// break/continue/return: __exit__(None, None, None) still runs.
		if _, xerr := exit(None, None, None); xerr != nil {
			return xerr
		}
		return err
	}
}

func (c *compiler) compileAssign(t node) assignFn {
	vm := c.vm
	switch nStr(t, "op") {
	case "name":
		return c.compileStore(nStr(t, "id"))
	case "attr":
		oFn := c.compileExpr(nNode(t, "o"))
		name := nStr(t, "n")
		return func(fr *frame, v Value) error {
			o, err := oFn(fr)
			if err != nil {
				return err
			}
			return vm.setAttr(o, name, v)
		}
	case "index":
		oFn := c.compileExpr(nNode(t, "o"))
		kFn := c.compileKey(nNode(t, "k"))
		return func(fr *frame, v Value) error {
			o, err := oFn(fr)
			if err != nil {
				return err
			}
			k, err := kFn(fr)
			if err != nil {
				return err
			}
			return vm.setIndex(o, k, v)
		}
	case "tuple", "list":
		return c.compileUnpack(nList(t, "elts"))
	}
	c.fail("diaspore: cannot assign to %q target", nStr(t, "op"))
	return func(fr *frame, v Value) error { return nil }
}

func (c *compiler) compileUnpack(elts []any) assignFn {
	vm := c.vm
	star := -1
	fns := make([]assignFn, len(elts))
	for i, raw := range elts {
		e := raw.(node)
		if nStr(e, "op") == "starred" {
			star = i
			fns[i] = c.compileAssign(nNode(e, "v"))
		} else {
			fns[i] = c.compileAssign(e)
		}
	}
	n := len(elts)
	return func(fr *frame, v Value) error {
		items, err := vm.iterToSlice(v)
		if err != nil {
			if _, ok := raisedAs(err, "TypeError"); ok {
				return newErrorf("TypeError", "cannot unpack non-iterable %s object", typeName(v))
			}
			return err
		}
		if star < 0 {
			if len(items) < n {
				return newErrorf("ValueError", "not enough values to unpack (expected %d, got %d)",
					n, len(items))
			}
			if len(items) > n {
				return newErrorf("ValueError", "too many values to unpack (expected %d)", n)
			}
			for i, fn := range fns {
				if err := fn(fr, items[i]); err != nil {
					return err
				}
			}
			return nil
		}
		after := n - star - 1
		if len(items) < n-1 {
			return newErrorf("ValueError",
				"not enough values to unpack (expected at least %d, got %d)", n-1, len(items))
		}
		for i := 0; i < star; i++ {
			if err := fns[i](fr, items[i]); err != nil {
				return err
			}
		}
		mid := &List{Items: append([]Value{}, items[star:len(items)-after]...)}
		if err := fns[star](fr, mid); err != nil {
			return err
		}
		for i := 0; i < after; i++ {
			if err := fns[star+1+i](fr, items[len(items)-after+i]); err != nil {
				return err
			}
		}
		return nil
	}
}

// freeRef is one enclosing-function-scope candidate for a free variable:
// ascend `hops` frames, read slot `idx`.
type freeRef struct {
	hops int
	idx  int
}

// freeRefs computes the enclosing candidates for id, skipping class scopes
// (they never appear in a function's runtime parent chain).
func (c *compiler) freeRefs(id string) []freeRef {
	var refs []freeRef
	hops := 0
	for s := c.sc.parent; s != nil; s = s.parent {
		if s.isClass {
			continue
		}
		if !s.strict {
			break // module scope: handled via fr.globals
		}
		hops++
		if i, ok := s.layout[id]; ok {
			refs = append(refs, freeRef{hops, i})
		}
	}
	return refs
}

func (c *compiler) compileLoad(id string) evalFn {
	vm := c.vm
	sc := c.sc
	if sc.strict {
		if sc.gdecl[id] {
			gc := c.cellFor(id)
			return func(fr *frame) (Value, error) {
				if v := gc.v; v != nil {
					return v, nil
				}
				if v, ok := vm.builtins[id]; ok {
					return v, nil
				}
				return nil, newErrorf("NameError", "name '%s' is not defined", id)
			}
		}
		if idx, ok := sc.layout[id]; ok {
			if idx < sc.nHard {
				return func(fr *frame) (Value, error) {
					if v := fr.slots[idx]; v != nil {
						return v, nil
					}
					return nil, newErrorf("UnboundLocalError",
						"cannot access local variable '%s' where it is not associated with a value", id)
				}
			}
			// Soft slot: read like a dict member - unset falls through to the
			// free-variable chain.
			refs := c.freeRefs(id)
			gc := c.cellFor(id)
			return func(fr *frame) (Value, error) {
				if v := fr.slots[idx]; v != nil {
					return v, nil
				}
				return loadFree(vm, fr, id, refs, gc)
			}
		}
		refs := c.freeRefs(id)
		gc := c.cellFor(id)
		if len(refs) == 0 {
			// Only globals/builtins can supply it.
			bv := vm.builtins[id]
			return func(fr *frame) (Value, error) {
				if v := gc.v; v != nil {
					return v, nil
				}
				if bv != nil {
					return bv, nil
				}
				return nil, newErrorf("NameError", "name '%s' is not defined", id)
			}
		}
		return func(fr *frame) (Value, error) {
			return loadFree(vm, fr, id, refs, gc)
		}
	}
	if c.sc.parent == nil && !c.sc.isClass {
		// Module scope: the binding is a compile-time cell.
		gc := c.cellFor(id)
		return func(fr *frame) (Value, error) {
			if v := gc.v; v != nil {
				return v, nil
			}
			if v, ok := vm.builtins[id]; ok {
				return v, nil
			}
			return nil, newErrorf("NameError", "name '%s' is not defined", id)
		}
	}
	// Class scope: fully dynamic walk.
	return func(fr *frame) (Value, error) {
		return vm.loadDynamic(fr, id)
	}
}

func loadFree(vm *VM, fr *frame, id string, refs []freeRef, gc *cell) (Value, error) {
	f := fr
	at := 0
	for _, r := range refs {
		for ; at < r.hops; at++ {
			f = f.parent
		}
		if v := f.slots[r.idx]; v != nil {
			return v, nil
		}
	}
	if v := gc.v; v != nil {
		return v, nil
	}
	if v, ok := vm.builtins[id]; ok {
		return v, nil
	}
	return nil, newErrorf("NameError", "name '%s' is not defined", id)
}

// loadDynamic mirrors the reference VM's frame-dict walk for non-strict
// scopes (module and class bodies).
func (vm *VM) loadDynamic(fr *frame, id string) (Value, error) {
	for f := fr; f != nil; f = f.parent {
		if f.vars != nil {
			if v, ok := f.vars[id]; ok {
				return v, nil
			}
		} else if f.cells != nil {
			if c, ok := f.cells[id]; ok && c.v != nil {
				return c.v, nil
			}
		} else if i, ok := f.layout[id]; ok {
			if v := f.slots[i]; v != nil {
				return v, nil
			}
		}
	}
	if fr.globals != nil {
		if c, ok := fr.globals[id]; ok && c.v != nil {
			return c.v, nil
		}
	}
	if v, ok := vm.builtins[id]; ok {
		return v, nil
	}
	return nil, newErrorf("NameError", "name '%s' is not defined", id)
}

func (c *compiler) compileStore(id string) assignFn {
	sc := c.sc
	if sc.strict {
		if sc.gdecl[id] {
			gc := c.cellFor(id)
			return func(fr *frame, v Value) error {
				gc.v = v
				return nil
			}
		}
		if sc.ndecl[id] {
			hops := 0
			for s := sc.parent; s != nil; s = s.parent {
				if s.isClass {
					continue
				}
				if !s.strict {
					break
				}
				hops++
				if idx, ok := s.layout[id]; ok {
					h := hops
					return func(fr *frame, v Value) error {
						f := fr
						for i := 0; i < h; i++ {
							f = f.parent
						}
						f.slots[idx] = v
						return nil
					}
				}
			}
			return func(fr *frame, v Value) error {
				return newErrorf("RuntimeError", "nonlocal binding for '%s' not found", id)
			}
		}
		idx, ok := sc.layout[id]
		if !ok {
			// collectSoftSlots pre-binds assignment targets.
			idx = sc.nSlots
			sc.layout[id] = idx
			sc.nSlots++
		}
		return func(fr *frame, v Value) error {
			fr.slots[idx] = v
			return nil
		}
	}
	if sc.parent == nil && !sc.isClass {
		gc := c.cellFor(id)
		return func(fr *frame, v Value) error {
			gc.v = v
			return nil
		}
	}
	// Class scope: record first-assignment order (metaclasses receive the
	// namespace as an insertion-ordered dict).
	return func(fr *frame, v Value) error {
		if _, seen := fr.vars[id]; !seen {
			fr.varOrder = append(fr.varOrder, id)
		}
		fr.vars[id] = v
		return nil
	}
}

// compileUnbind clears a handler variable after its except block (Python
// deletes it unconditionally).
func (c *compiler) compileUnbind(id string) execFn {
	sc := c.sc
	if sc.strict {
		if idx, ok := sc.layout[id]; ok {
			return func(fr *frame) error {
				fr.slots[idx] = nil
				return nil
			}
		}
		return execNop
	}
	if sc.parent == nil && !sc.isClass {
		gc := c.cellFor(id)
		return func(fr *frame) error {
			gc.v = nil
			return nil
		}
	}
	return func(fr *frame) error {
		delete(fr.vars, id)
		return nil
	}
}

func (c *compiler) compileDelete(t node) execFn {
	vm := c.vm
	switch nStr(t, "op") {
	case "name":
		id := nStr(t, "id")
		sc := c.sc
		if sc.strict && !sc.gdecl[id] {
			if idx, ok := sc.layout[id]; ok {
				hard := idx < sc.nHard
				gc := c.cellFor(id)
				return func(fr *frame) error {
					if fr.slots[idx] != nil {
						fr.slots[idx] = nil
						return nil
					}
					if hard {
						return newErrorf("UnboundLocalError",
							"cannot access local variable '%s' where it is not associated with a value", id)
					}
					if gc.v != nil {
						gc.v = nil
						return nil
					}
					return newErrorf("NameError", "name '%s' is not defined", id)
				}
			}
		}
		// Module/class scopes and global-declared names.
		gc := c.cellFor(id)
		return func(fr *frame) error {
			if fr.vars != nil {
				if _, ok := fr.vars[id]; ok {
					delete(fr.vars, id)
					return nil
				}
			}
			if gc.v != nil {
				gc.v = nil
				return nil
			}
			return newErrorf("NameError", "name '%s' is not defined", id)
		}
	case "index":
		oFn := c.compileExpr(nNode(t, "o"))
		kFn := c.compileKey(nNode(t, "k"))
		return func(fr *frame) error {
			o, err := oFn(fr)
			if err != nil {
				return err
			}
			k, err := kFn(fr)
			if err != nil {
				return err
			}
			return vm.delIndex(o, k)
		}
	case "attr":
		oFn := c.compileExpr(nNode(t, "o"))
		name := nStr(t, "n")
		return func(fr *frame) error {
			o, err := oFn(fr)
			if err != nil {
				return err
			}
			return vm.delAttr(o, name)
		}
	}
	c.fail("diaspore: cannot delete %q target", nStr(t, "op"))
	return execNop
}

// compileTest compiles an expression in boolean position (if/while/cond
// tests, and/or/not operands) to a bool-returning closure: int and float
// comparisons skip Value boxing entirely.
func (c *compiler) compileTest(e node) func(fr *frame) (bool, error) {
	vm := c.vm
	switch nStr(e, "op") {
	case "compare":
		ops := nStrs(e, "ops")
		if len(ops) == 1 {
			kind := ops[0]
			switch kind {
			case "lt", "le", "gt", "ge", "eq", "ne":
				lFn := c.compileExpr(nNode(e, "left"))
				rFn := c.compileExpr(nList(e, "comps")[0].(node))
				return func(fr *frame) (bool, error) {
					l, err := lFn(fr)
					if err != nil {
						return false, err
					}
					r, err := rFn(fr)
					if err != nil {
						return false, err
					}
					if a, ok := l.(int64); ok {
						if b, ok := r.(int64); ok {
							switch kind {
							case "lt":
								return a < b, nil
							case "le":
								return a <= b, nil
							case "gt":
								return a > b, nil
							case "ge":
								return a >= b, nil
							case "eq":
								return a == b, nil
							default:
								return a != b, nil
							}
						}
					} else if a, ok := l.(float64); ok {
						if b, ok := r.(float64); ok {
							switch kind {
							case "lt":
								return a < b, nil
							case "le":
								return a <= b, nil
							case "gt":
								return a > b, nil
							case "ge":
								return a >= b, nil
							case "eq":
								return a == b, nil
							default:
								return a != b, nil
							}
						}
					}
					return vm.compare(kind, l, r)
				}
			}
		}
	case "boolop":
		vals := nList(e, "values")
		isAnd := nStr(e, "kind") == "and"
		tests := make([]func(fr *frame) (bool, error), len(vals))
		for i, sub := range vals {
			tests[i] = c.compileTest(sub.(node))
		}
		return func(fr *frame) (bool, error) {
			for i, t := range tests {
				b, err := t(fr)
				if err != nil {
					return false, err
				}
				if i == len(tests)-1 || isAnd != b {
					return b, nil
				}
			}
			return isAnd, nil
		}
	case "unary":
		if nStr(e, "kind") == "not" {
			inner := c.compileTest(nNode(e, "v"))
			return func(fr *frame) (bool, error) {
				b, err := inner(fr)
				return !b, err
			}
		}
	case "const":
		if v, err := parseConst(e); err == nil {
			if b, ok := v.(bool); ok {
				return func(fr *frame) (bool, error) { return b, nil }
			}
		}
	}
	vFn := c.compileExpr(e)
	return func(fr *frame) (bool, error) {
		v, err := vFn(fr)
		if err != nil {
			return false, err
		}
		if b, ok := v.(bool); ok {
			return b, nil
		}
		return vm.truth(v)
	}
}

func (c *compiler) compileExpr(e node) evalFn {
	if e == nil {
		c.fail("diaspore: missing expression node")
		return func(fr *frame) (Value, error) { return None, nil }
	}
	vm := c.vm
	switch nStr(e, "op") {
	case "const":
		v, err := parseConst(e)
		if err != nil {
			c.fail("%v", err)
		}
		return func(fr *frame) (Value, error) { return v, nil }
	case "name":
		return c.compileLoad(nStr(e, "id"))
	case "attr":
		oFn := c.compileExpr(nNode(e, "o"))
		name := nStr(e, "n")
		// Monomorphic inline cache for builtin-container receivers: their
		// method table is immutable, and the bound closure captures the
		// receiver, so identity implies the same result.
		var cachedRecv, cachedMethod Value
		return func(fr *frame) (Value, error) {
			o, err := oFn(fr)
			if err != nil {
				return nil, err
			}
			switch o.(type) {
			case *List, *Dict, *Set, *Tuple:
				if cachedRecv == o {
					return cachedMethod, nil
				}
				m, err := vm.getAttr(o, name)
				if err != nil {
					return nil, err
				}
				cachedRecv, cachedMethod = o, m
				return m, nil
			}
			return vm.getAttr(o, name)
		}
	case "index":
		oFn := c.compileExpr(nNode(e, "o"))
		kFn := c.compileKey(nNode(e, "k"))
		return func(fr *frame) (Value, error) {
			o, err := oFn(fr)
			if err != nil {
				return nil, err
			}
			k, err := kFn(fr)
			if err != nil {
				return nil, err
			}
			return vm.getIndex(o, k)
		}
	case "binop":
		return c.compileBinop(e)
	case "unary":
		return c.compileUnary(e)
	case "boolop":
		return c.compileBoolOp(e)
	case "compare":
		return c.compileCompare(e)
	case "cond":
		testFn := c.compileTest(nNode(e, "test"))
		thenFn := c.compileExpr(nNode(e, "then"))
		elseFn := c.compileExpr(nNode(e, "else"))
		return func(fr *frame) (Value, error) {
			b, err := testFn(fr)
			if err != nil {
				return nil, err
			}
			if b {
				return thenFn(fr)
			}
			return elseFn(fr)
		}
	case "call":
		return c.compileCall(e)
	case "list":
		eltsFn := c.compileElts(nList(e, "elts"))
		return func(fr *frame) (Value, error) {
			items, err := eltsFn(fr)
			if err != nil {
				return nil, err
			}
			return &List{Items: items}, nil
		}
	case "tuple":
		eltsFn := c.compileElts(nList(e, "elts"))
		return func(fr *frame) (Value, error) {
			items, err := eltsFn(fr)
			if err != nil {
				return nil, err
			}
			return &Tuple{Items: items}, nil
		}
	case "set":
		eltsFn := c.compileElts(nList(e, "elts"))
		return func(fr *frame) (Value, error) {
			items, err := eltsFn(fr)
			if err != nil {
				return nil, err
			}
			s := NewSet()
			for _, x := range items {
				if err := s.Add(x); err != nil {
					return nil, err
				}
			}
			return s, nil
		}
	case "dict":
		return c.compileDict(e)
	case "fstring":
		return c.compileFString(e)
	case "funcexpr", "func":
		cf := c.compileFunction(e)
		return func(fr *frame) (Value, error) {
			return vm.makeFunction(cf, fr)
		}
	case "yield":
		var vFn evalFn
		if v := nNode(e, "v"); v != nil {
			vFn = c.compileExpr(v)
		}
		return func(fr *frame) (Value, error) {
			if fr.gen == nil {
				return nil, &Trap{Msg: "diaspore: yield outside generator frame", Code: 4}
			}
			var v Value = None
			if vFn != nil {
				var err error
				v, err = vFn(fr)
				if err != nil {
					return nil, err
				}
			}
			return fr.gen.yield(vm, v)
		}
	case "yieldfrom":
		vFn := c.compileExpr(nNode(e, "v"))
		return func(fr *frame) (Value, error) {
			if fr.gen == nil {
				return nil, &Trap{Msg: "diaspore: yield outside generator frame", Code: 4}
			}
			sub, err := vFn(fr)
			if err != nil {
				return nil, err
			}
			it, err := vm.getIter(sub)
			if err != nil {
				return nil, err
			}
			return vm.delegateYieldFrom(fr, it)
		}
	case "namedexpr":
		vFn := c.compileExpr(nNode(e, "v"))
		store := c.compileStore(nStr(nNode(e, "target"), "id"))
		return func(fr *frame) (Value, error) {
			v, err := vFn(fr)
			if err != nil {
				return nil, err
			}
			if err := store(fr, v); err != nil {
				return nil, err
			}
			return v, nil
		}
	}
	c.fail("diaspore: unknown expression op %q", nStr(e, "op"))
	return func(fr *frame) (Value, error) { return None, nil }
}

func parseConst(e node) (Value, error) {
	switch nStr(e, "t") {
	case "none":
		return None, nil
	case "ellipsis":
		return Ellipsis, nil
	case "complex":
		re, err := strconv.ParseFloat(nStr(e, "re"), 64)
		if err != nil {
			return nil, fmt.Errorf("diaspore: bad complex re %q", nStr(e, "re"))
		}
		im, err := strconv.ParseFloat(nStr(e, "im"), 64)
		if err != nil {
			return nil, fmt.Errorf("diaspore: bad complex im %q", nStr(e, "im"))
		}
		return complex(re, im), nil
	case "int":
		n, err := strconv.ParseInt(nStr(e, "v"), 10, 64)
		if err == nil {
			return n, nil
		}
		if z, ok := new(big.Int).SetString(nStr(e, "v"), 10); ok {
			return normBig(z), nil
		}
		return nil, fmt.Errorf("diaspore: bad int constant %q", nStr(e, "v"))
	case "float":
		f, err := strconv.ParseFloat(nStr(e, "v"), 64)
		if err != nil {
			return nil, fmt.Errorf("diaspore: bad float constant %q", nStr(e, "v"))
		}
		return f, nil
	case "str":
		return nStr(e, "v"), nil
	case "bool":
		return e["v"] == true, nil
	case "bytes":
		b, err := base64.StdEncoding.DecodeString(nStr(e, "v"))
		if err != nil {
			return nil, fmt.Errorf("diaspore: bad bytes constant")
		}
		return b, nil
	}
	return nil, fmt.Errorf("diaspore: bad const type %q", nStr(e, "t"))
}

func (c *compiler) compileKey(k node) evalFn {
	if nStr(k, "op") != "slicelit" {
		return c.compileExpr(k)
	}
	var lowFn, upFn, stepFn evalFn
	if n := nNode(k, "lower"); n != nil {
		lowFn = c.compileExpr(n)
	}
	if n := nNode(k, "upper"); n != nil {
		upFn = c.compileExpr(n)
	}
	if n := nNode(k, "step"); n != nil {
		stepFn = c.compileExpr(n)
	}
	return func(fr *frame) (Value, error) {
		s := &Slice{Lower: None, Upper: None, Step: None}
		if lowFn != nil {
			v, err := lowFn(fr)
			if err != nil {
				return nil, err
			}
			s.Lower = v
		}
		if upFn != nil {
			v, err := upFn(fr)
			if err != nil {
				return nil, err
			}
			s.Upper = v
		}
		if stepFn != nil {
			v, err := stepFn(fr)
			if err != nil {
				return nil, err
			}
			s.Step = v
		}
		return s, nil
	}
}

// intFastOp returns the specialized int64 x int64 implementation for a binop
// kind (falling back to the generic checked implementation).
func intFastOp(vm *VM, kind string) func(a, b int64) (Value, error) {
	switch kind {
	case "add":
		return func(a, b int64) (Value, error) {
			if c, ovf := addOvf(a, b); !ovf {
				return mkInt(c), nil
			}
			return vm.intBinop(kind, a, b)
		}
	case "sub":
		return func(a, b int64) (Value, error) {
			if c, ovf := subOvf(a, b); !ovf {
				return mkInt(c), nil
			}
			return vm.intBinop(kind, a, b)
		}
	case "mul":
		return func(a, b int64) (Value, error) {
			if c, ovf := mulOvf(a, b); !ovf {
				return mkInt(c), nil
			}
			return vm.intBinop(kind, a, b)
		}
	case "bitand":
		return func(a, b int64) (Value, error) { return mkInt(a & b), nil }
	case "bitor":
		return func(a, b int64) (Value, error) { return mkInt(a | b), nil }
	case "bitxor":
		return func(a, b int64) (Value, error) { return mkInt(a ^ b), nil }
	default:
		return func(a, b int64) (Value, error) { return vm.intBinop(kind, a, b) }
	}
}

func (c *compiler) compileBinop(e node) evalFn {
	vm := c.vm
	kind := nStr(e, "kind")
	lFn := c.compileExpr(nNode(e, "l"))
	rFn := c.compileExpr(nNode(e, "r"))
	fastII := intFastOp(vm, kind)
	floatOK := kind == "add" || kind == "sub" || kind == "mul" || kind == "div"
	return func(fr *frame) (Value, error) {
		l, err := lFn(fr)
		if err != nil {
			return nil, err
		}
		r, err := rFn(fr)
		if err != nil {
			return nil, err
		}
		if a, ok := l.(int64); ok {
			if b, ok := r.(int64); ok {
				return fastII(a, b)
			}
		} else if floatOK {
			if a, ok := l.(float64); ok {
				if b, ok := r.(float64); ok {
					switch kind {
					case "add":
						return a + b, nil
					case "sub":
						return a - b, nil
					case "mul":
						return a * b, nil
					case "div":
						if b != 0 {
							return a / b, nil
						}
					}
				}
			}
		}
		return vm.binopWith(kind, l, r, false)
	}
}

func (c *compiler) compileUnary(e node) evalFn {
	vm := c.vm
	kind := nStr(e, "kind")
	vFn := c.compileExpr(nNode(e, "v"))
	if kind == "not" {
		return func(fr *frame) (Value, error) {
			v, err := vFn(fr)
			if err != nil {
				return nil, err
			}
			if b, ok := v.(bool); ok {
				return !b, nil
			}
			b, err := vm.truth(v)
			if err != nil {
				return nil, err
			}
			return !b, nil
		}
	}
	return func(fr *frame) (Value, error) {
		v, err := vFn(fr)
		if err != nil {
			return nil, err
		}
		if kind == "neg" {
			if n, ok := v.(int64); ok && n != math.MinInt64 {
				return mkInt(-n), nil
			}
		}
		return vm.unary(kind, v)
	}
}

func (c *compiler) compileBoolOp(e node) evalFn {
	vm := c.vm
	isAnd := nStr(e, "kind") == "and"
	vals := nList(e, "values")
	fns := make([]evalFn, len(vals))
	for i, sub := range vals {
		fns[i] = c.compileExpr(sub.(node))
	}
	return func(fr *frame) (Value, error) {
		var v Value = None
		for i, fn := range fns {
			var err error
			v, err = fn(fr)
			if err != nil {
				return nil, err
			}
			if i == len(fns)-1 {
				return v, nil
			}
			b, ok := v.(bool)
			if !ok {
				b, err = vm.truth(v)
				if err != nil {
					return nil, err
				}
			}
			if isAnd != b {
				return v, nil
			}
		}
		return v, nil
	}
}

func (c *compiler) compileCompare(e node) evalFn {
	vm := c.vm
	leftFn := c.compileExpr(nNode(e, "left"))
	ops := nStrs(e, "ops")
	comps := nList(e, "comps")
	compFns := make([]evalFn, len(comps))
	for i, sub := range comps {
		compFns[i] = c.compileExpr(sub.(node))
	}
	if len(ops) == 1 {
		kind := ops[0]
		rightFn := compFns[0]
		intFast := func(a, b int64) (bool, bool) {
			switch kind {
			case "lt":
				return a < b, true
			case "le":
				return a <= b, true
			case "gt":
				return a > b, true
			case "ge":
				return a >= b, true
			case "eq":
				return a == b, true
			case "ne":
				return a != b, true
			}
			return false, false
		}
		return func(fr *frame) (Value, error) {
			l, err := leftFn(fr)
			if err != nil {
				return nil, err
			}
			r, err := rightFn(fr)
			if err != nil {
				return nil, err
			}
			if a, ok := l.(int64); ok {
				if b, ok := r.(int64); ok {
					if res, ok := intFast(a, b); ok {
						return res, nil
					}
				}
			}
			return vm.compareValue(kind, l, r)
		}
	}
	return func(fr *frame) (Value, error) {
		left, err := leftFn(fr)
		if err != nil {
			return nil, err
		}
		for i, kind := range ops {
			right, err := compFns[i](fr)
			if err != nil {
				return nil, err
			}
			result, err := vm.compareValue(kind, left, right)
			if err != nil {
				return nil, err
			}
			if i == len(ops)-1 {
				return result, nil
			}
			ok, err := vm.truth(result)
			if err != nil {
				return nil, err
			}
			if !ok {
				return result, nil
			}
			left = right
		}
		return true, nil
	}
}

func anyStarred(argNodes []any) bool {
	for _, a := range argNodes {
		if nStr(a.(node), "op") == "starred" {
			return true
		}
	}
	return false
}

// isLocalName reports whether id is bound as a strict-scope local (so a
// frame-aware builtin name would be shadowed).
func (c *compiler) isLocalName(id string) bool {
	if c.sc.strict {
		if _, ok := c.sc.layout[id]; ok {
			return true
		}
		if c.sc.gdecl[id] {
			return true
		}
	}
	return false
}

func (c *compiler) compileCall(e node) evalFn {
	vm := c.vm
	// Frame-aware builtins: exec/eval default their namespace to the calling
	// frame; globals()/locals() introspect it. Special-cased only for a bare,
	// unshadowed name.
	callee := nNode(e, "f")
	if nStr(callee, "op") == "name" && !c.isLocalName(nStr(callee, "id")) {
		id := nStr(callee, "id")
		argNodes := nList(e, "args")
		if (id == "exec" || id == "eval") && len(nList(e, "kw")) == 0 &&
			len(argNodes) >= 1 && len(argNodes) <= 3 && !anyStarred(argNodes) {
			mode := "exec"
			if id == "eval" {
				mode = "eval"
			}
			aFns := make([]evalFn, len(argNodes))
			for i, a := range argNodes {
				aFns[i] = c.compileExpr(a.(node))
			}
			return func(fr *frame) (Value, error) {
				vals := make([]Value, len(aFns))
				for i, f := range aFns {
					v, err := f(fr)
					if err != nil {
						return nil, err
					}
					vals[i] = v
				}
				var glob, loc Value
				if len(vals) >= 2 {
					glob = vals[1]
				}
				if len(vals) >= 3 {
					loc = vals[2]
				}
				return vm.execEval(fr, vals[0], glob, loc, mode)
			}
		}
		if (id == "globals" || id == "locals") && len(argNodes) == 0 && len(nList(e, "kw")) == 0 {
			if id == "globals" {
				return func(fr *frame) (Value, error) { return vm.globalsDict(fr), nil }
			}
			return func(fr *frame) (Value, error) { return vm.localsDict(fr), nil }
		}
	}
	fFn := c.compileExpr(nNode(e, "f"))
	argNodes := nList(e, "args")
	argFns := make([]evalFn, len(argNodes))
	starred := make([]bool, len(argNodes))
	hasStar := false
	for i, raw := range argNodes {
		a := raw.(node)
		if nStr(a, "op") == "starred" {
			starred[i] = true
			hasStar = true
			argFns[i] = c.compileExpr(nNode(a, "v"))
		} else {
			argFns[i] = c.compileExpr(a)
		}
	}
	type kwSpec struct {
		name string // "" = ** expansion
		fn   evalFn
	}
	var kwSpecs []kwSpec
	for _, raw := range nList(e, "kw") {
		kw := raw.(node)
		name, _ := kw["n"].(string)
		kwSpecs = append(kwSpecs, kwSpec{name, c.compileExpr(nNode(kw, "v"))})
	}

	buildKw := func(fr *frame) ([]KwArg, error) {
		var kwargs []KwArg
		for _, spec := range kwSpecs {
			v, err := spec.fn(fr)
			if err != nil {
				return nil, err
			}
			if spec.name != "" {
				kwargs = kwSet(kwargs, spec.name, v)
			} else {
				d, ok := v.(*Dict)
				if !ok {
					return nil, newError("TypeError", "argument after ** must be a mapping")
				}
				for _, entry := range d.entries {
					ks, ok := entry.key.(string)
					if !ok {
						return nil, newError("TypeError", "keywords must be strings")
					}
					kwargs = kwSet(kwargs, ks, entry.val)
				}
			}
		}
		return kwargs, nil
	}

	if !hasStar && len(kwSpecs) == 0 {
		n := len(argFns)
		return func(fr *frame) (Value, error) {
			f, err := fFn(fr)
			if err != nil {
				return nil, err
			}
			if fn, ok := f.(*Function); ok {
				cf := fn.cf
				if cf.fastCall && len(cf.params.args) == n {
					// Zero-alloc path: args are written straight into the
					// callee's slots; no []Value, no kwargs machinery.
					if err := vm.fuel(); err != nil {
						return nil, err
					}
					callee := cf.getFrame()
					callee.parent = fn.closure
					callee.defclass = fn.defclass
					if fn.closure != nil {
						callee.globals = fn.closure.globals
					}
					idx := cf.params.argIdx
					for i, aFn := range argFns {
						v, aerr := aFn(fr)
						if aerr != nil {
							cf.putFrame(callee)
							return nil, aerr
						}
						callee.slots[idx[i]] = v
					}
					if n > 0 {
						callee.self = callee.slots[idx[0]]
					}
					return vm.runFrame(cf, callee)
				}
			}
			var args []Value
			if n > 0 {
				args = make([]Value, n)
				for i, aFn := range argFns {
					if args[i], err = aFn(fr); err != nil {
						return nil, err
					}
				}
			}
			if _, ok := f.(*superMarkerT); ok {
				return vm.makeSuper(args, fr)
			}
			return vm.call(f, args, nil)
		}
	}
	return func(fr *frame) (Value, error) {
		f, err := fFn(fr)
		if err != nil {
			return nil, err
		}
		var args []Value
		for i, aFn := range argFns {
			v, err := aFn(fr)
			if err != nil {
				return nil, err
			}
			if starred[i] {
				items, err := vm.iterToSlice(v)
				if err != nil {
					return nil, err
				}
				args = append(args, items...)
			} else {
				args = append(args, v)
			}
		}
		kwargs, err := buildKw(fr)
		if err != nil {
			return nil, err
		}
		if _, ok := f.(*superMarkerT); ok {
			return vm.makeSuper(args, fr)
		}
		return vm.call(f, args, kwargs)
	}
}

// compileElts compiles display elements (with possible stars) into a slice
// builder.
func (c *compiler) compileElts(elts []any) func(fr *frame) ([]Value, error) {
	vm := c.vm
	fns := make([]evalFn, len(elts))
	starred := make([]bool, len(elts))
	hasStar := false
	for i, raw := range elts {
		x := raw.(node)
		if nStr(x, "op") == "starred" {
			starred[i] = true
			hasStar = true
			fns[i] = c.compileExpr(nNode(x, "v"))
		} else {
			fns[i] = c.compileExpr(x)
		}
	}
	if !hasStar {
		n := len(fns)
		return func(fr *frame) ([]Value, error) {
			out := make([]Value, n)
			for i, fn := range fns {
				v, err := fn(fr)
				if err != nil {
					return nil, err
				}
				out[i] = v
			}
			return out, nil
		}
	}
	return func(fr *frame) ([]Value, error) {
		var out []Value
		for i, fn := range fns {
			v, err := fn(fr)
			if err != nil {
				return nil, err
			}
			if starred[i] {
				items, err := vm.iterToSlice(v)
				if err != nil {
					return nil, err
				}
				out = append(out, items...)
			} else {
				out = append(out, v)
			}
		}
		return out, nil
	}
}

func (c *compiler) compileDict(e node) evalFn {
	type entrySpec struct {
		kFn evalFn // nil = ** expansion
		vFn evalFn
	}
	entries := nList(e, "entries")
	specs := make([]entrySpec, len(entries))
	for i, raw := range entries {
		entry := raw.(node)
		var kFn evalFn
		if kNode := nNode(entry, "k"); kNode != nil {
			kFn = c.compileExpr(kNode)
		}
		specs[i] = entrySpec{kFn, c.compileExpr(nNode(entry, "v"))}
	}
	return func(fr *frame) (Value, error) {
		out := NewDict()
		for _, spec := range specs {
			v, err := spec.vFn(fr)
			if err != nil {
				return nil, err
			}
			if spec.kFn != nil {
				k, err := spec.kFn(fr)
				if err != nil {
					return nil, err
				}
				if err := out.Set(k, v); err != nil {
					return nil, err
				}
			} else {
				d, ok := v.(*Dict)
				if !ok {
					return nil, newError("TypeError", "argument after ** must be a mapping")
				}
				for _, de := range d.entries {
					if err := out.Set(de.key, de.val); err != nil {
						return nil, err
					}
				}
			}
		}
		return out, nil
	}
}

func (c *compiler) compileFString(e node) evalFn {
	vm := c.vm
	type partSpec struct {
		static string // used when vFn is nil
		vFn    evalFn
		conv   string
		spec   evalFn // nil = empty spec
	}
	parts := nList(e, "parts")
	specs := make([]partSpec, len(parts))
	for i, raw := range parts {
		p := raw.(node)
		if s, ok := p["s"].(string); ok {
			specs[i] = partSpec{static: s}
			continue
		}
		ps := partSpec{vFn: c.compileExpr(nNode(p, "v"))}
		if conv, ok := p["conv"].(string); ok {
			ps.conv = conv
		}
		if sNode := nNode(p, "spec"); sNode != nil {
			ps.spec = c.compileExpr(sNode)
		}
		specs[i] = ps
	}
	minCap := 0
	for i := range specs {
		minCap += len(specs[i].static) + 8
	}
	return func(fr *frame) (Value, error) {
		b := make([]byte, 0, minCap)
		for i := range specs {
			p := &specs[i]
			if p.vFn == nil {
				b = append(b, p.static...)
				continue
			}
			v, err := p.vFn(fr)
			if err != nil {
				return nil, err
			}
			if p.conv == "" && p.spec == nil {
				// Fast append for the common {value} case: no intermediate
				// string allocation for ints and strings.
				switch t := v.(type) {
				case string:
					b = append(b, t...)
					continue
				case int64:
					b = strconv.AppendInt(b, t, 10)
					continue
				case bool:
					if t {
						b = append(b, "True"...)
					} else {
						b = append(b, "False"...)
					}
					continue
				case float64:
					b = append(b, pyFloatStr(t)...)
					continue
				}
			}
			switch p.conv {
			case "r":
				s, err := vm.repr(v)
				if err != nil {
					return nil, err
				}
				v = s
			case "s":
				s, err := vm.str(v)
				if err != nil {
					return nil, err
				}
				v = s
			case "a":
				s, err := vm.ascii(v)
				if err != nil {
					return nil, err
				}
				v = s
			}
			spec := ""
			if p.spec != nil {
				sv, err := p.spec(fr)
				if err != nil {
					return nil, err
				}
				ss, ok := sv.(string)
				if !ok {
					return nil, newError("TypeError", "format spec must be a string")
				}
				spec = ss
			}
			var s string
			if spec == "" {
				s, err = vm.str(v)
			} else {
				s, err = vm.formatValue(v, spec)
			}
			if err != nil {
				return nil, err
			}
			b = append(b, s...)
		}
		return string(b), nil
	}
}
