// Package interp executes Diaspore bundles as an embeddable virtual machine.
// The language and runtime contract is defined in docs/spec.md.
//
// Diaspore values map to these concrete Go types:
//
//	int      -> int64 or *big.Int
//	float    -> float64
//	bool     -> bool
//	str      -> string
//	bytes    -> []byte
//	NoneType -> interp.NoneType (the singleton interp.None)
//	list     -> *interp.List
//	tuple    -> *interp.Tuple
//	dict     -> *interp.Dict (insertion-ordered)
//	set      -> *interp.Set  (insertion-ordered)
//	range    -> *interp.Range
//
// plus function, class, instance, module, generator, and file types.
package interp

import (
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
)

// Value is any Diaspore runtime value; see the package comment for the concrete
// types used.
type Value = any

// NativeFunc is a host function exposed to sandboxed code via WithBindings.
type NativeFunc func(args []Value) (Value, error)

// NoneType is the type of the None singleton.
type NoneType struct{}

// None is the Diaspore `None` value.
var None = NoneType{}

// EllipsisType is the type of the Ellipsis singleton (`...`).
type EllipsisType struct{}

// Ellipsis is the Diaspore `...` value.
var Ellipsis = EllipsisType{}

// List is a Diaspore list.
type List struct {
	// Items holds the elements in index order.
	Items []Value
}

// Tuple is a Diaspore tuple.
type Tuple struct {
	// Items holds the elements in index order.
	Items []Value
}

// Range is a Diaspore range object.
type Range struct {
	// Start is the inclusive lower bound.
	Start int64
	// Stop is the exclusive endpoint.
	Stop int64
	// Step is the nonzero increment between elements.
	Step int64
}

// Len returns the number of elements in the range.
func (r *Range) Len() int64 {
	if r.Step > 0 {
		if r.Stop <= r.Start {
			return 0
		}
		return (r.Stop - r.Start + r.Step - 1) / r.Step
	}
	if r.Stop >= r.Start {
		return 0
	}
	return (r.Start - r.Stop + (-r.Step) - 1) / (-r.Step)
}

// Slice is a slice object produced by `o[a:b:c]` subscripts. Absent bounds
// are None.
type Slice struct {
	// Lower is the start index, or None if omitted.
	Lower Value
	// Upper is the exclusive endpoint, or None if omitted.
	Upper Value
	// Step is the increment, or None if omitted.
	Step Value
}

type dictEntry struct {
	hk  string
	str bool // key is a string: hk is the raw key, indexed in strIndex
	key Value
	val Value
}

// Dict is a Diaspore dict: insertion-ordered with Python key hashing (1, 1.0 and
// True are the same key). String keys - the common case - live in their own
// index so lookups need no hash-key encoding.
type Dict struct {
	entries  []dictEntry
	strIndex map[string]int // string keys (raw)
	index    map[string]int // all other keys (hashKey-encoded)
}

// NewDict returns an empty dict.
func NewDict() *Dict { return &Dict{} }

// Len returns the number of entries.
func (d *Dict) Len() int { return len(d.entries) }

// GetStr looks up a string key.
func (d *Dict) GetStr(k string) (Value, bool) {
	if i, ok := d.strIndex[k]; ok {
		return d.entries[i].val, true
	}
	return nil, false
}

// Get looks up key k; the error is a TypeError for unhashable keys.
func (d *Dict) Get(k Value) (Value, bool, error) {
	if s, ok := k.(string); ok {
		v, ok := d.GetStr(s)
		return v, ok, nil
	}
	hk, err := hashKey(k)
	if err != nil {
		return nil, false, err
	}
	if i, ok := d.index[hk]; ok {
		return d.entries[i].val, true, nil
	}
	return nil, false, nil
}

