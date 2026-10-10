package interp

import (
	"sort"
	"strings"
)

// Metadata retains only sandbox state. Reflection objects and lexical cells are
// allocated on first access; ordinary calls keep using the compiled slot array.
type codeMetadata struct {
	source    node
	scope     *scope
	object    *codeObject
	positions codePositions
}

type functionMetadata struct {
	code       *codeObject
	closure    *Tuple
	defaults   *Tuple
	kwdefaults Value
	attrs      *metadataNamespace
}

type frameMetadata struct {
	code      *compiledFunc
	override  *codeObject
	source    node
	positions codePositions
	back      *frame
	lasti     int
	retained  bool
	object    *frameObject
	cells     map[int]*lexicalCell
	namespace *metadataNamespace
}

type metadataState struct {
	active            *frame
	typeNamespaces    map[*BuiltinType]*classDict
	descriptors       map[string]*metadataDescriptor
	nativeNamespaces  map[*NativeModule]*metadataNamespace
	constructors      map[*BuiltinType]*Builtin
	objectConstructor *Builtin
	objectNamespace   *classDict
}

type codeObject struct {
	cf        *compiledFunc
	source    node
	name      string
	qualname  string
	filename  string
	firstline int
	flags     int
	varnames  *Tuple
	freevars  *Tuple
	cellvars  *Tuple
	names     *Tuple
	consts    *Tuple
	refs      []freeRef
	positions codePositions
}

type lexicalCell struct {
	slot  *Value
	value Value
}

func (c *lexicalCell) contents() Value {
	if c.slot != nil {
		return *c.slot
	}
	return c.value
}

func (c *lexicalCell) store(v Value) {
	if c.slot != nil {
		*c.slot = v
	} else {
		c.value = v
	}
}

func (fr *frame) closureValue(index int) Value {
	if fr.closureCells != nil {
		return fr.closureCells[index].contents()
	}
	return fr.slots[index]
}

func (fr *frame) storeClosure(index int, value Value) {
	if fr.closureCells != nil {
		fr.closureCells[index].store(value)
	} else {
		fr.slots[index] = value
	}
}

func (c *compiler) mappingGlobalLoad(name string) evalFn {
	mapping, vm := c.globalMapping, c.vm
	return func(fr *frame) (Value, error) {
		value, err := vm.getIndex(mapping, name)
		if err == nil {
			return value, nil
		}
		if _, missing := raisedAs(err, "KeyError"); !missing {
			return nil, err
		}
		if value, found := vm.builtins[name]; found {
			return value, nil
		}
		return nil, newErrorf("NameError", "name '%s' is not defined", name)
	}
}

type frameObject struct {
	frame *frame
	code  *codeObject
}

type tracebackObject struct {
	frame *frameObject
	next  *tracebackObject
	line  int
	lasti int
}

// A namespace reads and writes the same cells used by compiled global loads.
// Reserved compiler bindings are not Python namespace entries.
type metadataNamespace struct {
	cells  map[string]*cell
	values map[string]Value
	extra  *Dict
}

func (ns *metadataNamespace) get(key Value) (Value, bool, error) {
	name, ok := key.(string)
	if !ok {
		if ns.extra != nil {
			return ns.extra.Get(key)
		}
		if _, err := hashKey(key); err != nil {
			return nil, false, err
		}
		return nil, false, nil
	}
	return ns.getString(name)
}

func (ns *metadataNamespace) getString(name string) (Value, bool, error) {
	if strings.HasPrefix(name, ".") {
		return nil, false, nil
	}
	if ns.cells != nil {
		c := ns.cells[name]
		if c != nil && c.v != nil {
			return c.v, true, nil
		}
		return nil, false, nil
	}
	v, found := ns.values[name]
	return v, found, nil
}

func (ns *metadataNamespace) set(key, value Value) error {
	name, ok := key.(string)
	if !ok {
		if ns.extra == nil {
			ns.extra = NewDict()
		}
		return ns.extra.Set(key, value)
	}
	return ns.setString(name, value)
}

func (ns *metadataNamespace) setString(name string, value Value) error {
	if strings.HasPrefix(name, ".") {
		return newError("ValueError", "reserved compiler binding is not a Python namespace entry")
	}
	if ns.cells != nil {
		c := ns.cells[name]
		if c == nil {
			c = &cell{}
			ns.cells[name] = c
		}
		c.v = value
	} else {
		ns.values[name] = value
	}
	return nil
}

func (ns *metadataNamespace) del(key Value) error {
	_, found, err := ns.get(key)
	if err != nil {
		return err
	}
	if !found {
		return newErrorArgs("KeyError", key)
	}
	name, text := key.(string)
	if !text {
		_, _, err := ns.extra.Delete(key)
		return err
	}
	if ns.cells != nil {
		// Compiled loads retain cell pointers, so deletion must not replace one.
		ns.cells[name].v = nil
	} else {
		delete(ns.values, name)
	}
	return nil
}

