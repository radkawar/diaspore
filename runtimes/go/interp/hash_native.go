package interp

import (
	"crypto/md5"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"hash"
)

// hashEngine stores only the native incremental compression state and at most
// one partial block. Guest input is consumed synchronously, never retained.
// Public algorithm names, metadata and constructors remain in _hashcore/hashlib.
type hashEngine struct {
	state hash.Cloner
}

func (vm *VM) makeHashModule() *NativeModule {
	return &NativeModule{Name: "_diaspore_hash", NS: map[string]Value{
		"new": bi("new", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw("new", kw); err != nil {
				return nil, err
			}
			if err := argCount("new", args, 1, 1); err != nil {
				return nil, err
			}
			name, ok := rawPrim(args[0]).(string)
			if !ok {
				return nil, newErrorf("TypeError", "hash name must be str, not %s", typeName(args[0]))
			}
			var state hash.Hash
			switch name {
			case "md5":
				state = md5.New()
			case "sha1":
				state = sha1.New()
			case "sha224":
				state = sha256.New224()
			case "sha256":
				state = sha256.New()
			case "sha384":
				state = sha512.New384()
			case "sha512":
				state = sha512.New()
			default:
				return nil, newErrorf("ValueError", "unsupported hash type %s", name)
			}
			// All Go 1.26 crypto hashes implement hash.Cloner. There is no
			// serialization fallback and no replay buffer of previous inputs.
			return &hashEngine{state: state.(hash.Cloner)}, nil
		}),
	}}
}

func (engine *hashEngine) attr(name string) (Value, bool) {
	switch name {
	case "update":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 1, 1); err != nil {
				return nil, err
			}
			data, err := contiguousBufferBytes(args[0])
			if err != nil {
				return nil, err
			}
			if err := vm.fuelN(int64(len(data))); err != nil {
				return nil, err
			}
			if _, err := engine.state.Write(data); err != nil {
				return nil, newError("RuntimeError", err.Error())
			}
			return None, nil
		}), true
	case "copy":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 0, 0); err != nil {
				return nil, err
			}
			if err := vm.fuelN(int64(engine.state.BlockSize())); err != nil {
				return nil, err
			}
			state, err := engine.state.Clone()
			if err != nil {
				return nil, newError("RuntimeError", err.Error())
			}
			return &hashEngine{state: state}, nil
		}), true
	case "digest", "hexdigest":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 0, 0); err != nil {
				return nil, err
			}
			if err := vm.fuelN(int64(2 * engine.state.BlockSize())); err != nil {
				return nil, err
			}
			// Sum finalizes a copy, leaving subsequent updates and copies valid.
			if name == "digest" {
				return engine.state.Sum(make([]byte, 0, engine.state.Size())), nil
			}
			var scratch [sha512.Size]byte
			return hex.EncodeToString(engine.state.Sum(scratch[:0])), nil
		}), true
	}
	return nil, false
}
