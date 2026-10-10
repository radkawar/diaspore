package interp

import "strings"

// importStar reads exports only after module execution, including cyclic imports.
func (vm *VM) importStar(fr *frame, mod Value) error {
	bind := func(name string) error {
		v, err := vm.getAttr(mod, name)
		if err != nil {
			return err
		}
		if fr.cells != nil {
			c := fr.cells[name]
			if c == nil {
				c = &cell{}
				fr.cells[name] = c
			}
			c.v = v
		} else if fr.vars != nil {
			if _, exists := fr.vars[name]; !exists {
				fr.varOrder = append(fr.varOrder, name)
			}
			fr.vars[name] = v
		} else if index, exists := fr.layout[name]; exists {
			fr.slots[index] = v
		} else {
			return newError("SyntaxError", "import * only allowed at module level")
		}
		return nil
	}
	all, err := vm.getAttr(mod, "__all__")
	if err == nil {
		for i := int64(0); ; i++ {
			name, err := vm.getIndex(all, i)
			if err != nil {
				if _, stop := raisedAs(err, "IndexError"); stop {
					return nil
				}
				return err
			}
			s, ok := name.(string)
			if !ok {
				return newErrorf("TypeError", "Item in __all__ must be str, not %s", typeName(name))
			}
			if err := bind(s); err != nil {
				return err
			}
		}
	}
	if _, absent := raisedAs(err, "AttributeError"); !absent {
		return err
	}
	switch m := mod.(type) {
	case *Module:
		for name, c := range m.frame.cells {
			if c.v != nil && !strings.HasPrefix(name, "_") && !strings.HasPrefix(name, ".") {
				if err := bind(name); err != nil {
					return err
				}
			}
		}
	case *NativeModule:
		for name := range m.NS {
			if !strings.HasPrefix(name, "_") && !strings.HasPrefix(name, ".") {
				if err := bind(name); err != nil {
					return err
				}
			}
		}
	}
	return nil
}
