package interp

import (
	"hash/fnv"
	"math"
	"math/big"
	"reflect"
	"strconv"
)

func hashInteger(v Value) (int64, error) {
	v = rawPrim(v)
	if n, ok := asInt(v); ok {
		if n == -1 {
			n = -2
		}
		return n, nil
	}
	if n, ok := v.(*big.Int); ok {
		if n.IsInt64() {
			return hashInteger(n.Int64())
		}
		modulus := big.NewInt((1 << 61) - 1)
		r := new(big.Int).Rem(n, modulus).Int64()
		if r == -1 {
			r = -2
		}
		return r, nil
	}
	return 0, newError("TypeError", "__hash__ method should return an integer")
}

func instanceHash(inst *Instance) (int64, error) {
	for _, owner := range inst.Class.MRO {
		hook, ok := owner.NS["__hash__"]
		if !ok {
			continue
		}
		if _, disabled := hook.(NoneType); disabled {
			return 0, newErrorf("TypeError", "unhashable type: '%s'", inst.Class.Name)
		}
		if hook == objectHash {
			if owner == objectClass && inst.Prim != nil {
				return hashValue(inst.Prim)
			}
			return int64(reflect.ValueOf(inst).Pointer()) &^ (1 << 62), nil
		}
		vm := inst.Class.vm
		result, err := vm.call(vm.bindAttr(hook, inst, inst.Class), nil, nil)
		if err != nil {
			return 0, err
		}
		return hashInteger(result)
	}
	if inst.Prim != nil {
		return hashValue(inst.Prim)
	}
	return int64(reflect.ValueOf(inst).Pointer()) &^ (1 << 62), nil
}

func hashValue(v Value) (int64, error) {
	if inst, ok := v.(*Instance); ok {
		return instanceHash(inst)
	}
	hk, err := hashKey(v)
	if err != nil {
		return 0, err
	}
	return encodedHash(v, hk), nil
}

// encodedHash never invokes user code: stored keys retain their insertion hash.
func encodedHash(v Value, hk string) int64 {
	if len(hk) != 0 && hk[0] == 'h' {
		n, _ := strconv.ParseInt(hk[1:], 10, 64)
		return n
	}
	if isIntKind(v) {
		return pythonIntegerHash(v)
	}
	if f, ok := v.(float64); ok && !math.IsNaN(f) {
		return pythonFloatHash(f)
	}
	if c, ok := v.(complex128); ok && !math.IsNaN(real(c)) && !math.IsNaN(imag(c)) {
		hash := int64(uint64(pythonFloatHash(real(c))) + 1000003*uint64(pythonFloatHash(imag(c))))
		if hash == -1 {
			hash = -2
		}
		return hash
	}
	h := fnv.New64a()
	_, _ = h.Write([]byte(hk))
	return int64(h.Sum64() &^ (1 << 63))
}

func compoundHash(items []Value) (int64, error) {
	// CPython's 64-bit tuple hash combines the element hashes, not their identities.
	acc := uint64(2870177450012600261)
	for _, item := range items {
		h, err := hashValue(item)
		if err != nil {
			return 0, err
		}
		acc += uint64(h) * 14029467366897019727
		acc = (acc << 31) | (acc >> 33)
		acc *= 11400714785074694791
	}
	acc += uint64(len(items)) ^ (uint64(2870177450012600261) ^ 3527539)
	if acc == ^uint64(0) {
		return 1546275796, nil
	}
	return int64(acc), nil
}

func keyVM(v Value) *VM {
	switch t := v.(type) {
	case *GenericAlias:
		if vm := keyVM(t.Origin); vm != nil {
			return vm
		}
		return keyVM(t.Args)
	case *UnionType:
		return keyVM(t.Args)
	case *Instance:
		return t.Class.vm
	case *Tuple:
		for _, item := range t.Items {
			if vm := keyVM(item); vm != nil {
				return vm
			}
		}
	case *Slice:
		if vm := keyVM(t.Lower); vm != nil {
			return vm
		}
		if vm := keyVM(t.Upper); vm != nil {
			return vm
		}
		return keyVM(t.Step)
	}
	return nil
}

func findHashed(entries []dictEntry, key Value, hk string) (int, bool, error) {
	hash := encodedHash(key, hk)
	for i := range entries {
		e := &entries[i]
		ehk := e.hk
		if e.str {
			ehk = "s" + ehk
		}
		if encodedHash(e.key, ehk) != hash {
			continue
		}
		vm := keyVM(e.key)
		if vm == nil {
			vm = keyVM(key)
		}
		// Primitive equality needs no VM state; user keys carry their owning VM.
		equal, err := vm.eqElem(e.key, key)
		if err != nil {
			return 0, false, err
		}
		if equal {
			return i, true, nil
		}
	}
	return 0, false, nil
}
