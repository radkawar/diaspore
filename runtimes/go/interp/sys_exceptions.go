package interp

import (
	"fmt"
	"strings"
)

func biExceptHook(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("excepthook", args, 3, 3); err != nil {
		return nil, err
	}
	if err := noKw("excepthook", kw); err != nil {
		return nil, err
	}
	return None, vm.writeSysException(args[1], args[2], "")
}

func (vm *VM) writeSysException(value, traceback Value, prefix string) error {
	var text strings.Builder
	text.WriteString(prefix)
	instance, isException := value.(*Instance)
	if !isException || !instance.Class.IsSub(baseExc) {
		fmt.Fprintf(&text, "TypeError: print_exception(): Exception expected for value, %s found\n", typeName(value))
	} else {
		tb, _ := instance.dictValue("__traceback__").(*tracebackObject)
		if tb == nil {
			tb, _ = traceback.(*tracebackObject)
			if tb != nil {
				instance.dictSet("__traceback__", tb)
			}
		}
		if tb != nil {
			text.WriteString("Traceback (most recent call last):\n")
		}
		for ; tb != nil; tb = tb.next {
			code := vm.frameCode(tb.frame)
			fmt.Fprintf(&text, "  File %q, line %d, in %s\n", code.filename, tb.line, code.name)
			if frame := tb.frame.frame; frame != nil {
				meta := vm.modMeta[frame.moduleName]
				if tb.line > 0 && tb.line <= len(meta.src) {
					fmt.Fprintf(&text, "    %s\n", strings.TrimSpace(meta.src[tb.line-1]))
				}
			}
		}
		text.WriteString(vm.exceptionDisplayName(instance.Class))
		message, err := vm.str(instance)
		if err != nil {
			text.WriteString(": <exception str() failed>")
		} else if message != "" {
			text.WriteString(": ")
			text.WriteString(message)
		}
		text.WriteByte('\n')
	}
	stream, present := vm.natives["sys"].NS["stderr"]
	if !present {
		return newError("RuntimeError", "lost sys.stderr")
	}
	if _, none := stream.(NoneType); none {
		return nil
	}
	write, err := vm.getAttr(stream, "write")
	if err != nil {
		return err
	}
	_, err = vm.call(write, []Value{text.String()}, nil)
	return err
}