// SetStr inserts or updates a string key.
func (d *Dict) SetStr(k string, v Value) {
	if i, ok := d.strIndex[k]; ok {
		d.entries[i].val = v
		return
	}
	if d.strIndex == nil {
		d.strIndex = map[string]int{}
	}
	d.strIndex[k] = len(d.entries)
	d.entries = append(d.entries, dictEntry{hk: k, str: true, key: k, val: v})
}

// Set inserts or updates key k.
func (d *Dict) Set(k, v Value) error {
	if s, ok := k.(string); ok {
		d.SetStr(s, v)
		return nil
	}
	hk, err := hashKey(k)
	if err != nil {
		return err
	}
	if i, ok := d.index[hk]; ok {
		d.entries[i].val = v
		return nil
	}
	if d.index == nil {
		d.index = map[string]int{}
	}
	d.index[hk] = len(d.entries)
	d.entries = append(d.entries, dictEntry{hk: hk, key: k, val: v})
	return nil
}

// lookupEntry finds the position of another dict's entry key (used by
// equality, which walks entries).
func (d *Dict) lookupEntry(e *dictEntry) (int, bool) {
	if e.str {
		i, ok := d.strIndex[e.hk]
		return i, ok
	}
	i, ok := d.index[e.hk]
	return i, ok
}

// Delete removes key k, reporting whether it was present.
func (d *Dict) Delete(k Value) (Value, bool, error) {
	var i int
	var ok bool
	if s, isStr := k.(string); isStr {
		i, ok = d.strIndex[s]
		if ok {
			delete(d.strIndex, s)
		}
	} else {
		hk, err := hashKey(k)
		if err != nil {
			return nil, false, err
		}
		i, ok = d.index[hk]
		if ok {
			delete(d.index, hk)
		}
	}
	if !ok {
		return nil, false, nil
	}
	v := d.entries[i].val
	d.entries = append(d.entries[:i], d.entries[i+1:]...)
	for j := i; j < len(d.entries); j++ {
		if d.entries[j].str {
			d.strIndex[d.entries[j].hk] = j
		} else {
			d.index[d.entries[j].hk] = j
		}
	}
	return v, true, nil
}

// Keys returns the keys in insertion order.
func (d *Dict) Keys() []Value {
	out := make([]Value, len(d.entries))
	for i, e := range d.entries {
		out[i] = e.key
	}
	return out
}

// Values returns the values in insertion order.
func (d *Dict) Values() []Value {
	out := make([]Value, len(d.entries))
	for i, e := range d.entries {
		out[i] = e.val
	}
	return out
}

// Copy returns a shallow copy.
func (d *Dict) Copy() *Dict {
	nd := &Dict{entries: append([]dictEntry{}, d.entries...)}
	if d.strIndex != nil {
		nd.strIndex = make(map[string]int, len(d.strIndex))
		for k, v := range d.strIndex {
			nd.strIndex[k] = v
		}
	}
	if d.index != nil {
		nd.index = make(map[string]int, len(d.index))
		for k, v := range d.index {
			nd.index[k] = v
		}
	}
	return nd
}

// Clear removes all entries.
func (d *Dict) Clear() {
	d.entries = nil
	d.strIndex = nil
	d.index = nil
}

// Set is a Diaspore set: insertion-ordered with Python element hashing.
type Set struct {
	entries []dictEntry // val unused
	index   map[string]int
}

// NewSet returns an empty set.
func NewSet() *Set { return &Set{index: map[string]int{}} }

// Len returns the number of elements.
func (s *Set) Len() int { return len(s.entries) }

// Has reports membership; the error is a TypeError for unhashable values.
func (s *Set) Has(k Value) (bool, error) {
	hk, err := hashKey(k)
	if err != nil {
		return false, err
	}
	_, ok := s.index[hk]
	return ok, nil
}

// Add inserts k if absent.
func (s *Set) Add(k Value) error {
	hk, err := hashKey(k)
	if err != nil {
		return err
	}
	if _, ok := s.index[hk]; !ok {
		s.index[hk] = len(s.entries)
		s.entries = append(s.entries, dictEntry{hk: hk, key: k})
	}
	return nil
}

