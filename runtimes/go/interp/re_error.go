package interp

import (
	"fmt"
	"regexp/syntax"
	"strings"
)

// reDiagnostic is internal to pattern and replacement compilation. Only these
// diagnostics become re.error; exceptions raised by replacement callbacks keep
// their original identity and class.
type reDiagnostic struct {
	message  string
	position int
	bare     bool
}

func (e *reDiagnostic) Error() string { return e.message }

func reFailure(message string) error {
	return &reDiagnostic{message: message, position: -1}
}

func reBareFailure(message string) error {
	return &reDiagnostic{message: message, position: -1, bare: true}
}

func reErrorAt(message string, position int) error {
	return &reDiagnostic{message: message, position: position}
}

func (t *reTranslator) errorAt(message string, bytePosition int) error {
	if bytePosition < 0 {
		return reFailure(message)
	}
	if bytePosition > len(t.s) {
		bytePosition = len(t.s)
	}
	return reErrorAt(message, pyRuneCount(t.s[:bytePosition]))
}

func (t *reTranslator) groupError(err error, opening int) error {
	if diagnostic, ok := err.(*reDiagnostic); ok && diagnostic.position < 0 && diagnostic.message == "missing ), unterminated subpattern" {
		return t.errorAt(diagnostic.message, opening)
	}
	return err
}

func makeReErrorClass() *Class {
	parent := excClasses["Exception"]
	class := &Class{Name: "error", Bases: []*Class{parent}, NS: map[string]Value{
		"__module__": "re", "__qualname__": "error",
	}}
	class.MRO = append([]*Class{class}, parent.MRO...)
	class.NS["__init__"] = bi("__init__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		values, err := reArgs("error", args, kw, []string{"self", "msg", "pattern", "pos"}, 2, []Value{None, None})
		if err != nil {
			return nil, err
		}
		self, ok := values[0].(*Instance)
		if !ok {
			return nil, newError("TypeError", "re.error.__init__ requires an exception instance")
		}
		if err := reInitError(vm, self, values[1], values[2], values[3]); err != nil {
			return nil, err
		}
		return None, nil
	})
	return class
}

func reInitError(vm *VM, instance *Instance, message, pattern, position Value) error {
	instance.dictSet("msg", message)
	instance.dictSet("pattern", pattern)
	instance.dictSet("pos", position)
	var line, column Value = None, None
	formatted := message
	if !reIsNone(pattern) && !reIsNone(position) {
		text, _, err := reText(pattern)
		if err != nil {
			return err
		}
		pos, err := reInteger(position, "pos")
		if err != nil {
			return err
		}
		stop := pos
		if stop < 0 {
			stop += int64(len(text))
		}
		if stop < 0 {
			stop = 0
		}
		if stop > int64(len(text)) {
			stop = int64(len(text))
		}
		lineNumber, lastNewline := int64(1), int64(-1)
		hasNewline := false
		for index, ch := range text {
			if ch == '\n' {
				hasNewline = true
				if int64(index) < stop {
					lineNumber++
					lastNewline = int64(index)
				}
			}
		}
		columnNumber := pos - lastNewline
		line, column = lineNumber, columnNumber
		msg, ok := message.(string)
		if !ok {
			msg, err = vm.str(message)
			if err != nil {
				return err
			}
		}
		msg = fmt.Sprintf("%s at position %d", msg, pos)
		if hasNewline {
			msg += fmt.Sprintf(" (line %d, column %d)", lineNumber, columnNumber)
		}
		formatted = msg
	}
	instance.dictSet("lineno", line)
	instance.dictSet("colno", column)
	instance.dictSet("args", &Tuple{Items: []Value{formatted}})
	return nil
}

func reRaiseDiagnostic(err error, pattern Value, class *Class) error {
	diagnostic, ok := err.(*reDiagnostic)
	if !ok {
		return err
	}
	if diagnostic.bare {
		pattern = None
	}
	var position Value = None
	if diagnostic.position >= 0 {
		position = int64(diagnostic.position)
	}
	instance := newInstance(class, nil)
	if initErr := reInitError(nil, instance, diagnostic.message, pattern, position); initErr != nil {
		return initErr
	}
	return &Raised{Exc: instance}
}

func reSyntaxDiagnostic(err error) error {
	parsed, ok := err.(*syntax.Error)
	if !ok {
		return reFailure(err.Error())
	}
	message := string(parsed.Code)
	switch parsed.Code {
	case syntax.ErrInvalidCharRange:
		message = "bad character range " + parsed.Expr
	case syntax.ErrMissingRepeatArgument:
		message = "nothing to repeat"
	case syntax.ErrInvalidRepeatOp:
		message = "multiple repeat"
	case syntax.ErrInvalidRepeatSize:
		message = "invalid repetition count"
	case syntax.ErrInvalidNamedCapture:
		message = "bad character in group name"
	}
	return reFailure(strings.TrimSpace(message))
}

// classError reports range diagnostics against the original spelling, not the
// hexadecimal classes emitted for the Go parser.
func (t *reTranslator) classError(err error, opening int, flags int64) error {
	type token struct {
		start, end       int
		value            rune
		category, hyphen bool
	}
	scan := &reTranslator{s: t.s, i: opening + 1, bytes: t.bytes}
	if scan.i < len(scan.s) && scan.s[scan.i] == '^' {
		scan.i++
	}
	var tokens []token
	for scan.i < len(scan.s) {
		if scan.s[scan.i] == ']' && len(tokens) > 0 {
			break
		}
		item := token{start: scan.i}
		if scan.s[scan.i] == '\\' {
			item.category = scan.i+1 < len(scan.s) && strings.ContainsRune("dDwWsS", rune(scan.s[scan.i+1]))
			atom, escapeErr := scan.escape(flags, true)
			if escapeErr != nil {
				return escapeErr
			}
			if !item.category {
				parsed, parseErr := syntax.Parse(atom, syntax.Perl)
				if parseErr != nil {
					return reSyntaxDiagnostic(parseErr)
				}
				item.value = parsed.Rune[0]
			}
		} else {
			ch, size := pyDecodeRuneInString(scan.s[scan.i:])
			item.value, item.hyphen = ch, ch == '-'
			scan.i += size
		}
		item.end = scan.i
		tokens = append(tokens, item)
	}
	for i := 0; i < len(tokens); {
		if i+2 < len(tokens) && tokens[i+1].hyphen {
			left, right := tokens[i], tokens[i+2]
			if left.category || right.category || right.value < left.value {
				message := "bad character range " + t.s[left.start:left.end] + "-" + t.s[right.start:right.end]
				return t.errorAt(message, left.start)
			}
			i += 3
		} else {
			i++
		}
	}
	if diagnostic, ok := err.(*reDiagnostic); ok && diagnostic.position < 0 {
		return t.errorAt(diagnostic.message, opening)
	}
	return err
}
