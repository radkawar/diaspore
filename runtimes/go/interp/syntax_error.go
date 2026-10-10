package interp

import (
	"math/big"
	"strconv"
	"strings"
)

var syntaxErrorClass *Class

var syntaxErrorMembers = [...]string{
	"msg", "filename", "lineno", "offset", "text", "end_lineno", "end_offset", "print_file_and_line",
}

func initializeSyntaxErrorClasses(classes map[string]*Class) {
	syntaxErrorClass = classes["SyntaxError"]
	for _, name := range []string{"SyntaxError", "IndentationError", "TabError"} {
		classes[name].layout = &instanceLayout{hasDict: true, slotCount: len(syntaxErrorMembers), solid: syntaxErrorClass}
	}
	for index, name := range syntaxErrorMembers {
		doc := "exception " + name
		if name == "end_lineno" {
			doc = "exception end lineno"
		}
		if name == "end_offset" {
			doc = "exception end offset"
		}
		syntaxErrorClass.NS[name] = &metadataDescriptor{
			owner: syntaxErrorClass, name: name, kind: "member_descriptor", getter: true, writable: true,
			storage: slotNullableMember, slotIndex: index, doc: doc,
		}
	}
	syntaxErrorClass.NS["__init__"] = bi("__init__", syntaxErrorInit)
	syntaxErrorClass.NS["__str__"] = bi("__str__", syntaxErrorStr)
}

func syntaxErrorField(instance *Instance, index int) Value {
	if index < len(instance.slotValues) && instance.slotValues[index] != nil {
		return instance.slotValues[index]
	}
	return None
}

func setSyntaxErrorField(instance *Instance, index int, value Value) {
	if instance.slotValues == nil {
		instance.slotValues = make([]Value, instanceSlotCount(instance.Class))
	}
	instance.slotValues[index] = value
}

func syntaxErrorReceiver(name string, args []Value, kw []KwArg) (*Instance, error) {
	if err := argCount(name, args, 1, -1); err != nil {
		return nil, err
	}
	instance, ok := args[0].(*Instance)
	if !ok || !instance.Class.IsSub(syntaxErrorClass) {
		return nil, newErrorf("TypeError", "descriptor '%s' requires a 'SyntaxError' object but received a '%s'", name, typeName(args[0]))
	}
	if err := noKw(instance.Class.Name, kw); err != nil {
		return nil, err
	}
	return instance, nil
}

func syntaxErrorInit(vm *VM, args []Value, kw []KwArg) (Value, error) {
	instance, err := syntaxErrorReceiver("__init__", args, kw)
	if err != nil {
		return nil, err
	}
	arguments := args[1:]
	instance.dictSet("args", &Tuple{Items: append([]Value(nil), arguments...)})
	if len(arguments) != 0 {
		setSyntaxErrorField(instance, 0, arguments[0])
	}
	if len(arguments) != 2 {
		return None, nil
	}
	var details []Value
	switch value := arguments[1].(type) {
	case *Tuple:
		details = value.Items
	case *List:
		details = value.Items
	default:
		details, err = vm.iterToSlice(value)
		if err != nil {
			return nil, err
		}
	}
	// Conversion must finish before resetting the optional end positions.
	// Invalid arity still commits args/msg and the reset, as CPython does.
	setSyntaxErrorField(instance, 5, nil)
	setSyntaxErrorField(instance, 6, nil)
	if len(details) < 4 {
		return nil, newErrorf("TypeError", "function takes at least 4 arguments (%d given)", len(details))
	}
	if len(details) > 6 {
		return nil, newErrorf("TypeError", "function takes at most 6 arguments (%d given)", len(details))
	}
	for index, value := range details {
		setSyntaxErrorField(instance, index+1, value)
	}
	if len(details) == 5 {
		return nil, newError("TypeError", "end_offset must be provided when end_lineno is provided")
	}
	return None, nil
}

func syntaxErrorStr(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("__str__", args, 1, 1); err != nil {
		return nil, err
	}
	instance, err := syntaxErrorReceiver("__str__", args, kw)
	if err != nil {
		return nil, err
	}
	filename, haveFilename := rawPrim(syntaxErrorField(instance, 1)).(string)
	if haveFilename {
		filename = filename[strings.LastIndexByte(filename, '/')+1:]
	}
	line, haveLine := int64(0), false
	switch value := syntaxErrorField(instance, 2).(type) {
	case int64:
		line, haveLine = value, true
	case *big.Int:
		line, haveLine = -1, true
		if value.IsInt64() {
			line = value.Int64()
		}
	}
	message, err := vm.str(syntaxErrorField(instance, 0))
	if err != nil {
		return nil, err
	}
	if haveFilename && haveLine {
		return message + " (" + filename + ", line " + strconv.FormatInt(line, 10) + ")", nil
	}
	if haveFilename {
		return message + " (" + filename + ")", nil
	}
	if haveLine {
		return message + " (line " + strconv.FormatInt(line, 10) + ")", nil
	}
	return message, nil
}