// Discard removes k, reporting whether it was present.
func (s *Set) Discard(k Value) (bool, error) {
	hk, err := hashKey(k)
	if err != nil {
		return false, err
	}
	i, ok := s.index[hk]
	if !ok {
		return false, nil
	}
	s.entries = append(s.entries[:i], s.entries[i+1:]...)
	delete(s.index, hk)
	for j := i; j < len(s.entries); j++ {
		s.index[s.entries[j].hk] = j
	}
	return true, nil
}

// Elems returns the elements in insertion order.
func (s *Set) Elems() []Value {
	out := make([]Value, len(s.entries))
	for i, e := range s.entries {
		out[i] = e.key
	}
	return out
}

// Copy returns a shallow copy.
func (s *Set) Copy() *Set {
	ns := NewSet()
	ns.entries = append(ns.entries, s.entries...)
	for k, v := range s.index {
		ns.index[k] = v
	}
	return ns
}

// Clear removes all elements.
func (s *Set) Clear() {
	s.entries = nil
	s.index = map[string]int{}
}

// Class is a user-defined class.
type Class struct {
	// Name is the class's __name__.
	Name string
	// Bases holds the direct base classes in declaration order.
	Bases []*Class
	// NS holds attributes defined on this class.
	NS map[string]Value
	// MRO holds the method resolution order, including this class.
	MRO []*Class
	// Prim is the builtin base for primitive subclasses, or nil.
	Prim *BuiltinType
}

// Lookup finds name along the MRO.
func (c *Class) Lookup(name string) (Value, bool) {
	for _, k := range c.MRO {
		if v, ok := k.NS[name]; ok {
			return v, true
		}
	}
	return nil, false
}

// IsSub reports whether c is other or a subclass of other.
func (c *Class) IsSub(other *Class) bool {
	for _, k := range c.MRO {
		if k == other {
			return true
		}
	}
	return false
}

// Instance is an instance of a user-defined class.
type Instance struct {
	// Class supplies the instance's type and method resolution order.
	Class *Class
	// D holds instance attributes.
	D map[string]Value
	// Prim holds the underlying value for a builtin-subclass instance.
	Prim Value
}

// Function is a user-defined (diaspore) function: a compiled body plus the
// definition-site state (closure frame, defaults, defining class).
type Function struct {
	cf         *compiledFunc
	closure    *frame
	defclass   *Class
	defaults   []Value
	kwdefaults map[string]Value
	name       string           // overridable via f.__name__
	attrs      map[string]Value // user-assigned function attributes
}

type paramSpec struct {
	args      []string
	argIdx    []int
	vararg    string // "" = none
	varargIdx int    // -1 = none
	kwonly    []string
	kwonlyIdx []int
	kwarg     string // "" = none
	kwargIdx  int    // -1 = none
	ndefault  int
}

// BoundMethod pairs a callable with a receiver (self or cls).
type BoundMethod struct {
	// Fn is the callable to invoke.
	Fn Value
	// Recv is prepended to positional arguments when Fn is called.
	Recv Value
}

// StaticMethod wraps a function stored on a class so attribute access does
// not bind it.
type StaticMethod struct {
	// Fn is returned without binding a receiver.
	Fn Value
}

// ClassMethod wraps a function stored on a class so attribute access binds
// the class instead of the instance.
type ClassMethod struct {
	// Fn is bound to the class during attribute lookup.
	Fn Value
}

// Super is the object returned by super() / super(C, obj).
type Super struct {
	// Cls is the class after which attribute lookup starts in the MRO.
	Cls *Class
	// Obj is the bound instance or class.
	Obj Value
}

// Module is an executed user module from the bundle.
type Module struct {
	// Name is the module's import name.
	Name  string
	frame *frame
}

// NativeModule is a built-in or host-bound module.
type NativeModule struct {
	// Name is the module's import name.
	Name string
	// NS holds the module's exported attributes.
	NS map[string]Value
}