func (ns *metadataNamespace) keys() []Value {
	names := make([]string, 0, len(ns.cells)+len(ns.values))
	for name, c := range ns.cells {
		if c.v != nil && !strings.HasPrefix(name, ".") {
			names = append(names, name)
		}
	}
	for name := range ns.values {
		if !strings.HasPrefix(name, ".") {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	keys := metadataStrings(names).Items
	if ns.extra != nil {
		keys = append(keys, ns.extra.Keys()...)
	}
	return keys
}

func (ns *metadataNamespace) len() int {
	n := 0
	for name, c := range ns.cells {
		if c.v != nil && !strings.HasPrefix(name, ".") {
			n++
		}
	}
	for name := range ns.values {
		if !strings.HasPrefix(name, ".") {
			n++
		}
	}
	if ns.extra != nil {
		n += ns.extra.Len()
	}
	return n
}

func (ns *metadataNamespace) snapshot() *Dict {
	d := NewDict()
	for _, key := range ns.keys() {
		value, _, _ := ns.get(key)
		_ = d.Set(key, value)
	}
	return d
}

func metadataStrings(names []string) *Tuple {
	items := make([]Value, len(names))
	for i, name := range names {
		items[i] = name
	}
	return &Tuple{Items: items}
}

func metadataInt(n node, key string) int {
	switch value := n[key].(type) {
	case float64:
		return int(value)
	case int:
		return value
	case int64:
		return int(value)
	}
	return 0
}

func (cf *compiledFunc) initMetadata(source node, sc *scope) {
	cf.metadata.source = source
	cf.metadata.scope = sc
	sc.metadataName = cf.name
}

func metadataQualname(sc *scope) string {
	if sc == nil || sc.metadataName == "" {
		return ""
	}
	parent := metadataQualname(sc.parent)
	if parent == "" {
		return sc.metadataName
	}
	if sc.parent.strict {
		parent += ".<locals>"
	}
	return parent + "." + sc.metadataName
}

func (vm *VM) enterMetadataFrame(fr *frame) {
	fr.metadata.back = vm.metadata.active
	vm.metadata.active = fr
}

func (vm *VM) leaveMetadataFrame(fr *frame) {
	vm.metadata.active = fr.metadata.back
	if fr.gen != nil {
		fr.metadata.back = nil
	}
}

func (vm *VM) exposeMetadataFrame(fr *frame) *frameObject {
	if fr == nil {
		return nil
	}
	if fr.metadata.object == nil {
		fr.metadata.object = &frameObject{frame: fr}
		// f_back remains valid after either frame has returned to its caller.
		for f := fr; f != nil; f = f.metadata.back {
			f.metadata.retained = true
		}
	}
	return fr.metadata.object
}

func (vm *VM) recordMetadataTraceback(raised *Raised, fr *frame) {
	inst, ok := raised.Exc.(*Instance)
	if !ok || fr == nil || raised.metadataFrame == fr {
		return
	}
	next, _ := inst.dictValue("__traceback__").(*tracebackObject)
	raised.metadataFrame = fr
	inst.dictSet("__traceback__", &tracebackObject{
		frame: vm.exposeMetadataFrame(fr), next: next,
		line: fr.line, lasti: fr.metadata.lasti,
	})
}

func metadataScope(source node, parent *scope, class bool) *scope {
	s := &scope{parent: parent, strict: !class, isClass: class,
		annotation: source["annotation_scope"] == true,
		layout:     map[string]int{}, gdecl: strSet(nStrs(source, "gdecl")),
		ndecl: strSet(nStrs(source, "ndecl"))}
	for i, name := range nStrs(source, "locals") {
		s.layout[name] = i
	}
	return s
}

func metadataBinding(sc *scope, name string) *scope {
	if sc != nil && sc.gdecl[name] {
		return nil
	}
	for s := sc; s != nil; s = s.parent {
		if s.isClass {
			continue
		}
		if !s.strict {
			return nil
		}
		if s.gdecl[name] {
			return nil
		}
		if _, found := s.layout[name]; found {
			return s
		}
	}
	return nil
}

// Analyze the lexical IR rather than the values currently stored in a frame:
// empty cells and declarations without a load still belong to the closure.
func (co *codeObject) analyze(sc *scope) {
	free, captured, global := map[string]bool{}, map[string]bool{}, map[string]bool{}
	constants := []Value{None}
	if co.cf != nil {
		constants[0] = orNone(co.cf.doc)
	}
	var visit func(any, *scope)
	reference := func(name string, current *scope) {
		if name == "" || strings.HasPrefix(name, ".") {
			return
		}
		owner := metadataBinding(current, name)
		if owner == nil {
			if current == sc {
				global[name] = true
			}
			return
		}
		if owner == sc {
			if current != sc {
				captured[name] = true
			}
			return
		}
		for outer := sc.parent; outer != nil; outer = outer.parent {
			if outer == owner {
				free[name] = true
				return
			}
		}
	}
	visit = func(value any, current *scope) {
		switch v := value.(type) {
		case []any:
			for _, item := range v {
				visit(item, current)
			}
		case map[string]any:
			switch nStr(v, "op") {
			case "func", "funcexpr":
				p := nNode(v, "params")
				visit(nList(p, "defaults"), current)
				visit(nList(p, "kwdefaults"), current)
				child := metadataScope(v, current, false)
				for name := range child.ndecl {
					reference(name, child)
				}
				visit(nList(v, "body"), child)
				return
			case "class":
				visit(nList(v, "bases"), current)
				visit(nList(v, "body"), metadataScope(v, current, true))
				return
			case "name":
				reference(nStr(v, "id"), current)
			case "attr":
				if current == sc {
					global[nStr(v, "n")] = true
				}
			case "const":
				if current == sc {
					if item, err := parseConst(v); err == nil {
						constants = append(constants, item)
					}
				}
			}
			// JSON map iteration is not a source ordering. Sort fields so the
			// reflected constant pool is stable for the same executable IR.
			keys := make([]string, 0, len(v))
			for key := range v {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			for _, key := range keys {
				visit(v[key], current)
			}
		}
	}
	for name := range sc.ndecl {
		reference(name, sc)
	}
	visit(nList(co.source, "body"), sc)
	toNames := func(set map[string]bool) []string {
		names := make([]string, 0, len(set))
		for name := range set {
			names = append(names, name)
		}
		sort.Strings(names)
		return names
	}
	freeNames := toNames(free)
	co.freevars = metadataStrings(freeNames)
	co.cellvars = metadataStrings(toNames(captured))
	co.names = metadataStrings(toNames(global))
	co.consts = &Tuple{Items: constants}
	co.refs = make([]freeRef, 0, len(freeNames))
	for _, name := range freeNames {
		hops := 0
		for outer := sc.runtimeParent(); outer != nil; outer = outer.runtimeParent() {
			hops++
			if outer.isClass {
				continue
			}
			if !outer.strict {
				break
			}
			if index, found := outer.layout[name]; found {
				co.refs = append(co.refs, freeRef{hops: hops, idx: index, hard: index < outer.nHard})
				break
			}
		}
	}
}

func (vm *VM) compiledMetadata(cf *compiledFunc) *codeObject {
	if cf.metadata.object != nil {
		return cf.metadata.object
	}
	source := cf.metadata.source
	co := &codeObject{cf: cf, source: source, name: cf.name,
		qualname: metadataQualname(cf.metadata.scope), positions: cf.metadata.positions,
		filename: vm.modMeta[cf.moduleName].file, firstline: metadataInt(source, "ln"), flags: 3}
	if name := nStr(source, "qualname"); name != "" {
		co.qualname = name
	}
	if cf.params.vararg != "" {
		co.flags |= 4
	}
	if cf.params.kwarg != "" {
		co.flags |= 8
	}
	sc := cf.metadata.scope
	for outer := sc.parent; outer != nil; outer = outer.parent {
		if outer.strict {
			co.flags |= 16
			break
		}
	}
	if source["asyncgen"] == true {
		co.flags |= 512
	} else if source["coro"] == true {
		co.flags |= 128
	} else if cf.gen {
		co.flags |= 32
	}
	ordered := make([]string, cf.nSlots)
	for name, index := range cf.layout {
		ordered[index] = name
	}
	visible := ordered[:0]
	for _, name := range ordered {
		if !strings.HasPrefix(name, ".") {
			visible = append(visible, name)
		}
	}
	co.varnames = metadataStrings(visible)
	co.analyze(sc)
	cf.metadata.object = co
	return co
}

func (vm *VM) functionCode(fn *Function) *codeObject {
	if fn.metadata != nil && fn.metadata.code != nil {
		return fn.metadata.code
	}
	return vm.compiledMetadata(fn.cf)
}

func (fn *Function) metadataKeywordDefault(name string) (Value, bool) {
	if fn.metadata != nil {
		switch defaults := fn.metadata.kwdefaults.(type) {
		case *Dict:
			return defaults.GetStr(name)
		case *metadataNamespace:
			value, found, _ := defaults.get(name)
			return value, found
		}
	}
	value, found := fn.kwdefaults[name]
	return value, found
}

func (vm *VM) functionClosure(fn *Function) Value {
	if fn.metadata != nil && fn.metadata.closure != nil {
		return fn.metadata.closure
	}
	co := vm.functionCode(fn)
	if len(co.refs) == 0 {
		return None
	}
	if fn.metadata == nil {
		fn.metadata = &functionMetadata{}
	}
	if fn.metadata.closure == nil {
		items := make([]Value, len(co.refs))
		for i, ref := range co.refs {
			owner := fn.closure
			for hop := 1; hop < ref.hops; hop++ {
				owner = owner.parent
			}
			if owner.closureCells != nil {
				items[i] = owner.closureCells[ref.idx]
				continue
			}
			if owner.metadata.cells == nil {
				owner.metadata.cells = map[int]*lexicalCell{}
			}
			c := owner.metadata.cells[ref.idx]
			if c == nil {
				c = &lexicalCell{slot: &owner.slots[ref.idx]}
				owner.metadata.cells[ref.idx] = c
				owner.metadata.retained = true
			}
			items[i] = c
		}
		fn.metadata.closure = &Tuple{Items: items}
	}
	return fn.metadata.closure
}

func (vm *VM) frameNamespace(fr *frame) *metadataNamespace {
	if fr.metadata.namespace == nil {
		fr.metadata.namespace = &metadataNamespace{cells: fr.cells}
	}
	return fr.metadata.namespace
}

func (vm *VM) globalNamespace(fr *frame) Value {
	if fr.globalMapping != nil {
		return fr.globalMapping
	}
	for current := fr; current != nil; current = current.parent {
		if current.cells != nil {
			return vm.frameNamespace(current)
		}
	}
	return &metadataNamespace{cells: fr.globals}
}

func (vm *VM) frameCode(f *frameObject) *codeObject {
	if f.code != nil {
		return f.code
	}
	fr := f.frame
	if fr.metadata.override != nil {
		f.code = fr.metadata.override
		return f.code
	}
	if fr.metadata.code != nil {
		f.code = vm.compiledMetadata(fr.metadata.code)
		return f.code
	}
	source := fr.metadata.source
	if source == nil {
		raw, _ := vm.moduleIR(fr.moduleName)
		source, _ = raw.(node)
	}
	name := fr.fname
	if name == "" {
		name = "<module>"
	}
	positions := fr.metadata.positions
	if fr.metadata.source == nil {
		positions = vm.modMeta[fr.moduleName].positions
	}
	f.code = &codeObject{source: source, name: name, qualname: name,
		filename: vm.modMeta[fr.moduleName].file, firstline: metadataInt(source, "ln"),
		varnames: &Tuple{}, positions: positions}
	if name == "<module>" && source != nil {
		f.code.firstline = 1
	}
	f.code.analyze(&scope{})
	return f.code
}

func metadataTypeName(v Value) string {
	switch t := v.(type) {
	case *abcData:
		return "_abc_data"
	case *hashEngine:
		return "_HashEngine"
	case *codeObject:
		return "code"
	case *lexicalCell:
		return "cell"
	case *frameObject:
		return "frame"
	case *tracebackObject:
		return "traceback"
	case *metadataNamespace:
		return "dict"
	case *metadataDescriptor:
		return t.kind
	}
	return ""
}

func (vm *VM) metadataType(name string) *BuiltinType {
	if t := vm.types[name]; t != nil {
		return t
	}
	t := &BuiltinType{Name: name}
	t.ctor = func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		return nil, newErrorf("TypeError", "cannot create '%s' instances", name)
	}
	switch name {
	case "function":
		t.ctor = metadataFunctionConstructor
	case "mappingproxy":
		t.ctor = func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("mappingproxy", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("mappingproxy", kw); err != nil {
				return nil, err
			}
			if mapping, ok := rawPrim(args[0]).(*Dict); ok {
				return &classDict{mapping: mapping}, nil
			}
			if proxy, ok := args[0].(*classDict); ok {
				return proxy, nil
			}
			return nil, newErrorf("TypeError", "mappingproxy() argument must be a mapping, not %s", typeName(args[0]))
		}
	case "cell":
		t.ctor = func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw("cell", kw); err != nil {
				return nil, err
			}
			if err := argCount("cell", args, 0, 1); err != nil {
				return nil, err
			}
			c := &lexicalCell{}
			if len(args) != 0 {
				c.value = args[0]
			}
			return c, nil
		}
	case "traceback":
		t.ctor = metadataTracebackConstructor
	case "method":
		t.ctor = func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw("method", kw); err != nil {
				return nil, err
			}
			if err := argCount("method", args, 2, 2); err != nil {
				return nil, err
			}
			if _, none := args[1].(NoneType); none {
				return nil, newError("TypeError", "instance must not be None")
			}
			callable, err := biCallable(vm, args[:1], nil)
			if err != nil {
				return nil, err
			}
			if callable != true {
				return nil, newError("TypeError", "first argument must be callable")
			}
			return &BoundMethod{Fn: args[0], Recv: args[1]}, nil
		}
	}
	vm.types[name] = t
	return t
}

