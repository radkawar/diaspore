package interp

import (
	"io"
	"strings"
)

func (vm *VM) makeTextStream(name string) *NativeObject {
	methods := map[string]*Builtin{}
	if name == "stdin" {
		for _, operation := range []string{"read", "readline"} {
			operation := operation
			methods[operation] = vm.journaled("sys", name+"."+operation, bi(operation,
				func(vm *VM, args []Value, kw []KwArg) (Value, error) {
					if err := argCount(operation, args, 0, 1); err != nil {
						return nil, err
					}
					if err := noKw(operation, kw); err != nil {
						return nil, err
					}
					size := int64(-1)
					if len(args) != 0 {
						_, none := args[0].(NoneType)
						if !none || operation != "read" {
							var err error
							size, err = wantInt("size", args[0])
							if err != nil {
								return nil, err
							}
						}
					}
					if size < 0 && operation == "read" {
						data, err := io.ReadAll(vm.stdin)
						if err != nil {
							return nil, newError("OSError", err.Error())
						}
						return string(data), nil
					}
					var text strings.Builder
					for count := int64(0); size < 0 || count < size; count++ {
						r, _, err := vm.stdin.ReadRune()
						if err == io.EOF {
							break
						}
						if err != nil {
							return nil, newError("OSError", err.Error())
						}
						text.WriteRune(r)
						if operation == "readline" && r == '\n' {
							break
						}
					}
					return text.String(), nil
				}))
		}
	} else {
		writer := func(vm *VM) io.Writer {
			if name == "stderr" {
				return vm.stderr
			}
			return vm.stdout
		}
		methods["write"] = bi("write", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("write", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("write", kw); err != nil {
				return nil, err
			}
			text, ok := rawPrim(args[0]).(string)
			if !ok {
				return nil, newErrorf("TypeError", "write() argument must be str, not %s", typeName(args[0]))
			}
			if !vm.replaying {
				written, err := io.WriteString(writer(vm), text)
				if err == nil && written != len(text) {
					err = io.ErrShortWrite
				}
				if err != nil {
					return nil, newError("OSError", err.Error())
				}
			}
			return int64(pyRuneCount(text)), nil
		})
		methods["flush"] = bi("flush", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("flush", args, 0, 0); err != nil {
				return nil, err
			}
			if err := noKw("flush", kw); err != nil {
				return nil, err
			}
			if flusher, ok := writer(vm).(interface{ Flush() error }); ok && !vm.replaying {
				if err := flusher.Flush(); err != nil {
					return nil, newError("OSError", err.Error())
				}
			}
			return None, nil
		})
	}
	return &NativeObject{Kind: "TextIOWrapper", methods: methods}
}

func biGetRecursionLimit(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("getrecursionlimit", args, 0, 0); err != nil {
		return nil, err
	}
	return int64(vm.maxDepth), noKw("getrecursionlimit", kw)
}

func biSetRecursionLimit(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("setrecursionlimit", args, 1, 1); err != nil {
		return nil, err
	}
	if err := noKw("setrecursionlimit", kw); err != nil {
		return nil, err
	}
	argument := rawPrim(args[0])
	limit, integer := asInt(argument)
	if !integer {
		n, integer := asBig(argument)
		if !integer {
			return nil, newErrorf("TypeError", "'%s' object cannot be interpreted as an integer", typeName(args[0]))
		}
		if n.Sign() <= 0 {
			limit = 0
		} else if n.IsInt64() {
			limit = n.Int64()
		} else {
			limit = int64(vm.depthCap)
		}
	}
	if limit < 1 {
		return nil, newError("ValueError", "recursion limit must be greater or equal than 1")
	}
	if limit > int64(vm.depthCap) {
		limit = int64(vm.depthCap)
	}
	if limit <= int64(vm.depth) {
		return nil, newErrorf("RecursionError",
			"cannot set the recursion limit to %d at the recursion depth %d: the limit is too low", limit, vm.depth)
	}
	vm.maxDepth = int(limit)
	return None, nil
}

func biUnraisableHook(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("unraisablehook", args, 1, 1); err != nil {
		return nil, err
	}
	if err := noKw("unraisablehook", kw); err != nil {
		return nil, err
	}
	report := args[0]
	exception, err := vm.getAttr(report, "exc_value")
	if err != nil {
		return nil, err
	}
	message, err := vm.getAttr(report, "err_msg")
	if err != nil {
		return nil, err
	}
	object, err := vm.getAttr(report, "object")
	if err != nil {
		return nil, err
	}
	prefix := "Exception ignored in"
	if _, none := message.(NoneType); !none {
		prefix, err = vm.str(message)
		if err != nil {
			return nil, err
		}
	}
	repr, err := vm.repr(object)
	if err != nil {
		return nil, err
	}
	traceback, err := vm.getAttr(report, "exc_traceback")
	if err != nil {
		return nil, err
	}
	return None, vm.writeSysException(exception, traceback, prefix+": "+repr+"\n")
}