// Builtin is a native function exposed inside the sandbox.
type Builtin struct {
	// Name is the function name used in diagnostics.
	Name string
	// Fn implements the call using positional and keyword arguments.
	Fn func(vm *VM, args []Value, kw []KwArg) (Value, error)
}

// KwArg is one keyword argument in a call (insertion-ordered).
type KwArg struct {
	// Name is the keyword's spelling.
	Name string
	// Val is the argument value.
	Val Value
}

// kwSet updates name in kw (keeping its position) or appends it.
func kwSet(kw []KwArg, name string, v Value) []KwArg {
	for i := range kw {
		if kw[i].Name == name {
			kw[i].Val = v
			return kw
		}
	}
	return append(kw, KwArg{name, v})
}

func kwGet(kw []KwArg, name string) (Value, bool) {
	for _, k := range kw {
		if k.Name == name {
			return k.Val, true
		}
	}
	return nil, false
}

// BuiltinType represents `int`, `str`, ... as first-class values.
type BuiltinType struct {
	// Name is the Python type name.
	Name string
	ctor func(vm *VM, args []Value, kw []KwArg) (Value, error)
}

// NativeObject is a host-implemented object exposed to the sandbox with a
// fixed method table (re.Match, tasks.Task, tasks.Channel, ...).
type NativeObject struct {
	// Kind is the Python type name exposed by this object.
	Kind    string
	methods map[string]*Builtin
	iterFn  func() Iterator // non-nil for iterable natives (channels)
}

// Property is a property descriptor (property(fget, fset, fdel)).
type Property struct {
	// Fget is the getter callable, or nil if absent.
	Fget Value
	// Fset is the setter callable, or nil if absent.
	Fset Value
	// Fdel is the deleter callable, or nil if absent.
	Fdel Value
	// Doc is the descriptor's __doc__ value.
	Doc Value
}

func subclassablePrim(name string) bool {
	switch name {
	case "int", "float", "str", "bytes", "tuple", "list", "dict", "set", "bool":
		return true
	}
	return false
}

type superMarkerT struct{}

// superMarker is what the name `super` resolves to; calls are intercepted.
var superMarker = &superMarkerT{}

// FromGo converts a native Go value into a diaspore Value, recursively:
// nil -> None, bool, string, integer kinds -> int64, float32/float64 -> float64,
// []byte -> bytes, []any -> list, map[string]any -> dict (keys sorted for
// determinism). Values that already are diaspore values pass through unchanged.
func FromGo(v any) (Value, error) {
	switch t := v.(type) {
	case nil:
		return None, nil
	case NoneType, EllipsisType, bool, int64, float64, string,
		*List, *Tuple, *Dict, *Set, *Range, *Instance, *Class, *big.Int:
		return t, nil
	case int:
		return int64(t), nil
	case int8:
		return int64(t), nil
	case int16:
		return int64(t), nil
	case int32:
		return int64(t), nil
	case uint:
		return int64(t), nil
	case uint8:
		return int64(t), nil
	case uint16:
		return int64(t), nil
	case uint32:
		return int64(t), nil
	case uint64:
		if t > math.MaxInt64 {
			return nil, fmt.Errorf("diaspore: uint64 value %d overflows int64", t)
		}
		return int64(t), nil
	case float32:
		return float64(t), nil
	case []byte:
		return append([]byte{}, t...), nil
	case []any:
		items := make([]Value, len(t))
		for i, x := range t {
			cv, err := FromGo(x)
			if err != nil {
				return nil, err
			}
			items[i] = cv
		}
		return &List{Items: items}, nil
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		d := NewDict()
		for _, k := range keys {
			cv, err := FromGo(t[k])
			if err != nil {
				return nil, err
			}
			d.SetStr(k, cv)
		}
		return d, nil
	}
	return nil, fmt.Errorf("diaspore: cannot convert %T to a diaspore value", v)
}