func metadataFunctionConstructor(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("function", args, 0, 5); err != nil {
		return nil, err
	}
	var values [5]Value
	copy(values[:], args)
	names := [...]string{"code", "globals", "name", "argdefs", "closure"}
	for _, item := range kw {
		index := -1
		for i, name := range names {
			if item.Name == name {
				index = i
				break
			}
		}
		if index < 0 {
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for function()", item.Name)
		}
		if values[index] != nil {
			return nil, newErrorf("TypeError", "function() got multiple values for argument '%s'", item.Name)
		}
		values[index] = item.Val
	}
	co, ok := values[0].(*codeObject)
	if !ok || co.cf == nil {
		return nil, newError("TypeError", "function() argument 'code' must be code")
	}
	switch rawPrim(values[1]).(type) {
	case *Dict, *metadataNamespace:
	default:
		return nil, newError("TypeError", "function() argument 'globals' must be dict")
	}
	name := co.name
	if values[2] != nil && values[2] != None {
		var err error
		name, err = wantStr("function() argument 'name'", values[2])
		if err != nil {
			return nil, err
		}
	}
	var defaults, closure *Tuple
	if values[3] != nil && values[3] != None {
		defaults, ok = values[3].(*Tuple)
		if !ok {
			return nil, newError("TypeError", "arg 4 (defaults) must be None or tuple")
		}
	}
	if values[4] != nil && values[4] != None {
		closure, ok = values[4].(*Tuple)
		if !ok {
			return nil, newError("TypeError", "arg 5 (closure) must be tuple")
		}
	}
	count := 0
	if closure != nil {
		count = len(closure.Items)
		for _, value := range closure.Items {
			if _, ok := value.(*lexicalCell); !ok {
				return nil, newError("TypeError", "arg 5 (closure) expected cell")
			}
		}
	}
	if count != len(co.freevars.Items) {
		if closure == nil {
			return nil, newError("TypeError", "arg 5 (closure) must be tuple")
		}
		return nil, newErrorf("ValueError", "%s requires closure of length %d, not %d", name, len(co.freevars.Items), count)
	}
	fn := &Function{name: name, metadata: &functionMetadata{code: co, closure: closure, defaults: defaults}}
	if defaults != nil {
		fn.defaults = defaults.Items
	}
	if err := vm.rebindFunction(fn, co, values[1], closure); err != nil {
		return nil, err
	}
	fn.doc = fn.cf.doc
	return fn, nil
}

// Recompile IR against the supplied bindings; compiled global cells and lexical
// slot offsets belong to the original definition, not to its reconstructed copy.
func (vm *VM) rebindFunction(fn *Function, co *codeObject, globals Value, closure *Tuple) error {
	cells := map[string]*cell{}
	mapping := rawPrim(globals)
	if namespace, ok := mapping.(*metadataNamespace); ok && namespace.cells != nil {
		cells, mapping = namespace.cells, nil
	}
	outer := &frame{globals: cells, globalMapping: mapping, cells: cells}
	if namespace, ok := globals.(*metadataNamespace); ok && namespace.cells != nil {
		outer.metadata.namespace = namespace
	}
	sc := &scope{}
	if closure != nil && len(closure.Items) > 0 {
		layout := make(map[string]int, len(closure.Items))
		bindings := make([]*lexicalCell, len(closure.Items))
		for i, value := range closure.Items {
			layout[co.freevars.Items[i].(string)] = i
			bindings[i] = value.(*lexicalCell)
		}
		sc = &scope{parent: sc, strict: true, layout: layout, nHard: len(bindings), nSlots: len(bindings)}
		outer = &frame{parent: outer, globals: cells, globalMapping: mapping, layout: layout, closureCells: bindings}
	}
	c := &compiler{vm: vm, moduleName: co.cf.moduleName, sc: sc, cells: cells, globalMapping: mapping}
	cf := c.compileFunction(co.source)
	if c.err != nil {
		return c.err
	}
	fn.cf, fn.closure = cf, outer
	return nil
}

func metadataTracebackConstructor(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("traceback", kw); err != nil {
		return nil, err
	}
	if err := argCount("traceback", args, 4, 4); err != nil {
		return nil, err
	}
	next, ok := args[0].(*tracebackObject)
	if _, none := args[0].(NoneType); !ok && !none {
		return nil, newError("TypeError", "tb_next must be a traceback or None")
	}
	fr, ok := args[1].(*frameObject)
	if !ok {
		return nil, newError("TypeError", "tb_frame must be a frame")
	}
	lasti, err := wantInt("tb_lasti", args[2])
	if err != nil {
		return nil, err
	}
	line, err := wantInt("tb_lineno", args[3])
	if err != nil {
		return nil, err
	}
	return &tracebackObject{frame: fr, next: next, lasti: int(lasti), line: int(line)}, nil
}

func nativeHashDisabled(name string) bool {
	switch name {
	case "list", "dict", "set", "bytearray":
		return true
	default:
		return false
	}
}

