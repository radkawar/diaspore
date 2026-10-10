package interp

func (vm *VM) makeOperatorModule() *NativeModule {
	return &NativeModule{Name: "_operator", NS: map[string]Value{
		"add": bi("add", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("add", args, 2, 2); err != nil {
				return nil, err
			}
			if err := noKw("add", kw); err != nil {
				return nil, err
			}
			return vm.binop("add", args[0], args[1])
		}),
		"index": bi("index", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("index", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("index", kw); err != nil {
				return nil, err
			}
			value, err := percentIndex(vm, args[0])
			if err != nil {
				return nil, err
			}
			if boolean, ok := value.(bool); ok {
				if boolean {
					return int64(1), nil
				}
				return int64(0), nil
			}
			return value, nil
		}),
	}}
}