func typeName(v Value) string {
	switch t := v.(type) {
	case nil:
		return "NoneType"
	case NoneType:
		return "NoneType"
	case EllipsisType:
		return "ellipsis"
	case bool:
		return "bool"
	case int64:
		return "int"
	case float64:
		return "float"
	case string:
		return "str"
	case []byte:
		return "bytes"
	case *List:
		return "list"
	case *Tuple:
		return "tuple"
	case *Dict:
		return "dict"
	case *Set:
		return "set"
	case *Range:
		return "range"
	case *Slice:
		return "slice"
	case complex128:
		return "complex"
	case *Property:
		return "property"
	case *Instance:
		return t.Class.Name
	case *Class, *BuiltinType:
		return "type"
	case *Function:
		return "function"
	case *BoundMethod:
		return "method"
	case *Builtin:
		return "builtin_function_or_method"
	case *Generator:
		return "generator"
	case *Module, *NativeModule:
		return "module"
	case *File:
		return "file"
	case *NativeObject:
		return t.Kind
	case *big.Int:
		return "int"
	case *StaticMethod:
		return "staticmethod"
	case *ClassMethod:
		return "classmethod"
	case *Super:
		return "super"
	case *superMarkerT:
		return "super"
	case Iterator:
		return t.KindName()
	}
	return fmt.Sprintf("%T", v)
}

// hashKey produces a unique, comparable encoding of a hashable value, with
// Python's numeric key unification (1, 1.0 and True hash alike). Unhashable
// values yield a TypeError.
func hashKey(v Value) (string, error) {
	switch t := v.(type) {
	case NoneType:
		return "N", nil
	case EllipsisType:
		return "E", nil
	case bool:
		if t {
			return "i1", nil
		}
		return "i0", nil
	case int64:
		return "i" + strconv.FormatInt(t, 10), nil
	case float64:
		if t == math.Trunc(t) && t >= math.MinInt64 && t <= math.MaxInt64 && !math.IsInf(t, 0) {
			return "i" + strconv.FormatInt(int64(t), 10), nil
		}
		if math.IsNaN(t) {
			return "fnan", nil
		}
		if t == math.Trunc(t) && !math.IsInf(t, 0) {
			// Huge integral floats hash like the exact integer they equal.
			z, _ := new(big.Float).SetFloat64(t).Int(nil)
			return "i" + z.String(), nil
		}
		return "f" + strconv.FormatUint(math.Float64bits(t), 16), nil
	case *big.Int:
		return "i" + t.String(), nil
	case complex128:
		if imag(t) == 0 {
			return hashKey(real(t))
		}
		return "c" + strconv.FormatUint(math.Float64bits(real(t)), 16) +
			"," + strconv.FormatUint(math.Float64bits(imag(t)), 16), nil
	case string:
		return "s" + t, nil
	case []byte:
		return "b" + string(t), nil
	case *Tuple:
		out := "t"
		for _, x := range t.Items {
			k, err := hashKey(x)
			if err != nil {
				return "", newError("TypeError", "unhashable type: '"+typeName(v)+"'")
			}
			out += strconv.Itoa(len(k)) + ":" + k
		}
		return out, nil
	case *Range:
		return fmt.Sprintf("r%d,%d,%d", t.Start, t.Stop, t.Step), nil
	case *Slice:
		lo, e1 := hashKey(t.Lower)
		up, e2 := hashKey(t.Upper)
		st, e3 := hashKey(t.Step)
		if e1 != nil || e2 != nil || e3 != nil {
			return "", newError("TypeError", "unhashable type: 'slice'")
		}
		return "S" + lo + "|" + up + "|" + st, nil
	case *Instance, *Class, *BuiltinType, *Function, *BoundMethod, *Builtin,
		*Module, *NativeModule, *Generator, *StaticMethod, *ClassMethod:
		return fmt.Sprintf("p%p", v), nil
	}
	return "", newError("TypeError", "unhashable type: '"+typeName(v)+"'")
}