func (vm *VM) metadataAttr(value Value, name string) (Value, bool, error) {
	if name == "__hash__" {
		owner := ""
		switch object := value.(type) {
		case *BuiltinType:
			owner = object.Name
		case *Class, *Instance:
			// User classes and instances honor their own overrides below.
		default:
			owner = typeName(value)
		}
		if nativeHashDisabled(owner) {
			return None, true, nil
		}
	}
	if name == "__doc__" {
		if doc, found := nativeDocumentation(value); found {
			return doc, true, nil
		}
	}
	if name == "__class__" && metadataTypeName(value) != "" {
		return vm.metadataType(metadataTypeName(value)), true, nil
	}
	switch object := value.(type) {
	case *Function:
		switch name {
		case "__doc__":
			return orNone(object.doc), true, nil
		case "__get__":
			if attribute, found := object.attrs[name]; found {
				return attribute, true, nil
			}
			descriptor := vm.builtinMetadataDescriptor(vm.metadataType("function"), name)
			return descriptor.bind(object), true, nil
		case "__code__":
			return vm.functionCode(object), true, nil
		case "__closure__":
			return vm.functionClosure(object), true, nil
		case "__globals__":
			return vm.globalNamespace(object.closure), true, nil
		case "__dict__":
			if object.attrs == nil {
				object.attrs = map[string]Value{}
			}
			if object.metadata == nil {
				object.metadata = &functionMetadata{}
			}
			if object.metadata.attrs == nil {
				object.metadata.attrs = &metadataNamespace{values: object.attrs}
			}
			return object.metadata.attrs, true, nil
		case "__module__":
			if module, found := object.attrs[name]; found {
				return module, true, nil
			}
			return object.cf.moduleName, true, nil
		case "__qualname__":
			if qualname, found := object.attrs[name]; found {
				return qualname, true, nil
			}
			return vm.functionCode(object).qualname, true, nil
		case "__defaults__":
			if object.metadata != nil && object.metadata.defaults != nil {
				return object.metadata.defaults, true, nil
			}
			if len(object.defaults) == 0 {
				return None, true, nil
			}
			if object.metadata == nil {
				object.metadata = &functionMetadata{}
			}
			if object.metadata.defaults == nil {
				object.metadata.defaults = &Tuple{Items: object.defaults}
			}
			return object.metadata.defaults, true, nil
		case "__kwdefaults__":
			if object.metadata != nil && object.metadata.kwdefaults != nil {
				return object.metadata.kwdefaults, true, nil
			}
			if len(object.kwdefaults) == 0 {
				return None, true, nil
			}
			if object.metadata == nil {
				object.metadata = &functionMetadata{}
			}
			if object.metadata.kwdefaults == nil {
				object.metadata.kwdefaults = &metadataNamespace{values: object.kwdefaults}
			}
			return object.metadata.kwdefaults, true, nil
		}
	case *Module:
		if name == "__dict__" {
			return vm.frameNamespace(object.frame), true, nil
		}
	case *NativeModule:
		if name == "__dict__" {
			if vm.metadata.nativeNamespaces == nil {
				vm.metadata.nativeNamespaces = map[*NativeModule]*metadataNamespace{}
			}
			if ns := vm.metadata.nativeNamespaces[object]; ns != nil {
				return ns, true, nil
			}
			ns := &metadataNamespace{values: object.NS}
			vm.metadata.nativeNamespaces[object] = ns
			return ns, true, nil
		}
	case *codeObject:
		v, err := vm.codeMetadataAttr(object, name)
		return v, true, err
	case *lexicalCell:
		if name == "cell_contents" {
			v := object.contents()
			if v == nil {
				return nil, true, newError("ValueError", "Cell is empty")
			}
			return v, true, nil
		}
	case *tracebackObject:
		switch name {
		case "tb_frame":
			return object.frame, true, nil
		case "tb_next":
			if object.next == nil {
				return None, true, nil
			}
			return object.next, true, nil
		case "tb_lineno":
			return mkInt(int64(object.line)), true, nil
		case "tb_lasti":
			return mkInt(int64(object.lasti)), true, nil
		}
	case *frameObject:
		fr := object.frame
		switch name {
		case "f_code":
			return vm.frameCode(object), true, nil
		case "f_back":
			if fr.metadata.back == nil {
				return None, true, nil
			}
			return vm.exposeMetadataFrame(fr.metadata.back), true, nil
		case "f_lineno":
			return mkInt(int64(fr.line)), true, nil
		case "f_lasti":
			return mkInt(int64(fr.metadata.lasti)), true, nil
		case "f_globals":
			return vm.globalNamespace(fr), true, nil
		case "f_builtins":
			return &metadataNamespace{values: vm.builtins}, true, nil
		case "f_locals":
			if fr.cells != nil {
				return vm.frameNamespace(fr), true, nil
			}
			if fr.vars != nil {
				return &metadataNamespace{values: fr.vars}, true, nil
			}
			d := NewDict()
			for variable, index := range fr.layout {
				if v := fr.slots[index]; v != nil && !strings.HasPrefix(variable, ".") {
					d.SetStr(variable, v)
				}
			}
			co := vm.frameCode(object)
			if fr.metadata.code != nil {
				co = vm.compiledMetadata(fr.metadata.code)
			}
			for i, ref := range co.refs {
				outer := fr
				for range ref.hops {
					outer = outer.parent
				}
				if v := outer.closureValue(ref.idx); v != nil {
					d.SetStr(co.freevars.Items[i].(string), v)
				}
			}
			return d, true, nil
		}
	case *Instance:
		if object.Class.IsSub(baseExc) {
			switch name {
			case "__traceback__", "__cause__", "__context__":
				return orNone(object.dictValue(name)), true, nil
			case "__suppress_context__":
				if value := object.dictValue(name); value != nil {
					return value, true, nil
				}
				return false, true, nil
			}
		}
		attribute, overridden := object.Class.Lookup(name)
		if builtin, ok := attribute.(*Builtin); ok && objectClass.NS[name] == builtin {
			overridden = false
		}
		if !overridden {
			if name == "__init__" {
				if _, property := propertyValue(object); property || callableDescriptorKind(object) != "" {
					return nil, false, nil
				}
			}
			if _, shadowed := object.dictLookup(name); !shadowed {
				if name == "__hash__" && object.Class.Prim != nil && nativeHashDisabled(object.Class.Prim.Name) {
					return None, true, nil
				}
				if name == "__new__" {
					if object.Class.Prim != nil {
						return vm.primitiveNew(object.Class.Prim), true, nil
					}
					return vm.objectNewBuiltin(), true, nil
				}
				if object.Prim != nil {
					owner := vm.metadataType(typeName(object.Prim))
					if descriptor := vm.builtinMetadataDescriptor(owner, name); descriptor != nil && !descriptor.getter {
						if descriptor.kind == "classmethod_descriptor" {
							return descriptor.bind(object.Class), true, nil
						}
						return descriptor.bind(object), true, nil
					}
				}
				if descriptor := vm.objectMetadataDescriptor(name); descriptor != nil {
					if descriptor.kind == "classmethod_descriptor" {
						return descriptor.bind(object.Class), true, nil
					}
					return descriptor.bind(object), true, nil
				}
			}
		}
	case *Class:
		if name == "__hash__" && object.Prim != nil && nativeHashDisabled(object.Prim.Name) {
			attribute, found := object.Lookup(name)
			builtin, native := attribute.(*Builtin)
			if !found || native && builtin == objectClass.NS[name] {
				return None, true, nil
			}
		}
		if name == "__flags__" {
			flags := object.abcFlags
			if object.nativeImmutable || object == objectClass || excClasses[object.Name] == object {
				flags |= 256
			} else {
				flags |= 512
			}
			if !object.nativeFinal {
				flags |= 1024
			}
			return flags, true, nil
		}
		if object == objectClass {
			if name == "__dict__" {
				return vm.metadataObjectNamespace(), true, nil
			}
			if descriptor := vm.objectMetadataDescriptor(name); descriptor != nil {
				if descriptor.kind == "classmethod_descriptor" {
					return descriptor.bind(object), true, nil
				}
				return descriptor, true, nil
			}
		}
		if name == "__dict__" {
			return &classDict{cls: object}, true, nil
		}
		if object.Prim != nil {
			if _, overridden := object.Lookup(name); !overridden {
				if descriptor := vm.builtinMetadataDescriptor(object.Prim, name); descriptor != nil && descriptor.kind == "classmethod_descriptor" {
					return descriptor.bind(object), true, nil
				}
			}
		}
	case *BuiltinType:
		if name == "__flags__" {
			return int64(0), true, nil
		}
		if name == "maketrans" {
			switch object.Name {
			case "str":
				return strMakeTransBuiltin, true, nil
			case "bytes", "bytearray":
				return bytesMakeTransBuiltin, true, nil
			}
		}
		if name == "__dict__" {
			return vm.metadataTypeNamespace(object), true, nil
		}
		if descriptor := vm.builtinMetadataDescriptor(object, name); descriptor != nil {
			if descriptor.getter && descriptor.owner == vm.types["type"] {
				return vm.typeMetadataGet(object, name)
			}
			if descriptor.kind == "classmethod_descriptor" {
				return descriptor.bind(object), true, nil
			}
			return descriptor, true, nil
		}
	case *Builtin:
		switch name {
		case "__name__":
			return object.Name, true, nil
		case "__qualname__":
			if object.owner != nil {
				return metadataOwnerName(object.owner) + "." + object.Name, true, nil
			}
			return object.Name, true, nil
		case "__doc__":
			return orNone(object.doc), true, nil
		case "__self__":
			if object.owner != nil {
				return object.owner, true, nil
			}
		}
	case *Generator:
		switch name {
		case "gi_code", "cr_code", "ag_code":
			return vm.functionCode(object.fn), true, nil
		case "gi_frame", "cr_frame", "ag_frame":
			if object.ch.done.Load() {
				return None, true, nil
			}
			return vm.exposeMetadataFrame(object.frame), true, nil
		}
	case *metadataNamespace:
		if method := vm.metadataNamespaceMethod(object, name); method != nil {
			return method, true, nil
		}
	case *metadataDescriptor:
		v, err := object.attr(vm, name)
		return v, true, err
	}
	return nil, false, nil
}

