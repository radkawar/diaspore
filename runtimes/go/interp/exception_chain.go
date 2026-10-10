package interp

func exceptionContext(exception *Instance) *Instance {
	if exception == nil {
		return nil
	}
	context, _ := exception.dictValue("__context__").(*Instance)
	return context
}

// chainException runs before a handler's exception stack unwinds. A Raised is
// chained only at its origin, not again while propagating through outer frames.
func (vm *VM) chainException(err error) {
	raised, ok := err.(*Raised)
	if !ok || raised.contextSet {
		return
	}
	raised.contextSet = true
	if len(vm.excStack) == 0 {
		return
	}
	exception, ok := raised.Exc.(*Instance)
	if !ok {
		return
	}
	context, ok := vm.excStack[len(vm.excStack)-1].(*Instance)
	if !ok || context == exception {
		return
	}
	// Break a prospective cycle, including reraising an earlier exception.
	// Floyd's cursor also bounds traversal of explicitly assigned cycles.
	slow, fast := context, context
	for current := context; current != nil; current = exceptionContext(current) {
		if exceptionContext(current) == exception {
			current.dictSet("__context__", None)
			break
		}
		slow = exceptionContext(slow)
		fast = exceptionContext(exceptionContext(fast))
		if slow != nil && slow == fast {
			break
		}
	}
	exception.dictSet("__context__", context)
}
