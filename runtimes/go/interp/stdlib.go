package interp

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"sync"
)

//go:embed stdlib.json.gz
var stdlibData []byte

type stdlibStore struct {
	id      string
	modules map[string]*stdlibModule
	names   []string
}

type stdlibModule struct {
	raw  []byte
	once sync.Once
	ir   node
	err  error
}

// UnmarshalJSON retains a view into the store's immutable decompressed bytes.
// Unlike json.RawMessage, it does not copy each module's serialized payload.
func (module *stdlibModule) UnmarshalJSON(data []byte) error {
	module.raw = data
	return nil
}

func (module *stdlibModule) load() (node, error) {
	module.once.Do(func() {
		var value any
		value, module.err = DecodeJSON(module.raw)
		if module.err != nil {
			return
		}
		var ok bool
		module.ir, ok = value.(map[string]any)
		if !ok {
			module.err = fmt.Errorf("stdlib module is not an object")
			return
		}
		if _, ok := module.ir["body"].([]any); !ok {
			module.err = fmt.Errorf("stdlib module body is not an array")
		}
	})
	return module.ir, module.err
}

type stdlibModuleIndex struct {
	raw     []byte
	modules map[string]*stdlibModule
}

// UnmarshalJSON indexes module payloads without constructing their IR trees.
func (index *stdlibModuleIndex) UnmarshalJSON(data []byte) error {
	index.raw = data
	return json.Unmarshal(data, &index.modules)
}

func loadStdlib() (*stdlibStore, error) {
	data, err := maybeGunzip(stdlibData)
	if err != nil {
		return nil, fmt.Errorf("cannot load shipped stdlib: %w", err)
	}
	return indexStdlib(data)
}

func indexStdlib(data []byte) (*stdlibStore, error) {
	var root struct {
		Version int               `json:"diaspore_stdlib"`
		ID      string            `json:"id"`
		Modules stdlibModuleIndex `json:"modules"`
	}
	if err := json.Unmarshal(data, &root); err != nil {
		return nil, fmt.Errorf("invalid shipped stdlib JSON: %w", err)
	}
	if root.Version != 1 {
		return nil, fmt.Errorf("not a diaspore v1 stdlib store")
	}
	if !validStdlibID(root.ID) {
		return nil, fmt.Errorf("invalid shipped stdlib content id")
	}
	if root.Modules.modules == nil {
		return nil, fmt.Errorf("shipped stdlib modules are not an object")
	}
	// Generated stores use canonical JSON. Hash the exact canonical modules
	// object, excluding notices and the declared id, without allocating IR.
	hash := sha256.New()
	hash.Write([]byte(`{"diaspore_stdlib":1,"modules":`))
	hash.Write(root.Modules.raw)
	hash.Write([]byte("}"))
	actual := "sha256:" + hex.EncodeToString(hash.Sum(nil))
	if root.ID != actual {
		return nil, fmt.Errorf("shipped stdlib content id mismatch: declared %s, actual %s", root.ID, actual)
	}
	names := make([]string, 0, len(root.Modules.modules))
	for name, module := range root.Modules.modules {
		if module == nil {
			return nil, fmt.Errorf("shipped stdlib module %q is not an object", name)
		}
		names = append(names, name)
	}
	sort.Strings(names)
	return &stdlibStore{id: root.ID, modules: root.Modules.modules, names: names}, nil
}

var shippedStdlib = sync.OnceValues(loadStdlib)

// StdlibID returns the content identity of the installed precompiled stdlib.
// The shared serialized store is indexed once; module IR is decoded on demand.
func StdlibID() (string, error) {
	store, err := shippedStdlib()
	if err != nil {
		return "", err
	}
	return store.id, nil
}

func linkedStdlib(bundle map[string]any) (*stdlibStore, error) {
	raw, linked := bundle["stdlib"]
	if !linked {
		return nil, nil
	}
	id, ok := raw.(string)
	if !ok || !validStdlibID(id) {
		return nil, fmt.Errorf("bundle stdlib must be a sha256 content id")
	}
	store, err := shippedStdlib()
	if err != nil {
		return nil, err
	}
	if id != store.id {
		return nil, fmt.Errorf("stdlib content id mismatch: bundle requires %s, installed %s", id, store.id)
	}
	return store, nil
}

func (vm *VM) moduleIR(name string) (any, bool) {
	if meta, found := vm.modMeta[name]; found && meta.source != nil {
		return meta.source, true
	}
	if modules, ok := vm.bundle["modules"].(map[string]any); ok {
		if module, found := modules[name]; found {
			return module, true
		}
	}
	return nil, false
}

func (vm *VM) hasModule(name string) bool {
	if _, found := vm.modcode[name]; found {
		return true
	}
	if _, found := vm.moduleIR(name); found {
		return true
	}
	if vm.stdlib != nil {
		_, found := vm.stdlib.modules[name]
		return found
	}
	return false
}

func (vm *VM) makeStdlibModule() *NativeModule {
	return &NativeModule{Name: "_diaspore_stdlib", NS: map[string]Value{
		"get_id": bi("get_id", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs("get_id", args, kw); err != nil {
				return nil, err
			}
			id, err := StdlibID()
			if err != nil {
				return nil, newError("RuntimeError", err.Error())
			}
			return id, nil
		}),
		"get_module": bi("get_module", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("get_module", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("get_module", kw); err != nil {
				return nil, err
			}
			name, ok := args[0].(string)
			if !ok {
				return nil, newError("TypeError", "module name must be a string")
			}
			store, err := shippedStdlib()
			if err != nil {
				return nil, newError("RuntimeError", err.Error())
			}
			module, found := store.modules[name]
			if !found {
				return None, nil
			}
			ir, err := module.load()
			if err != nil {
				return nil, newErrorf("RuntimeError", "cannot load stdlib module %q: %v", name, err)
			}
			return stdlibIRValue(ir), nil
		}),
		"module_names": bi("module_names", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs("module_names", args, kw); err != nil {
				return nil, err
			}
			store, err := shippedStdlib()
			if err != nil {
				return nil, newError("RuntimeError", err.Error())
			}
			items := make([]Value, len(store.names))
			for i, name := range store.names {
				items[i] = name
			}
			return &List{Items: items}, nil
		}),
	}}
}

// stdlibIRValue copies immutable host IR into independently mutable sandbox containers.
// IR numbers are integer metadata, unlike the string payloads of numeric constants.
func stdlibIRValue(value any) Value {
	switch value := value.(type) {
	case nil:
		return None
	case float64:
		if value == math.Trunc(value) && value >= math.MinInt64 && value < math.MaxInt64 {
			return mkInt(int64(value))
		}
		return value
	case []any:
		items := make([]Value, len(value))
		for i, item := range value {
			items[i] = stdlibIRValue(item)
		}
		return &List{Items: items}
	case map[string]any:
		keys := make([]string, 0, len(value))
		for key := range value {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		result := NewDict()
		for _, key := range keys {
			result.SetStr(key, stdlibIRValue(value[key]))
		}
		return result
	default:
		return value
	}
}