func (vm *VM) codeMetadataAttr(co *codeObject, name string) (Value, error) {
	switch name {
	case "co_name":
		return co.name, nil
	case "co_qualname":
		return co.qualname, nil
	case "co_filename":
		return co.filename, nil
	case "co_firstlineno":
		return mkInt(int64(co.firstline)), nil
	case "co_flags":
		return mkInt(int64(co.flags)), nil
	case "co_argcount":
		return mkInt(int64(len(nList(nNode(co.source, "params"), "args")))), nil
	case "co_posonlyargcount":
		return mkInt(int64(metadataInt(nNode(co.source, "params"), "posonly"))), nil
	case "co_kwonlyargcount":
		return mkInt(int64(len(nList(nNode(co.source, "params"), "kwonly")))), nil
	case "co_nlocals":
		return mkInt(int64(len(co.varnames.Items))), nil
	case "co_varnames":
		return co.varnames, nil
	case "co_freevars":
		return co.freevars, nil
	case "co_cellvars":
		return co.cellvars, nil
	case "co_names":
		return co.names, nil
	case "co_consts":
		return co.consts, nil
	case "co_positions":
		return bi("co_positions", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs("co_positions", args, kw); err != nil {
				return nil, err
			}
			return &codePositionIter{positions: co.positions}, nil
		}), nil
	case "replace":
		return bi("replace", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("replace", args, 0, 0); err != nil {
				return nil, err
			}
			copy := *co
			for _, item := range kw {
				switch item.Name {
				case "co_name", "co_qualname", "co_filename":
					text, err := wantStr(item.Name, item.Val)
					if err != nil {
						return nil, err
					}
					switch item.Name {
					case "co_name":
						copy.name = text
					case "co_qualname":
						copy.qualname = text
					case "co_filename":
						copy.filename = text
					}
				case "co_flags", "co_firstlineno":
					number, err := wantInt(item.Name, item.Val)
					if err != nil {
						return nil, err
					}
					if number < 0 {
						return nil, newErrorf("ValueError", "%s must be a positive integer", item.Name)
					}
					if item.Name == "co_flags" {
						// Changing coroutine eligibility does not change the
						// compiled calling convention or execution body.
						if int(number) & ^256 != co.flags & ^256 {
							return nil, newError("ValueError", "cannot change the compiled calling convention")
						}
						copy.flags = int(number)
					} else {
						copy.firstline = int(number)
					}
				default:
					return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for replace()", item.Name)
				}
			}
			return &copy, nil
		}), nil
	}
	// Statement positions describe the IR execution addresses, not CPython
	// bytecode. Do not advertise invented co_code/co_stacksize/co_linetable data.
	return nil, newErrorf("AttributeError", "'code' object has no attribute '%s'", name)
}

func (vm *VM) metadataSetAttr(value Value, name string, replacement Value) (bool, error) {
	switch object := value.(type) {
	case *lexicalCell:
		if name == "cell_contents" {
			object.store(replacement)
			return true, nil
		}
	case *Function:
		switch name {
		case "__doc__":
			object.doc = replacement
			return true, nil
		case "__code__":
			co, ok := replacement.(*codeObject)
			if !ok || co.cf == nil {
				return true, newError("TypeError", "__code__ must be set to a function code object")
			}
			current := vm.functionCode(object)
			if len(current.refs) != len(co.refs) {
				return true, newErrorf("ValueError", "%s() requires a code object with %d free vars, not %d", funcName(object), len(current.refs), len(co.refs))
			}
			closure, _ := vm.functionClosure(object).(*Tuple)
			if err := vm.rebindFunction(object, co, vm.globalNamespace(object.closure), closure); err != nil {
				return true, err
			}
			if object.metadata == nil {
				object.metadata = &functionMetadata{}
			}
			object.metadata.code = co
			return true, nil
		case "__closure__", "__globals__":
			return true, newError("AttributeError", "readonly attribute")
		case "__defaults__":
			if _, none := replacement.(NoneType); none {
				object.defaults = nil
				if object.metadata != nil {
					object.metadata.defaults = nil
				}
				return true, nil
			}
			defaults, ok := replacement.(*Tuple)
			if !ok {
				return true, newError("TypeError", "__defaults__ must be set to a tuple object")
			}
			object.defaults = defaults.Items
			if object.metadata == nil {
				object.metadata = &functionMetadata{}
			}
			object.metadata.defaults = defaults
			return true, nil
		case "__kwdefaults__":
			if _, none := replacement.(NoneType); none {
				object.kwdefaults = nil
				if object.metadata != nil {
					object.metadata.kwdefaults = nil
				}
				return true, nil
			}
			switch replacement.(type) {
			case *Dict, *metadataNamespace:
			default:
				return true, newError("TypeError", "__kwdefaults__ must be set to a dict object")
			}
			if object.metadata == nil {
				object.metadata = &functionMetadata{}
			}
			object.metadata.kwdefaults = replacement
			return true, nil
		}
	case *tracebackObject:
		if name == "tb_next" {
			next, ok := replacement.(*tracebackObject)
			if _, none := replacement.(NoneType); !ok && !none {
				return true, newError("TypeError", "__traceback__ must be a traceback or None")
			}
			for current := next; current != nil; current = current.next {
				if current == object {
					return true, newError("ValueError", "traceback loop detected")
				}
			}
			object.next = next
			return true, nil
		}
		return true, newError("AttributeError", "readonly attribute")
	case *Instance:
		if object.Class.IsSub(baseExc) {
			switch name {
			case "__traceback__":
				if _, ok := replacement.(*tracebackObject); !ok {
					if _, none := replacement.(NoneType); !none {
						return true, newError("TypeError", "__traceback__ must be a traceback or None")
					}
				}
			case "__cause__", "__context__":
				if _, none := replacement.(NoneType); !none {
					if exception, ok := replacement.(*Instance); !ok || !exception.Class.IsSub(baseExc) {
						return true, newErrorf("TypeError", "%s must be an exception or None", name)
					}
				}
				if name == "__cause__" {
					object.dictSet("__suppress_context__", true)
				}
			case "__suppress_context__":
				if _, ok := replacement.(bool); !ok {
					return true, newError("TypeError", "attribute value type must be bool")
				}
			default:
				return false, nil
			}
			object.dictSet(name, replacement)
			return true, nil
		}
	case *codeObject, *frameObject, *metadataDescriptor:
		return true, newError("AttributeError", "readonly attribute")
	}
	return false, nil
}

func (vm *VM) metadataDelAttr(value Value, name string) (bool, error) {
	if exception, ok := value.(*Instance); ok && exception.Class.IsSub(baseExc) {
		switch name {
		case "__traceback__", "__cause__", "__context__", "__suppress_context__":
			return true, newErrorf("TypeError", "cannot delete %s", name)
		}
	}
	if cell, ok := value.(*lexicalCell); ok && name == "cell_contents" {
		cell.store(nil)
		return true, nil
	}
	if function, ok := value.(*Function); ok {
		switch name {
		case "__code__", "__closure__", "__globals__":
			return true, newError("TypeError", "cannot delete function metadata")
		case "__doc__", "__defaults__", "__kwdefaults__":
			return vm.metadataSetAttr(function, name, None)
		}
	}
	switch value.(type) {
	case *codeObject, *frameObject, *tracebackObject, *metadataDescriptor:
		return true, newError("AttributeError", "readonly attribute")
	}
	return false, nil
}

func (vm *VM) metadataNamespaceMethod(ns *metadataNamespace, name string) *Builtin {
	switch name {
	case "get", "pop", "setdefault":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 1, 2); err != nil {
				return nil, err
			}
			value, found, err := ns.get(args[0])
			if err != nil {
				return nil, err
			}
			if found {
				if name == "pop" {
					err = ns.del(args[0])
				}
				return value, err
			}
			fallback := Value(None)
			if len(args) == 2 {
				fallback = args[1]
			} else if name == "pop" {
				return nil, newErrorArgs("KeyError", args[0])
			}
			if name == "setdefault" {
				err = ns.set(args[0], fallback)
			}
			return fallback, err
		})
	case "clear", "copy", "keys", "values", "items":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs(name, args, kw); err != nil {
				return nil, err
			}
			if name == "clear" {
				for _, key := range ns.keys() {
					if err := ns.del(key); err != nil {
						return nil, err
					}
				}
				return None, nil
			}
			dict := ns.snapshot()
			if name == "copy" {
				return dict, nil
			}
			return &DictView{dict: dict, kind: name}, nil
		})
	case "update":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			incoming, err := ctorDict(vm, args, kw)
			if err != nil {
				return nil, err
			}
			dict := incoming.(*Dict)
			for _, key := range dict.Keys() {
				value, _, _ := dict.Get(key)
				if err := ns.set(key, value); err != nil {
					return nil, err
				}
			}
			return None, nil
		})
	}
	return nil
}

type metadataDescriptor struct {
	owner     Value
	name      string
	kind      string
	getter    bool
	writable  bool
	fn        *Builtin
	receiver  Value
	storage   slotStorage
	slotIndex int
	doc       Value
}

func (d *metadataDescriptor) bind(receiver Value) *metadataDescriptor {
	bound := *d
	bound.receiver = receiver
	if d.kind == "wrapper_descriptor" {
		bound.kind = "method-wrapper"
	} else {
		bound.kind = "builtin_function_or_method"
	}
	return &bound
}

func metadataOwnerName(owner Value) string {
	switch t := owner.(type) {
	case *BuiltinType:
		return t.Name
	case *Class:
		return t.Name
	}
	return typeName(owner)
}

