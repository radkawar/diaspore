package interp

// exceptionDisplayName follows CPython's terminal traceback naming: the module
// is an attribute lookup, but the qualified name belongs to the actual type.
func (vm *VM) exceptionDisplayName(cls *Class) string {
	qualname := cls.Name
	if value, present := cls.NS["__qualname__"]; present {
		var ok bool
		qualname, ok = rawPrim(value).(string)
		if !ok {
			qualname = "<unknown>"
		}
	}
	value, err := vm.getAttr(cls, "__module__")
	module, ok := rawPrim(value).(string)
	if err != nil || !ok {
		return "<unknown>." + qualname
	}
	if module == "builtins" || module == "__main__" {
		return qualname
	}
	return module + "." + qualname
}
