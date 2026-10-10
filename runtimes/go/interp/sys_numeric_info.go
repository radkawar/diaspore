package interp

import "math/bits"

// Integer limbs are math/big.Word values, not CPython's packed 30-bit digits.
// Decimal conversions currently have no digit cap or configurable threshold.
var intInfoFields = [...]sysInfoField{
	{"bits_per_digit", "size of a digit in bits", int64(bits.UintSize)},
	{"sizeof_digit", "size in bytes of the C type used to represent a digit", int64(bits.UintSize / 8)},
	{"default_max_str_digits", "default decimal conversion limit; zero means unlimited", int64(0)},
	{"str_digits_check_threshold", "minimum configurable conversion limit; zero when no digit cap is implemented", int64(0)},
}

// Numeric hashes use CPython's 64-bit modular scheme. String/bytes hashes use
// unseeded FNV-1a over the runtime key encoding, with the sign bit cleared.
// The nan field is retained as the reserved CPython structseq field; NaN
// itself is hashed by the key encoder rather than this constant.
var hashInfoFields = [...]sysInfoField{
	{"width", "width of the type used for hashes in bits", int64(64)},
	{"modulus", "prime modulus used for numeric hash values", int64(numericHashModulus)},
	{"inf", "hash value returned for a positive infinity", int64(314159)},
	{"nan", "reserved; no longer used to hash NaN values", int64(0)},
	{"imag", "multiplier used for the imaginary part of a complex number", int64(1000003)},
	{"algorithm", "name of the algorithm for hashing str and bytes", "fnv1a_64"},
	{"hash_bits", "number of usable bits in string and bytes hashes", int64(63)},
	{"seed_bits", "size of the hash seed in bits", int64(0)},
	{"cutoff", "small string optimization cutoff in bytes", int64(0)},
}

func (vm *VM) makeIntInfo() *Instance {
	return vm.makeSystemInfo("int_info", "A named tuple holding information about the integer implementation.", intInfoFields[:])
}

func (vm *VM) makeHashInfo() *Instance {
	return vm.makeSystemInfo("hash_info", "A named tuple holding information about the hashing implementation.", hashInfoFields[:])
}