func (d *metadataDescriptor) accepts(value Value) bool {
	if d.owner == objectClass {
		return true
	}
	if owner, ok := d.owner.(*BuiltinType); ok {
		name := typeName(rawPrim(value))
		return name == owner.Name || owner.Name == "int" && name == "bool"
	}
	if instance, ok := value.(*Instance); ok {
		return instance.Class.IsSub(d.owner.(*Class))
	}
	return false
}

func (d *metadataDescriptor) call(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if d.getter {
		return nil, newErrorf("TypeError", "'%s' object is not callable", d.kind)
	}
	if d.receiver != nil {
		args = append([]Value{d.receiver}, args...)
	}
	if len(args) == 0 {
		return nil, newErrorf("TypeError", "descriptor '%s' of '%s' object needs an argument", d.name, metadataOwnerName(d.owner))
	}
	if d.kind == "classmethod_descriptor" {
		if err := d.classmethodOwner(args[0]); err != nil {
			return nil, err
		}
	}
	if d.kind != "classmethod_descriptor" && d.receiver == nil && !d.accepts(args[0]) {
		return nil, newErrorf("TypeError", "descriptor '%s' for '%s' objects doesn't apply to a '%s' object", d.name, metadataOwnerName(d.owner), typeName(args[0]))
	}
	return d.fn.Fn(vm, args, kw)
}

func (d *metadataDescriptor) classmethodOwner(owner Value) error {
	valid, err := nativeSubclassCheck(owner, d.owner)
	if err != nil || !valid {
		return newErrorf("TypeError", "descriptor '%s' requires a subtype of '%s' but received '%s'",
			d.name, metadataOwnerName(d.owner), metadataOwnerName(owner))
	}
	return nil
}

func (d *metadataDescriptor) attr(vm *VM, name string) (Value, error) {
	switch name {
	case "__name__":
		return d.name, nil
	case "__qualname__":
		return metadataOwnerName(d.owner) + "." + d.name, nil
	case "__objclass__":
		return d.owner, nil
	case "__doc__":
		if d.doc != nil {
			return d.doc, nil
		}
		switch d.storage {
		case slotDictionary:
			return "dictionary for instance variables", nil
		case slotWeakref:
			return "list of weak references to the object", nil
		}
		return None, nil
	case "__self__":
		if d.receiver != nil {
			return d.receiver, nil
		}
	case "__get__":
		return bi("__get__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw("__get__", kw); err != nil {
				return nil, err
			}
			if err := argCount("__get__", args, 1, 2); err != nil {
				return nil, err
			}
			if d.kind == "classmethod_descriptor" {
				owner := d.owner
				if len(args) == 2 {
					owner = args[1]
				} else if _, none := args[0].(NoneType); !none {
					var err error
					owner, err = ctorType(vm, args[:1], nil)
					if err != nil {
						return nil, err
					}
				}
				if err := d.classmethodOwner(owner); err != nil {
					return nil, err
				}
				return d.bind(owner), nil
			}
			if _, none := args[0].(NoneType); none {
				if d.storage != slotNone && (len(args) < 2 || args[1] == None) {
					return nil, newError("TypeError", "__get__(None, None) is invalid")
				}
				return d, nil
			}
			if !d.accepts(args[0]) {
				return nil, newErrorf("TypeError", "descriptor '%s' for '%s' objects doesn't apply to a '%s' object", d.name, metadataOwnerName(d.owner), typeName(args[0]))
			}
			if d.storage != slotNone {
				return d.storageGet(vm, args[0])
			}
			if d.getter {
				if d.owner == vm.types["type"] {
					value, _, err := vm.typeMetadataGet(args[0], d.name)
					return value, err
				}
				return vm.getAttr(args[0], d.name)
			}
			return d.bind(args[0]), nil
		}), nil
	case "__set__":
		if d.getter {
			return bi("__set__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noKw("__set__", kw); err != nil {
					return nil, err
				}
				if err := argCount("__set__", args, 2, 2); err != nil {
					return nil, err
				}
				if !d.accepts(args[0]) {
					return nil, newError("TypeError", "descriptor does not apply to this object")
				}
				if d.storage != slotNone {
					return None, d.storageSet(vm, args[0], args[1], false)
				}
				if !d.writable {
					return nil, newError("AttributeError", "readonly attribute")
				}
				return None, vm.setAttr(args[0], d.name, args[1])
			}), nil
		}
	case "__delete__":
		if d.getter {
			return bi("__delete__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noKw("__delete__", kw); err != nil {
					return nil, err
				}
				if err := argCount("__delete__", args, 1, 1); err != nil {
					return nil, err
				}
				if !d.accepts(args[0]) {
					return nil, newError("TypeError", "descriptor does not apply to this object")
				}
				if d.storage != slotNone {
					return None, d.storageSet(vm, args[0], nil, true)
				}
				if !d.writable {
					return nil, newError("AttributeError", "readonly attribute")
				}
				return None, vm.delAttr(args[0], d.name)
			}), nil
		}
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", d.kind, name)
}

func (vm *VM) cacheMetadataDescriptor(d *metadataDescriptor) *metadataDescriptor {
	key := metadataOwnerName(d.owner) + "." + d.name
	if vm.metadata.descriptors == nil {
		vm.metadata.descriptors = map[string]*metadataDescriptor{}
	}
	if existing := vm.metadata.descriptors[key]; existing != nil {
		return existing
	}
	vm.metadata.descriptors[key] = d
	return d
}

func (vm *VM) objectMetadataDescriptor(name string) *metadataDescriptor {
	if d := vm.metadata.descriptors["object."+name]; d != nil {
		return d
	}
	var fn *Builtin
	switch name {
	case "__init_subclass__":
		fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 1, 1); err != nil {
				return nil, err
			}
			return None, nil
		})
	case "__reduce__":
		fn = bi(name, objectReduce)
	case "__reduce_ex__":
		fn = bi(name, objectReduceEx)
	case "__getstate__":
		fn = bi(name, objectGetState)
	case "__eq__", "__ne__":
		fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 2, 2); err != nil {
				return nil, err
			}
			if name == "__eq__" {
				return objectIdentityEqual(args[0], args[1]), nil
			}
			equal, err := vm.objectEqualitySlot(args[0], args[1])
			return vm.negateEquality(equal, err)
		})
	case "__format__":
		fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 2, 2); err != nil {
				return nil, err
			}
			spec, err := wantStr("format specifier", rawPrim(args[1]))
			if err != nil {
				return nil, err
			}
			if spec != "" {
				return nil, newErrorf("TypeError", "unsupported format string passed to %s.__format__", typeName(args[0]))
			}
			return vm.str(args[0])
		})
	case "__init__":
		fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw("__init__", kw); err != nil {
				return nil, err
			}
			if err := argCount("__init__", args, 1, 1); err != nil {
				return nil, err
			}
			return None, nil
		})
	case "__str__", "__repr__":
		fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 1, 1); err != nil {
				return nil, err
			}
			if name == "__str__" {
				return vm.repr(args[0])
			}
			return "<" + typeName(args[0]) + " object>", nil
		})
	case "__getattribute__":
		fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 2, 2); err != nil {
				return nil, err
			}
			attribute, err := wantStr("attribute name", args[1])
			if err != nil {
				return nil, err
			}
			if instance, ok := args[0].(*Instance); ok {
				return vm.baseGetAttr(instance, attribute)
			}
			return vm.getAttr(args[0], attribute)
		})
	case "__setattr__":
		fn = bi(name, biObjectSetattr)
	case "__delattr__":
		fn = bi(name, biObjectDelattr)
	case "__hash__":
		fn = bi(name, biObjectHash)
	}
	if fn == nil {
		return nil
	}
	kind := "wrapper_descriptor"
	if name == "__format__" || name == "__reduce__" || name == "__reduce_ex__" || name == "__getstate__" {
		kind = "method_descriptor"
	}
	if name == "__init_subclass__" {
		kind = "classmethod_descriptor"
	}
	return vm.cacheMetadataDescriptor(&metadataDescriptor{owner: objectClass, name: name, kind: kind, fn: fn})
}

func objectIdentityEqual(left, right Value) Value {
	if isIdentical(left, right) {
		return true
	}
	return NotImplemented
}

func (vm *VM) negateEquality(value Value, err error) (Value, error) {
	if _, declined := value.(NotImplementedType); err != nil || declined {
		return value, err
	}
	truth, err := vm.truth(value)
	return !truth, err
}

// object.__ne__ consults the receiver type's equality slot, never attributes on
// the instance and never the reflected slot on the other operand.
func (vm *VM) objectEqualitySlot(left, right Value) (Value, error) {
	switch object := left.(type) {
	case *Instance:
		if equality, found := object.Class.Lookup("__eq__"); found {
			return vm.callEqualityDescriptor(equality, object, right, object.Class)
		}
		if object.Prim != nil {
			return vm.nativeEqualitySlot(object.Prim, rawPrim(right))
		}
	case *Class:
		if object.Meta != nil {
			if equality, found := object.Meta.Lookup("__eq__"); found {
				return vm.callEqualityDescriptor(equality, object, right, object.Meta)
			}
		}
	default:
		return vm.nativeEqualitySlot(left, rawPrim(right))
	}
	return objectIdentityEqual(left, right), nil
}

