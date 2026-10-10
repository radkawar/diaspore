package interp

import (
	"fmt"
	"regexp/syntax"
	"strconv"
	"strings"
	"unicode"
)

type reSpecial struct {
	kind     string
	ref      string
	flags    int64
	position int
}

type reSpecialNode struct {
	reSpecial
	node  *syntax.Regexp
	slot  int
	width int
}

type reSpecialInst struct {
	kind      string
	flags     int64
	slot      int
	width     int
	entry     uint32
	alternate uint32
}

func (t *reTranslator) openGroup(name string) int {
	if t.groupNames == nil {
		t.groupNames = make(map[string]int)
		t.closedGroups = make(map[int]bool)
	}
	t.groups++
	if name != "" {
		t.groupNames[name] = t.groups
	}
	return t.groups
}

func (t *reTranslator) extension(kind, ref, body string, flags int64, position int) string {
	name := fmt.Sprintf("%s%d", t.prefix, t.serial)
	t.serial++
	if t.specials == nil {
		t.specials = make(map[string]reSpecial)
	}
	t.specials[name] = reSpecial{kind: kind, ref: ref, flags: flags, position: position}
	return "(?P<" + name + ">" + body + ")"
}

func (t *reTranslator) reference(ref string, flags int64, position, openPosition int) (string, error) {
	group, err := strconv.Atoi(ref)
	if err != nil {
		var exists bool
		group, exists = t.groupNames[ref]
		if !exists {
			return "", t.errorAt("unknown group name "+reprString(ref), position)
		}
	}
	if group <= 0 || group > t.groups {
		return "", t.errorAt("invalid group reference "+ref, position)
	}
	if !t.closedGroups[group] {
		return "", t.errorAt("cannot refer to an open group", openPosition)
	}
	if t.lookbehindFloor > 0 && group >= t.lookbehindFloor {
		return "", t.errorAt("cannot refer to group defined in the same lookbehind subpattern", t.i)
	}
	return t.extension("reference", strconv.Itoa(group), "", flags, pyRuneCount(t.s[:position])), nil
}

// specialGroup consumes extensions after the opening '(?'. Their bodies remain
// in the syntax tree, so ordinary and named captures retain source ordering.
func (t *reTranslator) specialGroup(flags int64) (string, bool, error) {
	rest := t.s[t.i:]
	if strings.HasPrefix(rest, "P=") {
		end := strings.IndexByte(rest, ')')
		if end < 0 {
			return "", true, t.errorAt("missing ), unterminated name", t.i+2)
		}
		position := t.i + 2
		t.i += end + 1
		value, err := t.reference(rest[2:end], flags, position, position)
		return value, true, err
	}
	if strings.HasPrefix(rest, "(") {
		position := t.i + 1
		end := strings.IndexByte(rest, ')')
		if end < 0 {
			return "", true, t.errorAt("missing ), unterminated name", position)
		}
		ref := rest[1:end]
		if ref == "" {
			return "", true, t.errorAt("missing group name", position)
		}
		if _, err := strconv.Atoi(ref); err != nil {
			group, exists := t.groupNames[ref]
			if !exists {
				return "", true, t.errorAt("unknown group name "+reprString(ref), position)
			}
			ref = strconv.Itoa(group)
		}
		t.i += end + 1
		yes, err := t.translatePart(flags, true, true)
		if err != nil {
			return "", true, err
		}
		no := ""
		if t.stoppedAtPipe {
			t.i++
			no, err = t.translatePart(flags, true, true)
			if err != nil {
				return "", true, err
			}
			if t.stoppedAtPipe {
				return "", true, t.errorAt("conditional backref with more than two branches", t.i)
			}
		}
		body := t.extension("branch", "", yes, flags, -1) + "|" + t.extension("branch", "", no, flags, -1)
		return t.extension("conditional", ref, body, flags, pyRuneCount(t.s[:position])), true, nil
	}
	kind, prefix := "", ""
	for _, candidate := range []struct{ prefix, kind string }{
		{"<=", "behind"}, {"<!", "notbehind"}, {"=", "ahead"}, {"!", "notahead"}, {">", "atomic"},
	} {
		if strings.HasPrefix(rest, candidate.prefix) {
			prefix, kind = candidate.prefix, candidate.kind
			break
		}
	}
	if kind == "" {
		return "", false, nil
	}
	t.i += len(prefix)
	oldFloor := t.lookbehindFloor
	if kind == "behind" || kind == "notbehind" {
		t.lookbehindFloor = t.groups + 1
	}
	body, err := t.translate(flags, true)
	t.lookbehindFloor = oldFloor
	if err != nil {
		return "", true, err
	}
	return t.extension(kind, "", body, flags, -1), true, nil
}

