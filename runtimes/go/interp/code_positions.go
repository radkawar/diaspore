package interp

// codePositions maps the execution compiler's statement addresses to IR lines.
// Stripped bundles retain the address count without allocating a line table.
// Addresses use two-byte units for Python traceback's co_positions protocol;
// they do not describe CPython bytecode or invented source column ranges.
type codePositions struct {
	count int
	lines []int
}

func (positions *codePositions) add(line int) int {
	index := positions.count
	positions.count++
	if line > 0 {
		positions.lines = append(positions.lines, make([]int, index+1-len(positions.lines))...)
		positions.lines[index] = line
	}
	return index
}

type codePositionIter struct {
	positions codePositions
	index     int
}

func (iterator *codePositionIter) KindName() string { return "positions_iterator" }

func (iterator *codePositionIter) Next() (Value, bool, error) {
	if iterator.index >= iterator.positions.count {
		return nil, false, nil
	}
	var line Value = None
	if iterator.index < len(iterator.positions.lines) && iterator.positions.lines[iterator.index] > 0 {
		line = mkInt(int64(iterator.positions.lines[iterator.index]))
	}
	iterator.index++
	return &Tuple{Items: []Value{line, None, None, None}}, true, nil
}
