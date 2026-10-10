package interp

import (
	"bytes"
	"compress/flate"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"math"
	"sort"
	"strings"
	"sync"
	"unicode/utf8"
)

//go:embed stdlib.dsps
var stdlibData []byte

type stdlibStore struct {
	id      string
	modules map[string]*stdlibModule
	names   []string
}

type stdlibModule struct {
	name       string
	file       string
	raw        []byte
	decoded    uint32
	codec      byte
	digest     [sha256.Size]byte
	dictionary []byte
	once       sync.Once
	ir         node
	err        error
}

func (module *stdlibModule) load() (node, error) {
	module.once.Do(func() {
		module.ir, module.err = module.decode()
	})
	return module.ir, module.err
}

func (module *stdlibModule) decode() (node, error) {
	if sha256.Sum256(module.raw) != module.digest {
		return nil, fmt.Errorf("stdlib module %q payload digest mismatch", module.name)
	}
	data := module.raw
	if module.codec == 1 {
		source := bytes.NewReader(data)
		var reader io.ReadCloser
		if len(module.dictionary) == 0 {
			reader = flate.NewReader(source)
		} else {
			reader = flate.NewReaderDict(source, module.dictionary)
		}
		defer reader.Close()
		data = make([]byte, int(module.decoded))
		if _, err := io.ReadFull(reader, data); err != nil {
			return nil, fmt.Errorf("stdlib module %q decoded length: %w", module.name, err)
		}
		var extra [1]byte
		if n, err := reader.Read(extra[:]); n != 0 || err != io.EOF || source.Len() != 0 {
			return nil, fmt.Errorf("stdlib module %q invalid deflate length or trailer", module.name)
		}
	}
	if !bytes.HasPrefix(data, dspbMagic) {
		return nil, fmt.Errorf("stdlib module %q must be a standalone DSPB01 bundle", module.name)
	}
	bundle, err := DecodeDSPB(data)
	if err != nil {
		return nil, err
	}
	modules, _ := bundle["modules"].(map[string]any)
	moduleIR, _ := modules[module.name].(map[string]any)
	if bundle["diaspore"] != float64(1) || bundle["main"] != module.name || len(modules) != 1 ||
		moduleIR["name"] != module.name || moduleIR["file"] != module.file {
		return nil, fmt.Errorf("stdlib module %q does not match its index record", module.name)
	}
	if _, linked := bundle["stdlib"]; linked {
		return nil, fmt.Errorf("stdlib module %q contains a stdlib reference", module.name)
	}
	if err := validateStoreModule(moduleIR); err != nil {
		return nil, fmt.Errorf("stdlib module %q: %w", module.name, err)
	}
	return moduleIR, nil
}

func loadStdlib() (*stdlibStore, error) {
	return indexStdlib(stdlibData)
}

const stdlibHeaderSize = 6 + 4 + sha256.Size
const stdlibMaxModuleSize = 256 << 20

// indexStdlib touches only the header and index, never the module payloads.
// The backing bytes are immutable for the lifetime of the store.
func indexStdlib(data []byte) (*stdlibStore, error) {
	if len(data) < stdlibHeaderSize || string(data[:6]) != "DSPS02" {
		return nil, fmt.Errorf("invalid shipped stdlib DSPS02 header")
	}
	indexSize := uint64(binary.LittleEndian.Uint32(data[6:10]))
	if indexSize < 38 || indexSize > 64<<20 || indexSize > uint64(len(data)-stdlibHeaderSize) {
		return nil, fmt.Errorf("invalid shipped stdlib index length")
	}
	index := data[stdlibHeaderSize : stdlibHeaderSize+int(indexSize)]
	digest := sha256.Sum256(index)
	if !bytes.Equal(digest[:], data[10:stdlibHeaderSize]) {
		return nil, fmt.Errorf("shipped stdlib index digest mismatch")
	}
	dictionarySize := int(binary.LittleEndian.Uint16(index[32:34]))
	if dictionarySize > 32768 || dictionarySize > len(index)-38 {
		return nil, fmt.Errorf("invalid shipped stdlib dictionary length")
	}
	dictionary := index[34 : 34+dictionarySize]
	count := binary.LittleEndian.Uint32(index[34+dictionarySize : 38+dictionarySize])
	if uint64(count) > uint64(len(index)-38-dictionarySize)/51 {
		return nil, fmt.Errorf("invalid shipped stdlib module count")
	}
	store := &stdlibStore{
		id:      "sha256:" + hex.EncodeToString(index[:32]),
		modules: make(map[string]*stdlibModule, int(count)),
		names:   make([]string, 0, int(count)),
	}
	index = index[38+dictionarySize:]
	payloads := data[stdlibHeaderSize+int(indexSize):]
	var next uint64
	previous := ""
	for range count {
		name, rest, err := stdlibIndexString(index)
		if err != nil {
			return nil, err
		}
		file, rest, err := stdlibIndexString(rest)
		if err != nil {
			return nil, err
		}
		if name <= previous || !validStoreModuleName(name) || strings.IndexByte(file, 0) >= 0 {
			return nil, fmt.Errorf("invalid shipped stdlib module name or file %q", name)
		}
		if len(rest) < 45 {
			return nil, fmt.Errorf("truncated shipped stdlib index record")
		}
		offset := uint64(binary.LittleEndian.Uint32(rest[:4]))
		size := uint64(binary.LittleEndian.Uint32(rest[4:8]))
		decoded := binary.LittleEndian.Uint32(rest[8:12])
		codec := rest[12]
		if offset != next || size < 1 || size > stdlibMaxModuleSize || offset+size > uint64(len(payloads)) ||
			decoded < 6 || decoded > stdlibMaxModuleSize || codec > 1 ||
			(codec == 0 && size != uint64(decoded)) {
			return nil, fmt.Errorf("invalid shipped stdlib payload span, length or codec for %q", name)
		}
		module := &stdlibModule{name: name, file: file, raw: payloads[int(offset):int(offset+size)], decoded: decoded, codec: codec, dictionary: dictionary}
		copy(module.digest[:], rest[13:45])
		store.modules[name] = module
		store.names = append(store.names, name)
		next, previous, index = offset+size, name, rest[45:]
	}
	if len(index) != 0 || next != uint64(len(payloads)) {
		return nil, fmt.Errorf("trailing shipped stdlib index or payload bytes")
	}
	return store, nil
}

func stdlibIndexString(data []byte) (string, []byte, error) {
	if len(data) < 2 {
		return "", nil, fmt.Errorf("truncated shipped stdlib index string")
	}
	size := int(binary.LittleEndian.Uint16(data[:2]))
	if size == 0 || size > len(data)-2 || !utf8.Valid(data[2:2+size]) {
		return "", nil, fmt.Errorf("invalid shipped stdlib index string")
	}
	return string(data[2 : 2+size]), data[2+size:], nil
}

func validStoreModuleName(name string) bool {
	for part := range strings.SplitSeq(name, ".") {
		if !pyIsIdentifier(part) {
			return false
		}
	}
	return true
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