func (vm *VM) callEqualityDescriptor(slot, object, other Value, owner *Class) (Value, error) {
	var method Value
	var err error
	if _, property := propertyValue(slot); property {
		method, err = vm.propertyGet(slot, object, owner)
	} else if callableDescriptorKind(slot) != "" {
		method, err = vm.callableDescriptorGet(slot, object, owner)
	} else if descriptor, ok := slot.(*Instance); ok {
		if _, binds := descriptor.Class.Lookup("__get__"); binds {
			method, err = vm.callDunder(descriptor, "__get__", []Value{object, owner})
		} else {
			method = slot
		}
	} else {
		method = vm.bindAttr(slot, object, owner)
	}
	if err != nil {
		return nil, err
	}
	return vm.call(method, []Value{other}, nil)
}

func metadataHasNativeEquality(name string) bool {
	switch name {
	case "int", "float", "bool", "complex", "str", "bytes", "bytearray", "memoryview",
		"list", "tuple", "dict", "mappingproxy", "set", "frozenset", "slice", "range",
		"GenericAlias", "UnionType", "dict_keys", "dict_items":
		return true
	}
	return false
}

func (vm *VM) nativeEqualitySlot(left, right Value) (Value, error) {
	leftName, rightName := typeName(left), typeName(right)
	if !metadataHasNativeEquality(leftName) {
		return objectIdentityEqual(left, right), nil
	}
	compatible := leftName == rightName || (isNumber(left) && isNumber(right))
	switch leftName {
	case "bytes", "bytearray", "memoryview":
		compatible = rightName == "bytes" || rightName == "bytearray" || rightName == "memoryview"
	case "dict":
		compatible = rightName == "dict"
	case "mappingproxy":
		compatible = rightName == "dict" || rightName == "mappingproxy"
	case "set", "frozenset":
		compatible = rightName == "set" || rightName == "frozenset"
	case "dict_keys", "dict_items":
		compatible = rightName == "set" || rightName == "frozenset" || rightName == "dict_keys" || rightName == "dict_items"
	}
	if !compatible {
		return NotImplemented, nil
	}
	return vm.eqBuiltin(left, right)
}

func (vm *VM) builtinMetadataDescriptor(owner *BuiltinType, name string) *metadataDescriptor {
	if owner.Name == "bool" && (name == "__eq__" || name == "__ne__" || name == "__format__" || name == "__hash__" || name == "__neg__" || name == "__pos__" || name == "__invert__") {
		return vm.builtinMetadataDescriptor(vm.types["int"], name)
	}
	if d := vm.metadata.descriptors[owner.Name+"."+name]; d != nil {
		return d
	}
	var kind string
	var getter, writable bool
	var fn *Builtin
	if name == "__hash__" || name == "__neg__" || name == "__pos__" || name == "__invert__" {
		switch owner.Name {
		case "int":
			kind, fn = "wrapper_descriptor", vm.builtinUnbound(owner.Name, name)
		case "float", "complex":
			if name != "__invert__" {
				kind, fn = "wrapper_descriptor", vm.builtinUnbound(owner.Name, name)
			}
		}
	}
	if name == "__get__" || name == "__set__" || name == "__delete__" {
		switch owner.Name {
		case "property":
			kind, fn = "wrapper_descriptor", vm.propertyTypeAttr(name).(*Builtin)
		case "staticmethod", "classmethod":
			if name == "__get__" {
				kind, fn = "wrapper_descriptor", vm.callableDescriptorTypeAttr(owner.Name, name).(*Builtin)
			}
		case "getset_descriptor", "member_descriptor", "method_descriptor", "wrapper_descriptor", "classmethod_descriptor":
			if name == "__get__" || owner.Name == "getset_descriptor" || owner.Name == "member_descriptor" {
				kind = "wrapper_descriptor"
				fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
					if len(args) == 0 {
						return nil, newErrorf("TypeError", "%s requires a descriptor", name)
					}
					method, err := args[0].(*metadataDescriptor).attr(vm, name)
					if err != nil {
						return nil, err
					}
					return vm.call(method, args[1:], kw)
				})
			}
		}
	}
	if owner.Name == "type" {
		switch name {
		case "__mro__", "__dict__", "__bases__":
			kind, getter = "getset_descriptor", true
		case "__base__":
			kind, getter = "member_descriptor", true
		case "__instancecheck__":
			kind, fn = "method_descriptor", bi(name, typeInstanceCheck)
		case "__subclasscheck__":
			kind, fn = "method_descriptor", bi(name, typeSubclassCheck)
		}
	}
	if name == "__init__" {
		switch owner.Name {
		case "dict":
			kind, fn = "wrapper_descriptor", bi(name, dictInit)
		case "bytearray":
			kind, fn = "wrapper_descriptor", bi(name, bytearrayInit)
		}
	}
	if name == "__init_subclass__" {
		return vm.objectMetadataDescriptor(name)
	}
	if name == "__reduce__" {
		switch owner.Name {
		case "set", "frozenset", "bytearray", "ellipsis", "NotImplementedType":
			kind, fn = "method_descriptor", bi(name, objectBuiltinReduce)
		}
	}
	if name == "__reduce_ex__" && owner.Name == "bytearray" {
		kind, fn = "method_descriptor", bi(name, bytearrayReduceEx)
	}
	if kind == "" && (name == "__reduce__" || name == "__reduce_ex__" || name == "__getstate__") && !builtinMethodNames[owner.Name][name] {
		return vm.objectMetadataDescriptor(name)
	}
	if name == "__format__" {
		switch owner.Name {
		case "str", "int", "float":
			kind = "method_descriptor"
			fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noKw(name, kw); err != nil {
					return nil, err
				}
				if err := argCount(name, args, 2, 2); err != nil {
					return nil, err
				}
				spec, err := wantStr("format specifier", rawPrim(args[1]))
				if err != nil {
					return nil, err
				}
				if spec == "" && owner.Name != "str" {
					return vm.str(args[0])
				}
				return vm.formatValue(rawPrim(args[0]), spec)
			})
		default:
			return vm.objectMetadataDescriptor(name)
		}
	}
	if (name == "__eq__" || name == "__ne__") && metadataHasNativeEquality(owner.Name) {
		kind = "wrapper_descriptor"
		fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 2, 2); err != nil {
				return nil, err
			}
			equal, err := vm.nativeEqualitySlot(rawPrim(args[0]), rawPrim(args[1]))
			if name == "__ne__" {
				return vm.negateEquality(equal, err)
			}
			return equal, err
		})
	}
	switch owner.Name {
	case "function":
		switch name {
		case "__get__":
			kind = "wrapper_descriptor"
			fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := noKw("__get__", kw); err != nil {
					return nil, err
				}
				if err := argCount("__get__", args, 2, 3); err != nil {
					return nil, err
				}
				if _, none := args[1].(NoneType); none {
					if len(args) < 3 || args[2] == None {
						return nil, newError("TypeError", "__get__(None, None) is invalid")
					}
					return args[0], nil
				}
				return &BoundMethod{Fn: args[0], Recv: args[1]}, nil
			})
		case "__doc__", "__code__", "__defaults__", "__kwdefaults__":
			kind, getter, writable = "getset_descriptor", true, true
		case "__closure__", "__globals__":
			kind, getter = "member_descriptor", true
		}
	case "cell":
		if name == "cell_contents" {
			kind, getter, writable = "getset_descriptor", true, true
		}
	case "dict":
		if name == "fromkeys" {
			kind, fn = "classmethod_descriptor", bi(name, metadataFromKeys)
		}
	case "bytes", "bytearray":
		if name == "fromhex" {
			kind = "classmethod_descriptor"
			fn = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				return vm.bytesFromHex(args[0], args[1:], kw)
			})
		}
	case "code":
		switch name {
		case "co_name", "co_qualname", "co_filename", "co_firstlineno", "co_flags",
			"co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
			"co_varnames", "co_freevars", "co_cellvars", "co_names", "co_consts":
			kind, getter = "member_descriptor", true
		}
	case "frame":
		switch name {
		case "f_code", "f_back", "f_lineno", "f_lasti", "f_globals", "f_locals", "f_builtins":
			kind, getter = "getset_descriptor", true
		}
	case "traceback":
		switch name {
		case "tb_next":
			kind, getter, writable = "getset_descriptor", true, true
		case "tb_frame", "tb_lineno", "tb_lasti":
			kind, getter = "member_descriptor", true
		}
	}
	if name == "__repr__" || name == "__str__" {
		kind, fn = "wrapper_descriptor", vm.builtinUnbound(owner.Name, name)
	}
	if kind == "" && builtinMethodNames[owner.Name][name] {
		kind, fn = "method_descriptor", vm.builtinUnbound(owner.Name, name)
	}
	if kind == "" {
		return nil
	}
	return vm.cacheMetadataDescriptor(&metadataDescriptor{owner: owner, name: name,
		kind: kind, getter: getter, writable: writable, fn: fn})
}