func (c *compiledRe) prepareSpecials(parsed *syntax.Regexp, descriptions map[string]reSpecial) (map[int]*reSpecialNode, error) {
	nodes := make(map[int]*syntax.Regexp)
	var collect func(*syntax.Regexp)
	collect = func(node *syntax.Regexp) {
		if node.Op == syntax.OpCapture {
			nodes[node.Cap] = node
		}
		for _, child := range node.Sub {
			collect(child)
		}
	}
	collect(parsed)
	specials := make(map[int]*reSpecialNode)
	for slot, node := range nodes {
		description, ok := descriptions[node.Name]
		if !ok {
			continue
		}
		special := &reSpecialNode{reSpecial: description, node: node.Sub[0]}
		if description.ref != "" {
			group, err := strconv.Atoi(description.ref)
			if err != nil || group <= 0 || group >= len(c.slots) {
				return nil, reErrorAt("invalid group reference "+description.ref, description.position)
			}
			special.slot = c.slots[group]
		}
		specials[slot] = special
	}
	var width func(*syntax.Regexp) int
	active := make(map[int]bool)
	width = func(node *syntax.Regexp) int {
		switch node.Op {
		case syntax.OpEmptyMatch, syntax.OpBeginLine, syntax.OpEndLine, syntax.OpBeginText, syntax.OpEndText, syntax.OpWordBoundary, syntax.OpNoWordBoundary:
			return 0
		case syntax.OpLiteral:
			return len(node.Rune)
		case syntax.OpCharClass, syntax.OpAnyChar, syntax.OpAnyCharNotNL:
			return 1
		case syntax.OpCapture:
			if _, ok := c.assertions[node.Cap]; ok {
				return 0
			}
			if special, ok := specials[node.Cap]; ok {
				switch special.kind {
				case "ahead", "notahead", "behind", "notbehind":
					return 0
				case "reference":
					if active[special.slot] {
						return -1
					}
					active[special.slot] = true
					result := width(nodes[special.slot].Sub[0])
					delete(active, special.slot)
					return result
				}
			}
			return width(node.Sub[0])
		case syntax.OpConcat:
			total := 0
			for _, child := range node.Sub {
				part := width(child)
				if part < 0 || part > int(^uint(0)>>1)-total {
					return -1
				}
				total += part
			}
			return total
		case syntax.OpAlternate:
			result := width(node.Sub[0])
			for _, child := range node.Sub[1:] {
				if width(child) != result {
					return -1
				}
			}
			return result
		case syntax.OpRepeat:
			if node.Min == node.Max {
				part := width(node.Sub[0])
				if node.Min == 0 {
					return 0
				}
				if part >= 0 && part <= int(^uint(0)>>1)/node.Min {
					return part * node.Min
				}
			}
		case syntax.OpStar, syntax.OpPlus, syntax.OpQuest:
			if width(node.Sub[0]) == 0 {
				return 0
			}
		}
		return -1
	}
	for _, special := range specials {
		if special.kind == "behind" || special.kind == "notbehind" {
			special.width = width(special.node)
			if special.width < 0 {
				return nil, reBareFailure("look-behind requires fixed-width pattern")
			}
		}
	}
	return specials, nil
}

func reFoldEqual(a, b rune, flags int64) bool {
	if a == b {
		return true
	}
	if flags&reIgnoreCase == 0 {
		return false
	}
	if flags&reASCII != 0 {
		if a >= 'A' && a <= 'Z' {
			a += 'a' - 'A'
		}
		if b >= 'A' && b <= 'Z' {
			b += 'a' - 'A'
		}
		return a == b
	}
	// GROUPREF_UNI_IGNORE compares simple lowercase, unlike literal classes,
	// which additionally use Python's case-equivalence closure.
	return unicode.ToLower(a) == unicode.ToLower(b)
}
