package interp

import (
	"strings"
	"unicode/utf8"
)

// Audit hooks are interpreter-local and iterated live, as in CPython 3.12's
// Python/sysmodule.c. Registering a hook during an event also visits it for that
// event; errors stop dispatch, and registration suppresses Exception subclasses.
func (vm *VM) auditEvent(event string, args []Value) error {
	if len(vm.auditHooks) == 0 {
		return nil
	}
	arguments := [2]Value{event, &Tuple{Items: append([]Value(nil), args...)}}
	// A range would freeze the length and omit newly registered hooks.
	for index := 0; index < len(vm.auditHooks); index++ {
		hook := vm.auditHooks[index]
		canTrace, err := vm.getAttr(hook, "__cantrace__")
		if err == nil {
			// Python tracing is not exposed by this runtime, but the attribute's
			// lookup and truth-conversion callbacks are part of hook dispatch.
			_, err = vm.truth(canTrace)
		} else if _, missing := raisedAs(err, "AttributeError"); missing {
			err = nil
		}
		if err != nil {
			return err
		}
		if _, err := vm.call(hook, arguments[:], nil); err != nil {
			return err
		}
	}
	return nil
}

func biAudit(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("sys.audit", kw); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return nil, newError("TypeError", "audit() missing 1 required positional argument: 'event'")
	}
	// CPython deliberately avoids even event validation without active hooks.
	if len(vm.auditHooks) == 0 {
		return None, nil
	}
	event, ok := rawPrim(args[0]).(string)
	if !ok {
		return nil, newErrorf("TypeError", "expected str for argument 'event', not %s", typeName(args[0]))
	}
	if !utf8.ValidString(event) {
		if _, err := encodeStr(event, "utf-8"); err != nil {
			return nil, err
		}
	}
	if nul := strings.IndexByte(event, 0); nul >= 0 {
		event = event[:nul]
	}
	if err := vm.auditEvent(event, args[1:]); err != nil {
		return nil, err
	}
	return None, nil
}

func biAddAuditHook(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if count := len(args) + len(kw); count > 1 {
		return nil, newErrorf("TypeError", "addaudithook() takes at most 1 argument (%d given)", count)
	}
	var hook Value
	if len(args) != 0 {
		hook = args[0]
	} else if len(kw) == 1 && kw[0].Name == "hook" {
		hook = kw[0].Val
	} else {
		return nil, newError("TypeError", "addaudithook() missing required argument 'hook' (pos 1)")
	}
	if err := vm.auditEvent("sys.addaudithook", nil); err != nil {
		if _, suppressed := raisedAs(err, "Exception"); suppressed {
			return None, nil
		}
		return nil, err
	}
	// Callable validation occurs when the hook is called, not on registration.
	vm.auditHooks = append(vm.auditHooks, hook)
	return None, nil
}