func metadataFromKeys(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("fromkeys", kw); err != nil {
		return nil, err
	}
	if err := argCount("fromkeys", args, 2, 3); err != nil {
		return nil, err
	}
	var result Value
	switch owner := args[0].(type) {
	case *BuiltinType:
		if owner.Name != "dict" {
			return nil, newError("TypeError", "fromkeys requires a dict subtype")
		}
		result = NewDict()
	case *Class:
		if owner.Prim == nil || owner.Prim.Name != "dict" {
			return nil, newError("TypeError", "fromkeys requires a dict subtype")
		}
		var err error
		result, err = vm.instantiate(owner, nil, nil)
		if err != nil {
			return nil, err
		}
	default:
		return nil, newError("TypeError", "fromkeys requires a type")
	}
	iter, err := vm.getIter(args[1])
	if err != nil {
		return nil, err
	}
	value := Value(None)
	if len(args) == 3 {
		value = args[2]
	}
	for {
		key, ok, err := iter.Next()
		if err != nil {
			return nil, err
		}
		if !ok {
			return result, nil
		}
		if err := vm.fuel(); err != nil {
			return nil, err
		}
		if err := vm.setIndex(result, key, value); err != nil {
			return nil, err
		}
	}
}

func (vm *VM) metadataObjectNamespace() *classDict {
	if vm.metadata.objectNamespace != nil {
		return vm.metadata.objectNamespace
	}
	ns := &classDict{cls: &Class{NS: map[string]Value{"__new__": vm.objectNewBuiltin()}}}
	ns.cls.NS["__doc__"] = nativeTypeDocumentation["object"]
	vm.metadata.objectNamespace = ns
	for _, name := range []string{"__eq__", "__ne__", "__init__", "__str__", "__repr__",
		"__format__", "__getattribute__", "__setattr__", "__delattr__", "__hash__",
		"__init_subclass__", "__reduce__", "__reduce_ex__", "__getstate__"} {
		ns.cls.NS[name] = vm.objectMetadataDescriptor(name)
	}
	return ns
}

func (vm *VM) metadataTypeNamespace(owner *BuiltinType) *classDict {
	if ns := vm.metadata.typeNamespaces[owner]; ns != nil {
		return ns
	}
	if vm.metadata.typeNamespaces == nil {
		vm.metadata.typeNamespaces = map[*BuiltinType]*classDict{}
	}
	// classDict is the existing read-only mapping view. Its backing namespace
	// is not a user class and is never returned as a type.
	ns := &classDict{cls: &Class{NS: map[string]Value{}}}
	vm.metadata.typeNamespaces[owner] = ns
	ns.cls.NS["__name__"] = owner.Name
	ns.cls.NS["__new__"] = vm.primitiveNew(owner)
	if nativeHashDisabled(owner.Name) {
		ns.cls.NS["__hash__"] = None
	}
	ns.cls.NS["__doc__"] = nativeTypeDoc(owner.Name)
	for name := range builtinMethodNames[owner.Name] {
		if d := vm.builtinMetadataDescriptor(owner, name); d != nil {
			ns.cls.NS[name] = d
		}
	}
	switch owner.Name {
	case "str":
		ns.cls.NS["maketrans"] = &StaticMethod{Fn: strMakeTransBuiltin}
	case "bytes", "bytearray":
		ns.cls.NS["maketrans"] = &StaticMethod{Fn: bytesMakeTransBuiltin}
	}
	names := []string{"__mro__", "__dict__", "__bases__", "__base__", "__instancecheck__", "__subclasscheck__", "__get__", "__set__", "__delete__", "__init__", "__format__", "__hash__", "__neg__", "__pos__", "__invert__", "__code__", "__closure__", "__globals__", "__defaults__", "__kwdefaults__",
		"cell_contents", "fromkeys", "fromhex", "co_name", "co_qualname", "co_filename", "co_firstlineno",
		"__doc__", "co_flags", "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
		"co_varnames", "co_freevars", "co_cellvars", "co_names", "co_consts",
		"f_code", "f_back", "f_lineno", "f_lasti", "f_globals", "f_locals", "f_builtins",
		"__reduce__", "__reduce_ex__", "__getstate__", "tb_next", "tb_frame", "tb_lineno", "tb_lasti"}
	for _, name := range names {
		if d := vm.builtinMetadataDescriptor(owner, name); d != nil && d.owner == owner {
			ns.cls.NS[name] = d
		}
	}
	return ns
}

// The explicit type checks bypass metaclass overrides: super() in a custom
// metaclass must reach the real inheritance check rather than call itself.
func typeSubclassCheck(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__subclasscheck__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__subclasscheck__", args, 2, 2); err != nil {
		return nil, err
	}
	return nativeSubclassCheck(args[1], args[0])
}

func nativeSubclassCheck(derived, base Value) (bool, error) {
	switch child := derived.(type) {
	case *Class:
		switch parent := base.(type) {
		case *Class:
			return parent == objectClass || child.IsSub(parent), nil
		case *BuiltinType:
			return child.Prim != nil && (child.Prim == parent || child.Prim.Name == "bool" && parent.Name == "int"), nil
		}
	case *BuiltinType:
		if base == objectClass {
			return true, nil
		}
		parent, ok := base.(*BuiltinType)
		return ok && (child == parent || child.Name == "bool" && parent.Name == "int"), nil
	default:
		return false, newError("TypeError", "issubclass() arg 1 must be a class")
	}
	return false, nil
}

func typeInstanceCheck(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__instancecheck__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__instancecheck__", args, 2, 2); err != nil {
		return nil, err
	}
	actual, err := ctorType(vm, args[1:], nil)
	if err != nil {
		return nil, err
	}
	match, err := nativeSubclassCheck(actual, args[0])
	if err != nil || match {
		return match, err
	}
	declared, err := vm.getAttr(args[1], "__class__")
	if err != nil {
		if _, missing := raisedAs(err, "AttributeError"); missing {
			return false, nil
		}
		return nil, err
	}
	switch declared.(type) {
	case *Class, *BuiltinType:
		return nativeSubclassCheck(declared, args[0])
	default:
		return false, nil
	}
}

// Read actual class storage without triggering a metaclass descriptor. In
// particular inspect.getattr_static relies on type's descriptors doing this.
func (vm *VM) typeMetadataGet(value Value, name string) (Value, bool, error) {
	switch cls := value.(type) {
	case *Class:
		switch name {
		case "__dict__":
			if cls == objectClass {
				return vm.metadataObjectNamespace(), true, nil
			}
			return &classDict{cls: cls}, true, nil
		case "__mro__":
			count := len(cls.MRO)
			if cls.Prim != nil {
				count++
			}
			items := make([]Value, 0, count)
			for _, entry := range cls.MRO {
				if entry == objectClass && cls.Prim != nil {
					items = append(items, cls.Prim)
				}
				items = append(items, entry)
			}
			return &Tuple{Items: items}, true, nil
		case "__bases__", "__base__":
			bases := baseValues(cls.Bases, cls.directPrim, cls.primIndex)
			if len(bases) == 0 && cls != objectClass {
				bases = []Value{objectClass}
			}
			if name == "__bases__" {
				return &Tuple{Items: bases}, true, nil
			}
			if len(bases) == 0 {
				return None, true, nil
			}
			best := bases[0]
			inherited := layoutOf(best)
			for _, base := range bases[1:] {
				candidate := layoutOf(base)
				if candidate.solid != inherited.solid && layoutSubtype(candidate.solid, inherited.solid) {
					best, inherited = base, candidate
				}
			}
			return best, true, nil
		}
	case *BuiltinType:
		base := Value(objectClass)
		if cls.Name == "bool" {
			base = vm.types["int"]
		}
		switch name {
		case "__dict__":
			return vm.metadataTypeNamespace(cls), true, nil
		case "__mro__":
			if cls.Name == "bool" {
				return &Tuple{Items: []Value{cls, base, objectClass}}, true, nil
			}
			return &Tuple{Items: []Value{cls, base}}, true, nil
		case "__bases__":
			return &Tuple{Items: []Value{base}}, true, nil
		case "__base__":
			return base, true, nil
		}
	}
	return nil, false, newErrorf("AttributeError", "type object has no attribute '%s'", name)
}
